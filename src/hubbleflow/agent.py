"""Assembles the DeepAgent that backs the harness."""

from __future__ import annotations

from dataclasses import dataclass

from deepagents import SubAgent, create_deep_agent
from deepagents.backends import CompositeBackend
from langchain.agents.middleware import SummarizationMiddleware, TodoListMiddleware
from langchain.agents.middleware.human_in_the_loop import InterruptOnConfig
from langchain.agents.middleware.types import ToolCallRequest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from hubbleflow import config as config_module
from hubbleflow.backend import ForgivingFilesystemBackend
from hubbleflow import mcp as mcp_module
from hubbleflow import models as models_module
from hubbleflow.cache import CacheStats, CachingChatGoogle, GeminiCache
from hubbleflow.config import GEMINI, MESH, NVIDIA, OLLAMA, Config
from hubbleflow.permissions import PermissionPolicy
from hubbleflow.profile import register_gemini_profile
from hubbleflow.tools.shell import ShellSession, make_shell_tool
from hubbleflow.tools.clock import make_clock_tools, system_prompt_line
from hubbleflow.tools.knowledge import make_knowledge_tools
from hubbleflow.tools.web import make_web_tools

SYSTEM_PROMPT = """\
You are Hubbleflow, a coding agent running in the user's terminal. You read and \
change real files and run real commands.

Two things share a filesystem but spell paths differently. The file tools -- \
read_file, write_file, edit_file, ls, glob, grep -- are rooted at the workspace, \
so the workspace itself is `/` and a file in it is `/src/main.py`. bash is not: \
it runs in the real directory and prints real paths like \
`/Users/you/project/src/main.py`. Give the file tools workspace paths. If you \
paste a real path from shell output into a file tool it will still work, but \
paths starting from `/` as the workspace root are the ones to reach for.

Work like a careful colleague who already knows this codebase's conventions. \
Match the style of the code around what you touch -- its naming, its comment \
density, its idioms -- rather than importing habits from elsewhere. When you \
add a dependency or call an API, check first that it is already used here.

Prefer the file tools over shelling out for anything they cover: read_file, \
write_file, edit_file, ls, glob and grep are faster and safer than cat, sed and \
find. Use bash for the things only a shell can do -- running tests, git, \
package managers, build steps.

The same goes for the internet: web_search answers questions about anything \
newer than your training data, and web_fetch reads a page. Reach for those \
rather than curl, wget, or a Python one-liner around urllib -- they are built \
for it, they cite their sources, and the user has approved them for this.

For work that spans several steps, keep a todo list and work through it. For a \
question that needs a wide search across many files, dispatch the explore \
subagent rather than reading everything into your own context.

Never commit, push, or run a destructive command unless the user asked for it.\
"""

EXPLORE_PROMPT = """\
You are a read-only research agent. Search the codebase and report back what \
you found.

Read excerpts, not whole files, and follow the trail across directories and \
naming conventions until you can answer confidently. You cannot edit anything, \
and you should not try.

Your reply is consumed by another agent, not shown to a person. Return the \
findings and the file:line references that support them. No preamble, no \
summary of your process -- just what is true and where it lives.\
"""

# Tools that touch the user's machine in a way they'd want to see first.
_GUARDED_TOOLS = ("bash", "execute", "write_file", "edit_file", "delete", "web_fetch")

# Compact the transcript before Gemini's window runs out, keeping recent turns.
# Global skills live outside the workspace, which a sandboxed backend won't
# read. Routing them at a virtual prefix keeps the sandbox and reaches them.
_SKILLS_ROUTE = "/skills/"
_PROJECT_SKILLS = "/.hubbleflow/skills/"
# Skills are how the agent works; a knowledge bundle is what it knows. Different
# authors, different lifetimes, so a route of its own rather than a skills
# subdirectory -- and the bundle usually lives outside the workspace anyway.
_KNOWLEDGE_ROUTE = "/knowledge/"
# The harness's own notes, kept apart from a generated bundle so neither tool
# rewrites the other's directory.
_RESEARCH_ROUTE = "/research/"

# The system prompt and tool declarations ride on every request; measured at
# about 5.3k, rounded up so a new tool doesn't silently eat the margin.
_PROMPT_OVERHEAD_TOKENS = 6_000
_KEEP_RECENT_MESSAGES = 24


