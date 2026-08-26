"""The REPL: reads a prompt, streams the agent, brokers permission decisions."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import warnings
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import FormattedText
from rich.console import Console

from hubbleflow import agent as agent_module
from hubbleflow import commands as command_module
from hubbleflow import mcp as mcp_module
from hubbleflow import config as config_module
from hubbleflow.config import CONFIG_DIR, Config
from hubbleflow.permissions import PermissionPolicy
from hubbleflow.ui.approve import ALWAYS, APPROVE, REJECT, ApprovalPrompt, choices_for
from hubbleflow.ui.banner import render_banner
from hubbleflow.ui.input import Composer
from hubbleflow.ui.render import Transcript, format_tool_call
from hubbleflow.ui.theme import ACCENT, FAINT, THEME

RECURSION_LIMIT = 220
REJECTION_DEFAULT = "The user declined this. Do not retry it; find another way or ask them what they'd prefer."

_KEY_HELP = {
    config_module.GEMINI: ("GOOGLE_API_KEY", "https://aistudio.google.com/apikey"),
    config_module.NVIDIA: ("NVIDIA_API_KEY", "https://build.nvidia.com"),
}


def _missing_key_message(provider: str) -> str | None:
    """Explain the one credential this provider needs, if it isn't set."""
    if config_module.key_for(provider):
        return None
    entry = _KEY_HELP.get(provider)
    if entry is None:
        return None
    name, url = entry
    return (
        f"No {name} found.\n\n"
        "Set it in your shell, in this project's .env, or in ~/.hubbleflow/.env:\n\n"
        f"    export {name}=...\n\n"
        f"Get a key at {url}"
    )


# Warnings that are wrong or inapplicable here, matched on text rather than by
# module so a genuinely new warning from either library still gets through.
_MUTED_WARNINGS = (
    # langchain-nvidia checks a model against the static table bundled with the
    # installed release, so anything newer looks unknown. Both of these fire on
    # models that work: muse-glimmer calls tools correctly despite the second.
    # `filterwarnings(message=...)` anchors at the start of the text, so these
    # need the leading wildcard.
    r".*but type is unknown and inference may fail",
    r".*is not known to support tools",
    r".*An API key is required for the hosted NIM",
)


def _quieten_sdk_chatter() -> None:
    """Silence advisory noise that would otherwise land mid-transcript.

    google-genai warns on every streamed tool call that we should be using its
    chat helper instead of generate_content. LangGraph drives the model, so that
    advice doesn't apply, and it's unactionable noise in a TUI.
    """
    for name in ("google_genai", "google_genai.models", "google.genai"):
        logging.getLogger(name).setLevel(logging.ERROR)
    # deepagents warns whenever a model has no built-in harness profile, which
    # is every mesh and Ollama model by definition.
    logging.getLogger("deepagents.profiles.harness.harness_profiles").setLevel(logging.ERROR)
    for pattern in _MUTED_WARNINGS:
        warnings.filterwarnings("ignore", message=pattern, category=UserWarning)


