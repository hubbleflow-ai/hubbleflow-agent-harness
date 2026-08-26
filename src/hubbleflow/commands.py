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
    model_registry.FREETOKEN: ("FreeToken", "MoE models on your own GPU"),
}

_CLOUD = [model_registry.GEMINI, model_registry.NVIDIA]
_SELF_HOSTED = [model_registry.MESH, model_registry.FREETOKEN, model_registry.OLLAMA]
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
    app.transcript.print(Text(f"  session {app.config.thread_id}", style="hf.faint"))
    app.transcript.print()
    return CONTINUE


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


async def _knowledge(app: "Hubbleflow", args: str) -> str:
    """Show the OKF bundle: a tree by type, a search, or the graph."""
    root = app.config.knowledge
    if root is None:
        app.transcript.notice(
            "No knowledge bundle. Generate one with OpenWiki, or point "
            "HUBBLEFLOW_KNOWLEDGE at an OKF directory.",
            style="hf.warn",
        )
        return CONTINUE

    bundle = knowledge_module.load(root)
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