def _project_context(config: Config) -> str:
    """The project's own instructions, appended to the system prompt.

    Every other harness reads a file like this and acts on it; without one, a
    repo's conventions have to be re-explained every session. It is also how a
    generated knowledge bundle announces itself -- OpenWiki writes a pointer
    block into AGENTS.md rather than expecting the agent to go looking.

    Read failures are ignored on purpose. A context file is a convenience, and
    an unreadable one should not be the reason a session refuses to start.
    """
    for name in config_module.context_files():
        try:
            text = (config.workspace / name).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            continue
        if not text:
            continue
        if len(text) > config_module.MAX_CONTEXT_CHARS:
            text = text[: config_module.MAX_CONTEXT_CHARS].rstrip() + "\n\n[...truncated]"
        return (
            f"\n\n# {name}\n\nThis project ships its own instructions. They describe "
            "conventions and constraints particular to this codebase, and they win "
            f"over your general habits wherever the two disagree.\n\n{text}"
        )
    return ""


def _summarize_after(config: Config) -> int:
    """The token count at which the transcript gets compacted.

    A hosted model has room to spare, so it compacts late and the number is a
    cost decision -- see `config.compact_after`. A local model runs in whatever window was asked for --
    32k by default -- so that number can never be reached: the request fails on
    context size long before the middleware would trip, and the session dies
    instead of compacting.

    The trigger has to clear the window by more than the transcript, because
    compacting *is* another model call: it resends everything, then writes a
    summary. So the budget is the window less what that call needs around the
    transcript -- the prompt it always carries, and the output it has to fit.
    A window too small to hold all three still gets a usable trigger rather
    than a negative one, and will simply compact often.
    """
    if not config.is_local:
        return config_module.compact_after(config.provider)
    window = config_module.context_window()
    room = window - _PROMPT_OVERHEAD_TOKENS - config_module.max_output_tokens()
    return max(room, window // 4)


@dataclass(slots=True)
class Harness:
    """A built agent plus the pieces the UI needs to talk about it."""

    graph: object
    shell: ShellSession
    policy: PermissionPolicy
    checkpointer: AsyncSqliteSaver
    tool_names: list[str]
    mcp: mcp_module.Servers
    cache: GeminiCache | None
    cache_stats: CacheStats


async def build(
    config: Config,
    checkpointer: AsyncSqliteSaver,
    policy: PermissionPolicy,
    model: str | object | None = None,
    servers: mcp_module.Servers | None = None,
) -> Harness:
    """Construct the DeepAgent for this session.

    `model` overrides `config.model`; the tests use it to drive the whole loop
    against a scripted model instead of the live API.
    """
    register_gemini_profile()
    model = model or config.model
    servers = servers if servers is not None else mcp_module.Servers()
    stats = CacheStats()
    cache: GeminiCache | None = None
    if model is config.model or model == config.model:
        model, cache = _build_model(config, stats)

    shell = ShellSession(config.workspace)
    bash = make_shell_tool(shell)

    backend, skill_sources = _backend(config)

    explore = SubAgent(
        name="explore",
        description=(
            "Read-only search agent. Dispatch it when answering means sweeping many "
            "files or directories and you only need the conclusion, not the file "
            "contents in your own context."
        ),
        system_prompt=EXPLORE_PROMPT,
        model=model,
    )

    graph = create_deep_agent(
        model=model,
        tools=[bash, *make_clock_tools(), *make_web_tools(), *make_knowledge_tools(config.knowledge, _KNOWLEDGE_ROUTE, config.research, _RESEARCH_ROUTE), *servers.tools],
        system_prompt=SYSTEM_PROMPT + system_prompt_line() + _project_context(config),
        middleware=[
            TodoListMiddleware(),
            SummarizationMiddleware(
                model=model,
                trigger=("tokens", _summarize_after(config)),
                keep=("messages", _KEEP_RECENT_MESSAGES),
            ),
        ],
        subagents=[explore],
        skills=skill_sources or None,
        backend=backend,
        interrupt_on=_interrupt_config(policy, servers),
        checkpointer=checkpointer,
        name="hubbleflow",
    )

    return Harness(
        graph=graph,
        shell=shell,
        policy=policy,
        checkpointer=checkpointer,
        tool_names=_tool_names(graph),
        mcp=servers,
        cache=cache,
        cache_stats=stats,
    )


def _build_model(config: Config, stats: CacheStats) -> tuple[object, GeminiCache | None]:
    """Decide how to construct the chat model for this session.

    A plain provider string is enough for most cases; three need an instance --
    Gemini when caching is on, NVIDIA because its client defaults to a 60s
    timeout and a 1024-token output cap, and Ollama because it otherwise sizes
    the context window itself, none of which survives real work.
    """
    if config.provider == NVIDIA:
        return _nvidia_model(config), None
    if config.provider == MESH:
        return _mesh_model(config), None
    if (server := models_module.local_server(config.provider)) is not None:
        return _local_server_model(config, server), None
    if config.provider == OLLAMA:
        return _ollama_model(config), None
    if config.cache and config.provider == GEMINI:
        key = config_module.api_key()
        cache = GeminiCache(config.model_name, key, stats)
        return CachingChatGoogle(model=config.model_name, google_api_key=key).attach(cache), cache
    return config.model, None


def _mesh_model(config: Config) -> object:
    """mesh-llm pools GPUs across machines behind one OpenAI-compatible API.

    There's no dedicated integration, and none is needed -- an OpenAI client
    aimed at the node's base URL is the whole thing. The key is required by the
    client and ignored by the mesh.
    """
    from langchain_openai import ChatOpenAI

    from hubbleflow.models import mesh_url

    return ChatOpenAI(
        model=config.model_name,
        base_url=mesh_url(),
        api_key="mesh-llm",
        timeout=config_module.request_timeout(),
        max_tokens=config_module.max_output_tokens(),
    )


def _local_server_model(config: Config, server) -> object:
    """vLLM, llama.cpp and FreeToken all speak the OpenAI protocol.

    Nothing here is specific to any of them -- the same client covers all three,
    and the only thing that varies is the base URL, which the server table
    already holds. The key is required by the client and ignored by every one
    of these servers.
    """
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=config.model_name,
        base_url=models_module.server_url(server),
        api_key=server.provider,
        timeout=config_module.request_timeout(),
        max_tokens=config_module.max_output_tokens(),
    )


