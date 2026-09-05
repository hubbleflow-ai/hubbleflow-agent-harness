"""Slash commands. Everything the user can do that isn't a prompt for the model."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.table import Table
from rich.text import Text

import json
import os
from pathlib import Path

from hubbleflow import mcp as mcp_module
from hubbleflow import knowledge as knowledge_module
from hubbleflow import research as research_module
from hubbleflow.tools import knowledge as tools_knowledge
from hubbleflow import models as model_registry

if TYPE_CHECKING:
    from hubbleflow.app import Hubbleflow

EXIT = "exit"
CONTINUE = "continue"


@dataclass(slots=True, frozen=True)
class Command:
    name: str
    description: str
    handler: Callable[["Hubbleflow", str], Awaitable[str]]


async def dispatch(app: "Hubbleflow", text: str) -> str:
    """Run a slash command. Returns EXIT to end the session."""
    head, _, args = text.strip().partition(" ")
    command = REGISTRY.get(head.lower())
    if command is None:
        app.transcript.notice(f"Unknown command {head}. Try /help.", style="hf.warn")
        return CONTINUE
    return await command.handler(app, args.strip())


def listing() -> list[tuple[str, str]]:
    """(name, description) pairs for the composer's completion menu."""
    return [(c.name, c.description) for c in ORDERED]


# --------------------------------------------------------------------------
# handlers
# --------------------------------------------------------------------------

async def _help(app: "Hubbleflow", args: str) -> str:
    table = Table.grid(padding=(0, 3))
    table.add_column(style="hf.accent", no_wrap=True)
    table.add_column(style="hf.muted")
    for command in ORDERED:
        table.add_row(command.name, command.description)
    app.transcript.print()
    app.transcript.print(table, indent=2)
    app.transcript.print()
    return CONTINUE


async def _model(app: "Hubbleflow", args: str) -> str:
    if not args:
        app.transcript.notice(f"model  {app.config.model}")
        return CONTINUE
    await app.switch_model(model_registry.resolve(args))
    return CONTINUE


_PROVIDER_LABELS = {
    model_registry.GEMINI: ("Gemini", "billed to your Google API key"),
    model_registry.NVIDIA: ("NVIDIA", "hosted NIM, billed to your NVIDIA key"),
    model_registry.MESH: ("Mesh", "pooled across the mesh, no per-token cost"),
    model_registry.OLLAMA: ("Ollama", "pulled locally, runs offline"),
    **{s.provider: (s.label, s.blurb) for s in model_registry.LOCAL_SERVERS},
}

_CLOUD = [model_registry.GEMINI, model_registry.NVIDIA]
_SELF_HOSTED = [model_registry.MESH,
                *[s.provider for s in model_registry.LOCAL_SERVERS],
                model_registry.OLLAMA]
_ALL = [*_CLOUD, *_SELF_HOSTED]

# NVIDIA lists well over a hundred; showing them all buries everything else.
MAX_PER_PROVIDER = 20


async def _models(app: "Hubbleflow", args: str) -> str:
    return await _show_models(app, _ALL, args)


async def _local(app: "Hubbleflow", args: str) -> str:
    return await _show_models(app, _SELF_HOSTED, args)


async def _mesh(app: "Hubbleflow", args: str) -> str:
    posture = model_registry.mesh_posture()
    if posture.reachable:
        app.transcript.print()
        line = Text("  node  ", style="hf.muted")
        if posture.contributing:
            line.append("contributing", style="hf.warn").append(f"  {posture.why()}", style="hf.faint")
        else:
            line.append("client-only", style="hf.ok").append(
                f"  {posture.node_state.lower()}, {posture.peers} peers, giving no compute", style="hf.faint")
        app.transcript.print(line)
    return await _show_models(app, [model_registry.MESH], args)


async def _cloud(app: "Hubbleflow", args: str) -> str:
    return await _show_models(app, _CLOUD, args)