class Hubbleflow:
    """One session: one workspace, one thread, one agent."""

    def __init__(self, config: Config) -> None:
        _quieten_sdk_chatter()
        self.config = config
        self.console = Console(theme=THEME, soft_wrap=False)
        self.transcript = Transcript(self.console)
        self.harness: agent_module.Harness | None = None
        self._checkpointer: AsyncSqliteSaver | None = None
        self._composer = Composer(
            commands=command_module.listing,
            hint=self._hint,
            history_file=CONFIG_DIR / "history",
        )
        self._reason_prompt: PromptSession = PromptSession()
        self._warned_leak = False

    # -- lifecycle ----------------------------------------------------------

    async def run(self, first_prompt: str | None = None, *, interactive: bool = True) -> int:
        if (problem := _missing_key_message(self.config.provider)) is not None:
            self.console.print()
            self.console.print(problem, style="hf.warn", highlight=False)
            self.console.print()
            return 1

        if interactive:
            render_banner(
                self.console,
                model=self.config.model_name,
                workspace=self.config.workspace,
                thread_id=self.config.thread_id,
                resumed=self.config.resumed,
            )

        async with AsyncSqliteSaver.from_conn_string(str(self.config.session_db)) as checkpointer:
            self._checkpointer = checkpointer
            policy = PermissionPolicy(auto_approve=self.config.auto_approve)
            servers = await self._start_servers(interactive=interactive)
            self.harness = await agent_module.build(self.config, checkpointer, policy, servers=servers)

            if self.harness.cache and (released := self.harness.cache.sweep()):
                self.transcript.notice(f"released {released} cache(s) left by an earlier run", style="hf.faint")

            if first_prompt:
                self.transcript.user_echo(first_prompt)
                await self.turn(first_prompt)
            try:
                if interactive:
                    await self._repl()
            finally:
                # Storage bills for the whole TTL, so don't leave caches behind.
                if self.harness and self.harness.cache:
                    self.harness.cache.close()

        return 0

    async def _start_servers(self, *, interactive: bool) -> mcp_module.Servers:
        """Connect MCP servers, reporting what came up and what didn't."""
        if not mcp_module.read_config(self.config.workspace):
            return mcp_module.Servers()

        self.transcript.start("Connecting to MCP servers")
        try:
            servers = await mcp_module.connect(self.config.workspace)
        finally:
            self.transcript.flush()

        if interactive and servers.connected:
            count = sum(len(names) for names in servers.connected.values())
            self.transcript.notice(f"MCP: {', '.join(servers.connected)} — {count} tools", style="hf.ok")
        for name, reason in servers.failed.items():
            self.transcript.notice(f"MCP: {name} didn't start — {reason}", style="hf.warn")
        if servers.connected or servers.failed:
            self.transcript.print()
        return servers

    async def _repl(self) -> None:
        while True:
            try:
                text = (await self._composer.ask()).strip()
            except KeyboardInterrupt:
                continue
            except EOFError:
                break

            if not text:
                continue
            if text.startswith("/"):
                if await command_module.dispatch(self, text) == command_module.EXIT:
                    break
                continue

            self.transcript.user_echo(text)
            await self.turn(text)

        self.console.print()
        self.console.print("  Bye.", style="hf.faint")
        self.console.print()

    # -- a single turn ------------------------------------------------------

    async def turn(self, text: str) -> None:
        """Run the agent until it finishes or the user stops answering prompts."""
        self._warned_leak = False
        payload: Any = {"messages": [HumanMessage(content=text)]}
        while payload is not None:
            interrupt = await self._run(payload)
            payload = await self._resolve(interrupt) if interrupt is not None else None

    async def _run(self, payload: Any) -> Any:
        """Stream one leg of the graph. Returns an interrupt payload, or None."""
        self.transcript.start("Working")
        task = asyncio.create_task(self._consume(payload))
        restore = _trap_sigint(task)
        try:
            return await task
        except asyncio.CancelledError:
            self.transcript.flush()
            self.transcript.notice("Interrupted.", style="hf.warn")
            return None
        except Exception as error:  # a bad key, a rate limit, a model that doesn't exist
            self.transcript.flush()
            self.transcript.error(_explain(error))
            return None
        finally:
            restore()
            self.transcript.flush()

    async def _consume(self, payload: Any) -> Any:
        assert self.harness is not None
        interrupt: Any = None
        # Ids of messages already rendered token-by-token, so the update pass
        # can print the answer of a model that didn't stream without doubling
        # up on one that did.
        streamed: set[str] = set()
        stream = self.harness.graph.astream(
            payload,
            self._invoke_config(),
            stream_mode=["messages", "updates"],
            subgraphs=True,
        )
        async for namespace, mode, chunk in stream:
            if mode == "messages":
                # Only the top-level agent's prose is the answer; a subagent's
                # narration is working notes and stays out of the transcript.
                if not namespace:
                    text = _chunk_text(chunk)
                    if text:
                        streamed.add(_chunk_id(chunk))
                        self.transcript.append(text)
            elif mode == "updates":
                found = self._on_update(chunk, nested=bool(namespace), streamed=streamed)
                interrupt = found if found is not None else interrupt
        return interrupt

    def _on_update(self, chunk: dict, *, nested: bool, streamed: set[str] | None = None) -> Any:
        interrupt: Any = None
        for node, update in chunk.items():
            if node == "__interrupt__":
                interrupt = _interrupt_payload(update) or interrupt
                continue
            for message in _messages_of(update):
                if isinstance(message, AIMessage):
                    self.transcript.usage.add(message.usage_metadata)
                    if not nested and streamed is not None and message.id not in streamed:
                        self.transcript.append(_message_text(message))
                    if not nested and not message.tool_calls and looks_like_leaked_tool_call(_message_text(message)):
                        self._warn_leaked_call()
                    for call in message.tool_calls or ():
                        self._show_call(call, nested=nested)
                elif isinstance(message, ToolMessage):
                    self._show_result(message, nested=nested)
        return interrupt

    def _warn_leaked_call(self) -> None:
        """Say plainly that the model mangled a tool call, once per turn."""
        if self._warned_leak:
            return
        self._warned_leak = True
        self.transcript.notice(
            f"{self.config.model_name} wrote a tool call as text instead of calling the tool — "
            "the answer above is malformed. Try a model with reliable tool support (/models).",
            style="hf.warn",
        )

    def _show_call(self, call: dict, *, nested: bool) -> None:
        name = call.get("name", "tool")
        args = call.get("args") or {}
        if nested:
            self.transcript.print(format_tool_call(name, args), indent=4)
        else:
            self.transcript.tool_call(name, args)

    def _show_result(self, message: ToolMessage, *, nested: bool) -> None:
        content = message.content if isinstance(message.content, str) else str(message.content)
        failed = getattr(message, "status", None) == "error"
        if nested:
            return  # the subagent's own tool output would drown the transcript
        self.transcript.tool_result(message.name or "tool", content, failed=failed)

    # -- permissions --------------------------------------------------------

    async def _resolve(self, interrupt: Any) -> Any:
        """Ask the user about each pending action, then build the resume command."""
        requests = (interrupt or {}).get("action_requests") or []
        if not requests:
            return None

        decisions = []
        for request in requests:
            decisions.append(await self._ask_one(request))
        return Command(resume={"decisions": decisions})

    async def _ask_one(self, request: dict) -> dict:
        assert self.harness is not None
        name = request.get("name", "tool")
        args = request.get("args") or {}
        signature = PermissionPolicy.signature_for(name, args)

        self.console.print()
        answer = await ApprovalPrompt(_title_for(name), choices_for(name, signature)).ask()

        if answer == ALWAYS:
            self.harness.policy.allow_always(name, args)
            self.transcript.notice(f"✓ allowed for the rest of this session: {signature}", style="hf.ok")
            return {"type": "approve"}
        if answer == APPROVE:
            return {"type": "approve"}

        reason = await self._ask_reason()
        if reason:
            self.transcript.notice(f"✗ declined — {reason}", style="hf.warn")
            return {"type": "respond", "message": reason}
        self.transcript.notice("✗ declined", style="hf.warn")
        return {"type": "reject", "message": REJECTION_DEFAULT}

    async def _ask_reason(self) -> str:
        message = FormattedText([(ACCENT, "  › "), (FAINT, "what should it do instead? (enter to skip) ")])
        with contextlib.suppress(KeyboardInterrupt, EOFError):
            return (await self._reason_prompt.prompt_async(message)).strip()
        return ""

    # -- things slash commands drive ----------------------------------------

    async def switch_model(self, spec: str) -> None:
        assert self.harness is not None and self._checkpointer is not None
        previous = self.config.model
        self.config.model = spec
        try:
            self.harness = await agent_module.build(
                self.config, self._checkpointer, self.harness.policy, servers=self.harness.mcp
            )
        except Exception as error:
            self.config.model = previous
            self.transcript.error(f"Couldn't switch to {spec}: {_explain(error)}")
            return
        self.transcript.notice(f"model  {self.config.model_name}", style="hf.ok")

    async def set_caching(self, enabled: bool) -> bool:
        """Rebuild the agent with caching on or off. Returns the resulting state."""
        assert self.harness is not None and self._checkpointer is not None
        if enabled == self.config.cache:
            return self.config.cache

        previous, released = self.config.cache, 0
        if not enabled and self.harness.cache:
            # Turning it off should stop the meter immediately, not at exit.
            self.harness.cache.close()
            released = 1

        self.config.cache = enabled
        try:
            self.harness = await agent_module.build(
                self.config, self._checkpointer, self.harness.policy, servers=self.harness.mcp
            )
        except Exception as error:
            self.config.cache = previous
            self.transcript.error(f"Couldn't change caching: {_explain(error)}")
            return previous
        if released:
            self.transcript.notice("released the active cache", style="hf.faint")
        return self.config.cache

    async def new_session(self) -> None:
        fresh = Config.load(workspace=self.config.workspace, model=self.config.model)
        self.config.thread_id = fresh.thread_id
        self.console.clear()
        render_banner(
            self.console,
            model=self.config.model_name,
            workspace=self.config.workspace,
            thread_id=self.config.thread_id,
            resumed=False,
        )

    # -- helpers ------------------------------------------------------------

    def _invoke_config(self) -> dict:
        return {
            "configurable": {"thread_id": self.config.thread_id},
            "recursion_limit": RECURSION_LIMIT,
        }

    def _hint(self) -> str:
        bits = ["/ for commands", "@ for files", "esc+enter for newline", self.config.model_name]
        if self.harness and self.harness.policy.auto_approve:
            bits.append("approvals off")
        return "  ·  ".join(bits)