def _ollama_model(config: Config) -> object:
    """Ollama guesses the context window unless the request names one.

    The guess is drawn from free VRAM and lands on 4096 for a large model --
    less than the system prompt and tool schemas spend before the user has
    typed anything, so the first turn fails with `exceed_context_size_error`.
    `num_ctx` travels as a per-request option, which keeps the sizing here
    rather than in whatever the server was last started with.
    """
    from langchain_ollama import ChatOllama

    from hubbleflow.models import ollama_host

    return ChatOllama(
        model=config.model_name,
        base_url=ollama_host(),
        num_ctx=config_module.context_window(),
        num_predict=config_module.max_output_tokens(),
    )


def _nvidia_model(config: Config) -> object:
    from langchain_nvidia_ai_endpoints import ChatNVIDIA

    return ChatNVIDIA(
        model=config.model_name,
        api_key=config_module.nvidia_api_key(),
        timeout=config_module.request_timeout(),
        max_tokens=config_module.max_output_tokens(),
    )


def _backend(config: Config) -> tuple[object, list[str]]:
    """The filesystem the agent sees, plus the skill paths inside it."""
    workspace = ForgivingFilesystemBackend(root_dir=config.workspace, virtual_mode=config.virtual_mode)

    sources: list[str] = []
    if config.project_skills:
        sources.append(_PROJECT_SKILLS)

    routes = {}
    if config.global_skills:
        routes[_SKILLS_ROUTE] = ForgivingFilesystemBackend(
            root_dir=config.global_skills, virtual_mode=config.virtual_mode
        )
        sources.append(_SKILLS_ROUTE)
    if config.knowledge:
        routes[_KNOWLEDGE_ROUTE] = ForgivingFilesystemBackend(
            root_dir=config.knowledge, virtual_mode=config.virtual_mode
        )
    if config.research and config.research.is_dir():
        routes[_RESEARCH_ROUTE] = ForgivingFilesystemBackend(
            root_dir=config.research, virtual_mode=config.virtual_mode
        )
    if not routes:
        return workspace, sources
    return CompositeBackend(default=workspace, routes=routes), sources


def _interrupt_config(
    policy: PermissionPolicy, servers: mcp_module.Servers
) -> dict[str, bool | InterruptOnConfig] | None:
    """Pause for approval before anything that writes or executes.

    The `when` predicate is what makes the harness bearable: it consults the
    live policy on every call, so read-only commands never prompt and an
    "always allow" answer takes effect immediately without rebuilding the graph.
    """
    if policy.auto_approve:
        return None

    def gate(tool_name: str):
        def _when(request: ToolCallRequest) -> bool:
            return policy.needs_review(tool_name, request.tool_call.get("args") or {})

        return _when

    # MCP tools come from outside this codebase and can do anything their server
    # can, so every one of them asks the first time.
    guarded = [*_GUARDED_TOOLS, *(name for names in servers.connected.values() for name in names)]
    return {
        name: InterruptOnConfig(allowed_decisions=["approve", "reject", "respond"], when=gate(name))
        for name in guarded
    }


def _tool_names(graph: object) -> list[str]:
    """Best-effort introspection of the compiled graph's tool surface."""
    try:
        node = graph.nodes["tools"]  # type: ignore[index]
        tools = node.bound.tools_by_name  # type: ignore[attr-defined]
        return sorted(tools)
    except Exception:
        return []