async def _show_models(app: "Hubbleflow", providers: list[str], needle: str = "") -> str:
    model_registry.forget_local_models()
    needle = needle.strip().lower()
    listings = [
        (provider, [(n, d) for n, d in _names_for(provider) if needle in n.lower()])
        for provider in providers
    ]

    if not any(names for _, names in listings):
        # Distinguish "your key reaches nothing" from "your filter matched nothing".
        reachable = any(_names_for(provider) for provider in providers)
        message = f"Nothing matching {needle!r}." if needle and reachable else _nothing_reachable(providers)
        app.transcript.notice(message, style="hf.warn")
        return CONTINUE

    app.transcript.print()
    for provider, names in listings:
        label, why = _PROVIDER_LABELS[provider]
        if not names:
            reason = f"nothing matching {needle!r}" if needle else "nothing reachable"
            app.transcript.print(Text(f"  {label}  —  {reason}", style="hf.faint"))
            app.transcript.print()
            continue
        header = Text(f"  {label}", style="hf.accent")
        header.append(f"  {why}", style="hf.faint")
        if len(names) > MAX_PER_PROVIDER:
            header.append(f"  ({len(names)} models)", style="hf.faint")
        app.transcript.print(header)
        shown = names if len(names) <= MAX_PER_PROVIDER else _around_current(names, app.config.model_name)
        for name, detail in shown:
            current = f"{provider}:{name}" == app.config.model
            line = Text(f"    {'●' if current else ' '} ", style="hf.accent")
            line.append(name.ljust(38), style="default" if current else "hf.muted")
            if detail:
                line.append(detail, style="hf.faint")
            app.transcript.print(line)
        if len(names) > len(shown):
            app.transcript.print(Text(f"    … {len(names) - len(shown)} more — narrow it with a word, e.g. /cloud muse", style="hf.faint"))
        app.transcript.print()

    app.transcript.print(Text("  /model <name> to switch — a bare name finds the right provider", style="hf.faint"))
    app.transcript.print()
    return CONTINUE


def _names_for(provider: str) -> list[tuple[str, str]]:
    if provider == model_registry.MESH:
        return [(name, _context_note(ctx)) for name, ctx in model_registry.list_mesh_details()]
    if (server := model_registry.local_server(provider)) is not None:
        return [(name, _context_note(ctx)) for name, ctx in model_registry.list_server_details(server)]
    if provider == model_registry.OLLAMA:
        return [(name, f"{size} GB" if size else "") for name, size in model_registry.list_ollama_details()]
    if provider == model_registry.NVIDIA:
        return [(name, "") for name in model_registry.list_nvidia()]
    return [(name, "") for name in model_registry.list_gemini()]


def _context_note(context: int) -> str:
    """Context size, called out when it's too small to hold the system prompt."""
    if not context:
        return ""
    if context < model_registry.MIN_USABLE_CONTEXT:
        return f"{context:,} ctx — too small for this harness"
    return f"{context:,} ctx"


def _around_current(names: list[tuple[str, str]], current: str) -> list[tuple[str, str]]:
    """Trim a long list but never hide the model actually in use."""
    head = names[:MAX_PER_PROVIDER]
    if any(name == current for name, _ in head):
        return head
    match = [entry for entry in names if entry[0] == current]
    return match + head[: MAX_PER_PROVIDER - len(match)] if match else head


def _mesh_advice() -> str:
    """Say the one thing that's actually true: not installed, not up, or hosting."""
    where = model_registry.mesh_url()
    posture = model_registry.mesh_posture()

    if posture.reachable and posture.contributing:
        return (
            f"That node is contributing compute to the mesh ({posture.why()}), so it isn't being used.\n"
            "  Restart it as a consumer with `mesh-llm stop` then `mesh-llm client --auto`,\n"
            "  or set HUBBLEFLOW_MESH_ALLOW_HOST=1 to use it as it is."
        )
    if model_registry.mesh_installed():
        return f"No mesh node answering at {where} — start one with `mesh-llm client --auto`."
    return (
        f"No mesh node at {where}, and mesh-llm isn't installed. Install it with\n"
        "    curl -fsSL https://raw.githubusercontent.com/Mesh-LLM/mesh-llm/main/install.sh | bash\n"
        "  then `mesh-llm setup` and `mesh-llm serve --auto`. Set MESH_LLM_URL if your node is elsewhere."
    )


def _nothing_reachable(providers: list[str]) -> str:
    if providers == [model_registry.MESH]:
        return _mesh_advice()
    if providers == _SELF_HOSTED:
        return f"Nothing self-hosted — is Ollama running at {model_registry.ollama_host()}? For a mesh: {_mesh_advice()}"
    if providers == [model_registry.OLLAMA]:
        return f"No local models — is Ollama running at {model_registry.ollama_host()}?"
    if providers == _CLOUD:
        return "No hosted models reachable — check GOOGLE_API_KEY and NVIDIA_API_KEY."
    return "Nothing matched — check your keys, or start Ollama."