# --------------------------------------------------------------------------
# stream plumbing
# --------------------------------------------------------------------------

def _chunk_text(chunk: Any) -> str:
    message = chunk[0] if isinstance(chunk, tuple) else chunk
    if not isinstance(message, AIMessageChunk):
        return ""
    content = message.content
    if isinstance(content, str):
        return content
    # Gemini streams a list of typed parts; keep the text, drop tool-call parts.
    return "".join(
        part.get("text", "")
        for part in content
        if isinstance(part, dict) and part.get("type") in (None, "text")
    )


def _chunk_id(chunk: Any) -> str:
    message = chunk[0] if isinstance(chunk, tuple) else chunk
    return getattr(message, "id", "") or ""


def _message_text(message: AIMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(
        part.get("text", "")
        for part in content
        if isinstance(part, dict) and part.get("type") in (None, "text")
    )


def _messages_of(update: Any) -> list:
    if not isinstance(update, dict):
        return []
    messages = update.get("messages")
    if isinstance(messages, list):
        return messages
    return [messages] if messages is not None else []


def _interrupt_payload(update: Any) -> Any:
    for item in update if isinstance(update, (list, tuple)) else [update]:
        value = getattr(item, "value", item)
        if isinstance(value, dict) and "action_requests" in value:
            return value
    return None


def _title_for(name: str) -> str:
    match name:
        case "bash" | "execute":
            return "Run this command?"
        case "write_file":
            return "Create this file?"
        case "edit_file":
            return "Apply this edit?"
        case "delete":
            return "Delete this?"
        case _:
            return f"Allow {name}?"


def _trap_sigint(task: asyncio.Task):
    """Route ctrl+c to cancelling the run rather than killing the process."""
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, task.cancel)
    except (NotImplementedError, RuntimeError):
        return lambda: None

    def restore() -> None:
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.remove_signal_handler(signal.SIGINT)

    return restore