async def _cache(app: "Hubbleflow", args: str) -> str:
    want = args.strip().lower()
    if want in {"on", "1", "true", "yes"}:
        await app.set_caching(True)
    elif want in {"off", "0", "false", "no"}:
        await app.set_caching(False)
    elif want:
        app.transcript.notice("Usage: /cache, /cache on, /cache off", style="hf.warn")
        return CONTINUE
    elif app.config.provider != model_registry.GEMINI:
        app.transcript.notice(f"Caching is a Gemini feature; this session is on {app.config.provider}.")
        return CONTINUE
    else:
        await app.set_caching(not app.config.cache)

    stats = app.harness.cache_stats
    if app.config.cache:
        detail = f"{stats.builds} builds, {stats.reused} reuses" if stats.active else "nothing cached yet"
        app.transcript.notice(f"caching on — {detail}", style="hf.ok")
    else:
        app.transcript.notice("caching off — nothing is being stored")
    return CONTINUE


async def _mcp(app: "Hubbleflow", args: str) -> str:
    servers = app.harness.mcp
    if not servers.any_configured:
        app.transcript.print()
        app.transcript.print(Text("  No MCP servers configured.", style="hf.muted"))
        app.transcript.print(Text(f"  Add them to {mcp_module.GLOBAL_CONFIG} or ./{mcp_module.CONFIG_NAME}:", style="hf.faint"))
        app.transcript.print()
        app.transcript.print(Text(json.dumps(mcp_module.EXAMPLE, indent=2), style="hf.faint"), indent=4)
        app.transcript.print()
        return CONTINUE

    app.transcript.print()
    for name, tools in servers.connected.items():
        header = Text(f"  ● {name}", style="hf.ok")
        header.append(f"  {len(tools)} tools", style="hf.faint")
        app.transcript.print(header)
        app.transcript.print(Text("    " + "  ".join(tools), style="hf.muted"))
        app.transcript.print()
    for name, reason in servers.failed.items():
        app.transcript.print(Text(f"  ✗ {name}", style="hf.err").append(f"  {reason}", style="hf.faint"))
    if servers.failed:
        app.transcript.print()
    return CONTINUE


async def _tools(app: "Hubbleflow", args: str) -> str:
    app.transcript.print()
    app.transcript.print(Text("  " + "  ".join(app.harness.tool_names), style="hf.muted"))
    app.transcript.print()
    return CONTINUE


async def _clear(app: "Hubbleflow", args: str) -> str:
    await app.new_session()
    return CONTINUE


# Published rates, $ per million tokens: (fresh input, output, cached input).
# Override with HUBBLEFLOW_PRICE_IN / _OUT / _CACHED when these go stale.
_PRICES = {
    "gemini-3.5-flash-lite": (0.30, 2.50, 0.03),
    "gemini-3.7-flash": (0.30, 2.50, 0.03),
}


async def _usage(app: "Hubbleflow", args: str) -> str:
    usage = app.transcript.usage
    stats = app.harness.cache_stats

    app.transcript.print()
    line = Text("  ", style="hf.muted")
    line.append(f"{usage.sent:,}", style="default").append(" sent", style="hf.muted")
    if usage.cached:
        share = usage.cached / usage.sent * 100 if usage.sent else 0
        line.append("  ·  ", style="hf.faint")
        line.append(f"{usage.cached:,}", style="hf.ok").append(f" cached ({share:.0f}%)", style="hf.muted")
    line.append("  ·  ", style="hf.faint")
    line.append(f"{usage.received:,}", style="default").append(" received", style="hf.muted")
    app.transcript.print(line)

    if stats.active:
        app.transcript.print(Text(
            f"  cache: {stats.builds} builds, {stats.reused} reuses, {stats.tokens_cached:,} tokens held",
            style="hf.faint"))
    elif app.config.cache and stats.failures:
        app.transcript.print(Text(f"  cache unavailable — {stats.last_error}", style="hf.warn"))
    elif not app.config.cache:
        app.transcript.print(Text("  caching off — start with --cache to turn it on", style="hf.faint"))

    if (cost := _cost(app.config.model_name, usage)) is not None:
        detail = Text(f"  ≈ ${cost:.4f} at published rates", style="hf.faint")
        if usage.cached:
            saved = _saving(app.config.model_name, usage) - stats.storage_cost()
            detail.append(f"  ·  caching net ${saved:+.4f}", style="hf.ok" if saved > 0 else "hf.warn")
        app.transcript.print(detail)
    if (window := await _window_line(app)) is not None:
        app.transcript.print(window)
    app.transcript.print(Text(f"  session {app.config.thread_id}", style="hf.faint"))
    app.transcript.print()
    return CONTINUE


# Compaction replaces the history with a message that opens this way. Matching
# on it is how a session can say whether it has summarised, which the sent and
# received counters cannot -- those are cumulative spend, not transcript size.
_SUMMARY_MARKER = "here is a summary of the conversation to date"


async def _window_line(app: "Hubbleflow") -> Text | None:
    """How much of the window the conversation holds, and whether it compacted.

    `sent` counts every token of every turn, so it says nothing about how large
    the conversation currently is -- on a local model most of it is the system
    prompt, resent each turn. This is the number people actually mean when they
    ask whether compaction has run.
    """
    from hubbleflow.agent import _summarize_after

    try:
        state = await app.harness.graph.aget_state(
            {"configurable": {"thread_id": app.config.thread_id}}
        )
        messages = (state.values or {}).get("messages") or []
    except Exception:
        return None
    if not messages:
        return None

    # Four characters a token is rough, and right enough to answer "how close
    # am I" without pulling in a tokeniser for every provider.
    chars = 0
    compactions = 0
    for message in messages:
        content = getattr(message, "content", "")
        text = content if isinstance(content, str) else json.dumps(content)
        chars += len(text)
        if text[:80].lower().startswith(_SUMMARY_MARKER):
            compactions += 1

    held = chars // 4
    trigger = _summarize_after(app.config)
    share = held / trigger * 100 if trigger else 0

    line = Text("  ", style="hf.muted")
    line.append(f"~{held:,}", style="default")
    line.append(f" / {trigger:,} tokens before compaction", style="hf.muted")
    line.append(f"  ({share:.0f}%)", style="hf.warn" if share > 75 else "hf.faint")
    line.append("  ·  ", style="hf.faint")
    if compactions:
        line.append(f"compacted {compactions}×", style="hf.ok")
    else:
        line.append("not yet compacted", style="hf.faint")
    return line


def _cost(model: str, usage) -> float | None:
    """Estimated spend, when we know this model's rates."""
    rates = _PRICES.get(model)
    price_in = _env_price("HUBBLEFLOW_PRICE_IN", rates[0] if rates else None)
    price_out = _env_price("HUBBLEFLOW_PRICE_OUT", rates[1] if rates else None)
    price_cached = _env_price("HUBBLEFLOW_PRICE_CACHED", rates[2] if rates else None)
    if price_in is None or price_out is None:
        return None
    cached_cost = usage.cached / 1e6 * (price_cached if price_cached is not None else price_in)
    return usage.fresh / 1e6 * price_in + cached_cost + usage.received / 1e6 * price_out


def _saving(model: str, usage) -> float:
    """What those cached tokens would have cost at the full input rate."""
    rates = _PRICES.get(model)
    price_in = _env_price("HUBBLEFLOW_PRICE_IN", rates[0] if rates else None)
    price_cached = _env_price("HUBBLEFLOW_PRICE_CACHED", rates[2] if rates else None)
    if price_in is None or price_cached is None:
        return 0.0
    return usage.cached / 1e6 * (price_in - price_cached)


def _env_price(name: str, fallback: float | None) -> float | None:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return fallback


async def _permissions(app: "Hubbleflow", args: str) -> str:
    policy = app.harness.policy
    if args in {"on", "ask"}:
        policy.auto_approve = False
    elif args in {"off", "auto", "yolo"}:
        policy.auto_approve = True
    else:
        policy.auto_approve = not policy.auto_approve
    state = "off — nothing will ask before running" if policy.auto_approve else "on — writes and commands need approval"
    app.transcript.notice(f"approvals {state}", style="hf.warn" if policy.auto_approve else "hf.muted")
    return CONTINUE


async def _allowed(app: "Hubbleflow", args: str) -> str:
    policy = app.harness.policy
    entries = sorted(policy.always_allow_commands) + sorted(policy.always_allow_tools)
    if not entries:
        app.transcript.notice("Nothing allowlisted this session.")
        return CONTINUE
    app.transcript.print()
    for entry in entries:
        app.transcript.print(Text(f"  ✓ {entry}", style="hf.ok"))
    app.transcript.print()
    return CONTINUE