# Some models reproduce tool-call syntax as prose instead of emitting a real
# call. The agent then sees a message with no tool calls, treats it as the final
# answer, and either stops early or loops. Worth naming rather than leaving the
# user to guess.
_LEAKED_CALL_MARKERS = ("function_calls>", "antml:", "atem:", "<invoke name=", "<tool_call>")


def looks_like_leaked_tool_call(text: str) -> bool:
    return any(marker in text for marker in _LEAKED_CALL_MARKERS)


def _explain(error: Exception) -> str:
    text = str(error) or error.__class__.__name__
    lowered = text.lower()
    name = error.__class__.__name__

    if "timeout" in lowered or "timeout" in name.lower():
        return (
            f"{text}\n  The request took longer than the {config_module.request_timeout():.0f}s limit. "
            "Raise it with HUBBLEFLOW_TIMEOUT=600, or switch to a faster model."
        )
    if "recursion" in lowered:
        return (
            f"{text}\n  The agent looped without finishing. That usually means the model is "
            "writing tool calls as text instead of calling tools — try /models for one with "
            "reliable tool support."
        )
    if "api key" in lowered or "api_key" in lowered or "permission_denied" in lowered:
        return f"{text}\n  Check GOOGLE_API_KEY — /models will list what your key can reach."
    if "not found" in lowered and "model" in lowered:
        return f"{text}\n  Run /models to see the model ids your key can reach, then /model <id>."
    if "quota" in lowered or "429" in text or "resource_exhausted" in lowered:
        return f"{text}\n  Rate limited by the Gemini API — wait a moment, or /model a lighter model."
    return text