_SKILL_TEMPLATE = """---
name: {name}
description: >-
  One or two sentences saying when to reach for this skill. The model reads only
  this line when deciding whether to open the file, so describe the situation,
  not the contents.
---

# {title}

Write the procedure here: what to do, in what order, and what to avoid.
"""


async def _skills(app: "Hubbleflow", args: str) -> str:
    """List skills, show one, or scaffold a new one."""
    verb, _, rest = args.partition(" ")
    if verb == "new":
        return _skill_new(app, rest.strip())
    if args:
        return _skill_show(app, args.strip())

    found = [(scope, path) for scope, root in _skill_roots(app) for path in _skill_files(root)]
    if not found:
        app.transcript.notice("No skills yet. /skills new <name> scaffolds one.", style="hf.warn")
        return CONTINUE

    table = Table.grid(padding=(0, 3))
    table.add_column(style="hf.accent", no_wrap=True)
    table.add_column(style="hf.faint", no_wrap=True)
    table.add_column(style="hf.muted")
    for scope, path in found:
        name, description = _skill_meta(path)
        table.add_row(name, scope, description)
    app.transcript.print()
    app.transcript.print(table, indent=2)
    app.transcript.print(Text("  /skills <name> to read one  ·  /skills new <name> to add one", style="hf.faint"))
    app.transcript.print()
    return CONTINUE


def _skill_roots(app: "Hubbleflow") -> list[tuple[str, Path]]:
    """Project skills first: a workspace's own version of a name should win."""
    roots = []
    if app.config.project_skills:
        roots.append(("project", app.config.project_skills))
    if app.config.global_skills:
        roots.append(("global", app.config.global_skills))
    return roots


def _skill_files(root: Path) -> list[Path]:
    try:
        return sorted(root.glob("*/SKILL.md"))
    except OSError:
        return []


def _skill_meta(path: Path) -> tuple[str, str]:
    """(name, description) from the frontmatter, falling back to the directory.

    A malformed skill still gets listed -- knowing it is there and broken beats
    it silently vanishing from the listing.
    """
    name, description = path.parent.name, ""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return name, "unreadable"
    if text.startswith("---"):
        _, _, rest = text.partition("---")
        block, _, _ = rest.partition("---")
        try:
            import yaml

            meta = yaml.safe_load(block) or {}
        except Exception:
            return name, "unreadable frontmatter"
        if isinstance(meta, dict):
            name = str(meta.get("name") or name)
            description = " ".join(str(meta.get("description") or "").split())
    return name, description


def _skill_show(app: "Hubbleflow", name: str) -> str:
    for _, root in _skill_roots(app):
        path = root / name / "SKILL.md"
        if path.is_file():
            app.transcript.print()
            app.transcript.print(Text(path.read_text(encoding="utf-8").rstrip(), style="default"), indent=2)
            app.transcript.print()
            return CONTINUE
    app.transcript.notice(f"No skill named {name}. /skills lists them.", style="hf.warn")
    return CONTINUE


def _skill_new(app: "Hubbleflow", name: str) -> str:
    """Scaffold a project skill. Global skills are seeded, not authored here."""
    if not name or "/" in name or name.startswith("."):
        app.transcript.notice("Usage: /skills new <name>  (letters, digits and dashes)", style="hf.warn")
        return CONTINUE

    path = app.config.workspace / ".hubbleflow" / "skills" / name / "SKILL.md"
    if path.exists():
        app.transcript.notice(f"{name} already exists at {path}", style="hf.warn")
        return CONTINUE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_SKILL_TEMPLATE.format(name=name, title=name.replace("-", " ").title()), encoding="utf-8")
    except OSError as error:
        app.transcript.notice(f"Couldn't write {path}: {error}", style="hf.err")
        return CONTINUE

    app.transcript.notice(f"Created {path} — edit it, then /clear to reload.", style="hf.ok")
    return CONTINUE


_OPENING = """\
Research this properly and then file what you find:

    {question}

Work in rounds rather than one pass. Plan the sub-questions the answer depends \
on, search each as a real question, and read the primary source before relying \
on any of it -- a search summary can blend two versions together and sound \
certain about it. When a result contradicts what you expected, that is the one \
to corroborate, not the one to accept. When the question touches something in \
this repository, check the code and the lockfile: those are facts about this \
project, and they beat a claim about the world.

After each round, ask what is still unanswered. Go again if the gap matters. \
Stop when another round would not change the conclusion.
"""

_UPDATING = """\
This topic is already open. Here is what it currently says:

{overview}

Last updated {last}. Find out what has changed since then, and what was \
unresolved and now is not. Do not start again from nothing -- read what is \
above, then research forward from it.

Read the primary source before relying on it, and corroborate anything that \
contradicts what is already recorded rather than assuming the newer claim wins.
"""

_CLOSING = """\

Then call save_research once, using the topic name `{topic}`.

Write the overview as the whole picture as it now stands, not only the new \
part -- it replaces what was there. What changed goes in `changed`, one line, \
for the log. Write it so a reader need not open the sources to follow it, and \
say where the evidence is thin or the sources disagreed. If the answer is \
genuinely short, say so and say why, rather than padding it into bullets.

The pages you opened are recorded for you, so refer to them by what they are \
(the outlet and the date) instead of pasting links. Judge stale_after_days \
honestly: a running story is days, something settled is 0.

Do not claim you have saved anything. The tool call is what saves it, and you \
will be told whether it worked.\
"""


async def _deep_research(app: "Hubbleflow", args: str) -> str:
    """Open a topic, or carry an open one forward."""
    force = False
    question = args.strip()
    for flag in ("--again", "--force"):
        if question.startswith(flag):
            force, question = True, question[len(flag):].strip()

    root = app.config.research
    if root is None:
        app.transcript.notice(
            "Research is turned off — HUBBLEFLOW_RESEARCH is empty.", style="hf.warn")
        return CONTINUE
    if not question:
        return await _knowledge(app, "") if root.is_dir() else _no_research(app)

    directory = research_module.existing_topic(root, question)
    if directory is not None and not force and not _due(directory):
        app.transcript.print()
        app.transcript.notice(
            f"Already open: {directory.name} — /deep-research --again to carry it forward.",
            style="hf.ok")
        app.transcript.print(Text(f"    /research/{directory.name}/overview.md", style="hf.accent"), indent=2)
        app.transcript.print()
        return CONTINUE

    topic = directory.name if directory is not None else research_module.slug(question)
    brief = _OPENING.format(question=question)
    if directory is not None:
        brief = _UPDATING.format(
            overview=research_module.read_overview(directory)[:4000],
            last=research_module.last_logged(directory) or "an earlier round",
        )
    brief += _CLOSING.format(topic=topic)

    app.transcript.user_echo(f"/deep-research {question}")
    logged_before = research_module.last_logged(research_module.topic_dir(root, topic))
    await app.turn(brief)
    await _close_round(app, topic, question, logged_before)
    return CONTINUE


def _due(directory) -> bool:
    """A topic past its own expiry is carried forward rather than reused."""
    from hubbleflow.knowledge import load

    for concept in load(directory.parent, "research").concepts:
        if concept.path.startswith(f"{directory.name}/") and concept.type == research_module.TOPIC_TYPE:
            return concept.stale()
    return False


async def _close_round(app: "Hubbleflow", topic: str, question: str, logged_before: str) -> None:
    """Record what was read, file the answer if the model didn't, then link them.

    Sources are written from what `web_fetch` returned rather than from a list
    the model provides: a page it opened is a fact, and asking it to remember
    which ones is how the citations came out wrong before.
    """
    root = app.config.research
    directory = research_module.topic_dir(root, topic)

    pages = await _fetched_pages(app)
    already = research_module.known_sources(directory)
    recorded = [
        path for url, title, body in pages
        if url not in already
        and (path := research_module.record_source(directory, url=url, title=title, body=body))
    ]

    if not (directory / research_module.OVERVIEW).exists():
        answer = await _last_answer(app)
        if not answer or len(answer) < 200:
            app.transcript.notice(await _why_nothing(app, answer), style="hf.warn")
            return
        written, message = research_module.revise_overview(
            directory, title=question, overview=answer, status="draft",
            description="Filed automatically; the model did not classify it.")
        if not written:
            app.transcript.notice(message, style="hf.err")
            return
        research_module.append_log(directory, "Opened; filed by the harness.")
        app.transcript.notice(
            f"{message} (filed for you — the model didn't call save_research)", style="hf.ok")

    if recorded:
        research_module.link_sources(directory, recorded)
        research_module.write_index(directory)
    app.transcript.notice(
        f"/research/{directory.name}/ — {len(recorded)} source(s) recorded this round",
        style="hf.faint")


async def _fetched_pages(app: "Hubbleflow") -> list[tuple[str, str, str]]:
    """(url, title, body) for every page this turn actually opened.

    Paired from the call to its result, so the body kept is the page that came
    back rather than anything the model said about it.
    """
    try:
        state = await app.harness.graph.aget_state(
            {"configurable": {"thread_id": app.config.thread_id}})
        messages = (state.values or {}).get("messages") or []
    except Exception:
        return []

    wanted: dict[str, str] = {}
    for message in messages:
        for call in getattr(message, "tool_calls", None) or []:
            if call.get("name") == "web_fetch" and (url := (call.get("args") or {}).get("url")):
                wanted[str(call.get("id"))] = str(url)

    pages = []
    for message in messages:
        call_id = getattr(message, "tool_call_id", None)
        if call_id not in wanted:
            continue
        content = getattr(message, "content", "")
        body = content if isinstance(content, str) else json.dumps(content)
        pages.append((wanted[call_id], _title_of(body) or wanted[call_id], body))
    return pages


async def _why_nothing(app: "Hubbleflow", answer: str) -> str:
    """Say what the model actually did, not just that nothing was kept.

    The transcript can be full of text and still have nothing to file, because
    what fills it is tool output being rendered. Reporting that as "no
    conclusion worth keeping" reads as a judgment on research that was never
    written, and leaves nothing to act on.
    """
    used: dict[str, int] = {}
    try:
        state = await app.harness.graph.aget_state(
            {"configurable": {"thread_id": app.config.thread_id}})
        for message in (state.values or {}).get("messages") or []:
            for call in getattr(message, "tool_calls", None) or []:
                name = call.get("name", "?")
                used[name] = used.get(name, 0) + 1
    except Exception:
        pass

    did = ", ".join(f"{n}×{c}" for n, c in sorted(used.items())) or "nothing"
    if answer:
        return (
            f"Nothing filed — the model stopped after {len(answer)} characters, too "
            f"little to keep. It called {did}. Try /deep-research --again, or a larger model."
        )
    return (
        f"Nothing filed — the model never wrote an answer. It called {did} and then "
        f"stopped. The text on screen was tool output, not its conclusion. "
        f"Try /deep-research --again, or /model something larger."
    )


async def _last_answer(app: "Hubbleflow") -> str:
    """The final assistant message of the turn that just ran."""
    try:
        state = await app.harness.graph.aget_state(
            {"configurable": {"thread_id": app.config.thread_id}})
        messages = (state.values or {}).get("messages") or []
    except Exception:
        return ""
    for message in reversed(messages):
        if type(message).__name__ == "AIMessage":
            content = message.content
            text = content if isinstance(content, str) else json.dumps(content)
            if text.strip():
                return text
    return ""


def _title_of(page: str) -> str:
    """The page's own first heading, which names the file better than a URL."""
    import re

    match = re.search(r"^#\s+(.+)$", page[:2000], re.MULTILINE)
    return " ".join(match.group(1).split())[:80] if match else ""


def _no_research(app: "Hubbleflow") -> str:
    app.transcript.notice(
        "Nothing researched yet. /deep-research <question> to start.", style="hf.faint")
    return CONTINUE


def _already_known(bundle, question: str, overlap: float = 0.6) -> list:
    """Notes that answer this question and haven't expired.

    Word overlap rather than substring: a question is phrased freely and will
    not appear verbatim in a title. Stale notes are deliberately not returned --
    the point of `stale_after` is that the answer gets looked at again.
    """
    import re

    asked = {w for w in re.findall(r"[a-z0-9]+", question.lower()) if len(w) > 2}
    if not asked:
        return []

    found = []
    for concept in bundle.concepts:
        if concept.stale() or concept.status == "deprecated":
            continue
        haystack = " ".join((concept.title, concept.description, *concept.tags)).lower()
        words = set(re.findall(r"[a-z0-9]+", haystack))
        if len(asked & words) / len(asked) >= overlap:
            found.append(concept)
    return found


async def _knowledge(app: "Hubbleflow", args: str) -> str:
    """Show the OKF bundle: a tree by type, a search, or the graph."""
    root = app.config.knowledge
    if root is None and not (app.config.research and app.config.research.is_dir()):
        app.transcript.notice(
            "No knowledge bundle. Generate one with OpenWiki, or point "
            "HUBBLEFLOW_KNOWLEDGE at an OKF directory.",
            style="hf.warn",
        )
        return CONTINUE

    bundle = knowledge_module.merge(
        knowledge_module.load(root, "knowledge"),
        knowledge_module.load(app.config.research, "research"),
    )
    if not bundle:
        app.transcript.notice(f"{root} has no readable concepts.", style="hf.warn")
        return CONTINUE

    if args.strip() in {"--graph", "-g", "graph"}:
        return _knowledge_graph(app, bundle)

    hits = bundle.find(args.strip()) if args.strip() else bundle.concepts
    if not hits:
        app.transcript.notice(f"Nothing in the bundle matches {args.strip()!r}.", style="hf.warn")
        return CONTINUE

    table = Table.grid(padding=(0, 3))
    table.add_column(style="hf.accent", no_wrap=True)
    table.add_column(style="hf.muted")
    table.add_column(style="hf.warn", no_wrap=True)
    current = None
    for concept in hits:
        group = concept.type or "untyped"
        if group != current:
            current = group
            table.add_row(Text(group, style="hf.faint"), "", "")
        table.add_row(f"  {concept.name}", concept.description or concept.path, concept.suspect())

    stale = sum(1 for c in hits if c.stale() or c.status == "deprecated")
    app.transcript.print()
    app.transcript.print(table, indent=2)
    summary = f"  {len(hits)} concepts · {len(bundle.edges())} links"
    if stale:
        summary += f" · {stale} stale or deprecated"
    if bundle.unreadable:
        summary += f" · {len(bundle.unreadable)} unreadable"
    app.transcript.print(Text(summary + "  ·  /knowledge --graph for the node view", style="hf.faint"))
    app.transcript.print()
    return CONTINUE


def _knowledge_graph(app: "Hubbleflow", bundle) -> str:
    from hubbleflow.ui import graph as graph_module

    destination = app.config.workspace / ".hubbleflow" / "knowledge-graph.html"
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        graph_module.write(bundle, destination)
    except OSError as error:
        app.transcript.notice(f"Couldn't write the graph: {error}", style="hf.err")
        return CONTINUE

    # Opening it is a convenience, not the point -- a headless or remote session
    # still gets the file, and says where it is.
    opened = False
    try:
        import subprocess
        import sys

        opener = {"darwin": "open", "win32": "start"}.get(sys.platform, "xdg-open")
        opened = subprocess.run([opener, str(destination)], capture_output=True).returncode == 0
    except (OSError, ValueError):
        opened = False

    app.transcript.notice(
        f"{'Opened' if opened else 'Wrote'} {destination}", style="hf.ok"
    )
    return CONTINUE


async def _cwd(app: "Hubbleflow", args: str) -> str:
    app.transcript.notice(f"{app.config.workspace}  ·  shell at {app.harness.shell.cwd}")
    return CONTINUE


async def _exit(app: "Hubbleflow", args: str) -> str:
    return EXIT


ORDERED: tuple[Command, ...] = (
    Command("/help", "list every slash command", _help),
    Command("/model", "show or switch the model, local or cloud", _model),
    Command("/models", "list every model, local and cloud", _models),
    Command("/local", "list self-hosted models — Ollama and Mesh", _local),
    Command("/mesh", "list models served by the mesh-llm node", _mesh),
    Command("/cloud", "list hosted models — Gemini and NVIDIA", _cloud),
    Command("/skills", "list skills, read one, or scaffold a new one", _skills),
    Command("/knowledge", "browse the OKF knowledge bundle, or graph it", _knowledge),
    Command("/deep-research", "research a question in rounds and file the answer", _deep_research),
    Command("/tools", "show the tools the agent can reach", _tools),
    Command("/mcp", "show connected MCP servers and their tools", _mcp),
    Command("/permissions", "toggle whether writes and commands need approval", _permissions),
    Command("/allowed", "show what you've allowlisted this session", _allowed),
    Command("/cache", "turn context caching on or off, and show what it saved", _cache),
    Command("/usage", "token usage for this session", _usage),
    Command("/cwd", "show the workspace and the shell's directory", _cwd),
    Command("/clear", "start a fresh session, forgetting the conversation", _clear),
    Command("/exit", "leave the harness", _exit),
)

REGISTRY: dict[str, Command] = {c.name: c for c in ORDERED}
REGISTRY["/quit"] = REGISTRY["/exit"]
REGISTRY["/q"] = REGISTRY["/exit"]
