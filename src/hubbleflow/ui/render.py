"""Turning agent events into terminal output.

The transcript is append-only -- it lands in the terminal's own scrollback the
way ordinary command output does -- while a single Rich `Live` region at the
bottom holds whatever is still in flight.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.markdown import Markdown
from rich.padding import Padding
from rich.spinner import Spinner
from rich.text import Text

from hubbleflow.ui.theme import CROSS, DOT, ELBOW, SPARK

RESULT_LINES = 6
RESULT_WIDTH = 140
REFRESH_HZ = 12

_TODO_MARKS = {"completed": "✓", "in_progress": "◐", "pending": "○"}
_TODO_STYLES = {"completed": "hf.faint", "in_progress": "hf.accent", "pending": "hf.muted"}


# --------------------------------------------------------------------------
# tool call formatting
# --------------------------------------------------------------------------

def format_tool_call(name: str, args: dict) -> Text:
    """A one-line `Read(src/app.py)` header for a tool call."""
    label, detail = _label_and_detail(name, args)
    text = Text()
    text.append(label, style="hf.tool")
    if detail:
        text.append("(", style="hf.faint")
        text.append(_ellipsize(detail, 110), style="hf.toolargs")
        text.append(")", style="hf.faint")
    return text


def _label_and_detail(name: str, args: dict) -> tuple[str, str]:
    a = args or {}
    match name:
        case "bash" | "execute":
            return "Bash", str(a.get("command", ""))
        case "read_file":
            return "Read", _path(a)
        case "write_file":
            return "Write", _path(a)
        case "edit_file":
            return "Edit", _path(a)
        case "delete":
            return "Delete", _path(a)
        case "ls":
            return "List", _path(a) or "."
        case "glob":
            return "Glob", str(a.get("pattern", ""))
        case "grep":
            pattern = str(a.get("pattern", ""))
            where = _path(a)
            return "Grep", f"{pattern} in {where}" if where else pattern
        case "web_search":
            return "Search", str(a.get("query", ""))
        case "web_fetch":
            return "Fetch", str(a.get("url", ""))
        case "task":
            sub = a.get("subagent_type") or a.get("name") or "agent"
            return "Task", f"{sub}: {a.get('description') or a.get('prompt', '')}"
        case "write_todos":
            return "TodoWrite", f"{len(a.get('todos') or [])} items"
        case _:
            return name, _compact_args(a)


def _path(args: dict) -> str:
    for key in ("file_path", "path", "filename", "dir_path"):
        if value := args.get(key):
            return str(value)
    return ""


def _compact_args(args: dict) -> str:
    if not args:
        return ""
    try:
        return json.dumps(args, default=str)[1:-1]
    except (TypeError, ValueError):
        return str(args)


# --------------------------------------------------------------------------
# tool result formatting
# --------------------------------------------------------------------------

def format_tool_result(name: str, content: str, *, failed: bool = False) -> RenderableType:
    """The `└─ ...` block hanging off a tool call."""
    if name == "write_todos":
        return _todo_block(content)

    body = (content or "").strip()
    if not body:
        body = "(no output)"

    lines = body.splitlines()
    shown = [_ellipsize(line, RESULT_WIDTH) for line in lines[:RESULT_LINES]]
    if len(lines) > RESULT_LINES:
        shown.append(f"… +{len(lines) - RESULT_LINES} more lines")

    style = "hf.err" if failed else "hf.result"
    text = Text()
    for i, line in enumerate(shown):
        text.append(f"{ELBOW} " if i == 0 else "   ", style="hf.faint")
        text.append(line, style=style)
        if i < len(shown) - 1:
            text.append("\n")
    return text


def _todo_block(content: str) -> RenderableType:
    """Render the agent's todo list as a checklist rather than raw JSON."""
    todos = _extract_todos(content)
    if not todos:
        return Text(f"{ELBOW} updated", style="hf.faint")

    text = Text()
    for i, todo in enumerate(todos):
        status = str(todo.get("status", "pending"))
        label = str(todo.get("content") or todo.get("subject") or "")
        text.append(f"{ELBOW} " if i == 0 else "   ", style="hf.faint")
        text.append(f"{_TODO_MARKS.get(status, '○')} ", style=_TODO_STYLES.get(status, "hf.muted"))
        text.append(label, style="hf.faint" if status == "completed" else "hf.result")
        if i < len(todos) - 1:
            text.append("\n")
    return text


def _extract_todos(content: str) -> list[dict]:
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return []
    if isinstance(payload, dict):
        payload = payload.get("todos", [])
    return [t for t in payload if isinstance(t, dict)] if isinstance(payload, list) else []


def _ellipsize(value: str, limit: int) -> str:
    value = " ".join(value.split()) if "\n" in value else value
    return value if len(value) <= limit else value[: limit - 1] + "…"


# --------------------------------------------------------------------------
# the live view
# --------------------------------------------------------------------------

@dataclass(slots=True)
class Usage:
    """Token counters for the status line."""

    sent: int = 0
    received: int = 0
    cached: int = 0

    def add(self, metadata: dict | None) -> None:
        if not metadata:
            return
        self.sent += int(metadata.get("input_tokens") or 0)
        self.received += int(metadata.get("output_tokens") or 0)
        details = metadata.get("input_token_details") or {}
        self.cached += int(details.get("cache_read") or 0)

    @property
    def fresh(self) -> int:
        """Input tokens billed at the full rate."""
        return max(self.sent - self.cached, 0)


class Transcript:
    """Owns the console. Prints finished output, animates what's in flight."""

    def __init__(self, console: Console) -> None:
        self.console = console
        self._live: Live | None = None
        self._spinner: Spinner | None = None
        self._buffer = ""
        self._status = "Working"
        self._started = 0.0
        self.usage = Usage()

    # -- permanent output ---------------------------------------------------

    def print(self, renderable: RenderableType = "", *, indent: int = 0) -> None:
        """Write to the transcript, above the live region if one is running.

        Any assistant prose still in the live buffer is settled into scrollback
        first, so a tool card never jumps above the sentence that introduced it.
        """
        if self._buffer.strip():
            self._settle()
        target = self._live.console if self._live else self.console
        target.print(Padding(renderable, (0, 0, 0, indent)) if indent else renderable)

    def rule(self) -> None:
        self.print()

    def user_echo(self, prompt: str) -> None:
        text = Text()
        text.append("› ", style="hf.accent")
        text.append(prompt, style="hf.user")
        self.print()
        # Styling the Padding rather than the Text fills the whole line, so a
        # wrapped or multi-line prompt reads as one block instead of a ragged
        # highlight that stops wherever each line happens to end.
        self.print(Padding(text, (0, 1), style="hf.userline"))
        self.print()

    def tool_call(self, name: str, args: dict) -> None:
        header = Text(f"{DOT} ", style="hf.accent")
        header.append_text(format_tool_call(name, args))
        self.print(header)

    def tool_result(self, name: str, content: str, *, failed: bool = False) -> None:
        self.print(format_tool_result(name, content, failed=failed), indent=2)
        self.print()

    def notice(self, message: str, style: str = "hf.muted") -> None:
        self.print(Text(f"  {message}", style=style))

    def error(self, message: str) -> None:
        self.print()
        self.print(Text(f"{CROSS} {message}", style="hf.err"))
        self.print()

    # -- the live region ----------------------------------------------------

    def start(self, status: str = "Working") -> None:
        if self._live is not None:
            self.set_status(status)
            return
        self._buffer = ""
        self._status = status
        self._started = time.monotonic()
        # One Spinner for the life of the region. `Spinner.render` stamps its
        # start time on first render, so rebuilding it every frame -- which
        # `get_renderable` would otherwise do -- pins it to frame zero forever.
        self._spinner = Spinner("dots", style="hf.accent")
        # `get_renderable` rather than a fixed frame: Rich re-invokes it on every
        # refresh, which is what makes the elapsed time actually count up.
        self._live = Live(
            get_renderable=self._frame,
            console=self.console,
            refresh_per_second=REFRESH_HZ,
            vertical_overflow="visible",
            transient=False,
        )
        self._live.start()

    def set_status(self, status: str) -> None:
        self._status = status
        self._refresh()

    def append(self, chunk: str) -> None:
        """Add streamed assistant text to the in-flight message."""
        if not chunk:
            return
        if self._live is None:
            self.start(self._status or "Working")
        self._buffer += chunk
        self._refresh()

    def flush(self) -> None:
        """End the live region entirely, leaving the last frame in scrollback."""
        if self._live is None:
            return
        status, self._status = self._status, ""
        self._live.refresh()
        self._live.stop()
        self._live = None
        self._spinner = None
        if self._buffer.strip():
            self.console.print()
        self._buffer = ""
        self._status = status

    def _settle(self) -> None:
        """Commit the prose written so far, then resume spinning underneath it."""
        status = self._status
        self.flush()
        self.start(status or "Working")

    def stop(self) -> None:
        self.flush()

    def _refresh(self) -> None:
        if self._live is not None:
            self._live.refresh()

    def _frame(self) -> RenderableType:
        parts: list[RenderableType] = []
        if self._buffer.strip():
            parts.append(Padding(Markdown(self._buffer), (0, 0, 0, 2)))
        if self._status:
            parts.append(Padding(self._status_line(), (1 if parts else 0, 0, 0, 2)))
        return Group(*parts) if parts else Text("")

    def _status_line(self) -> RenderableType:
        elapsed = int(time.monotonic() - self._started)
        meta = f"{elapsed}s"
        if self.usage.received:
            meta += f" · {_thousands(self.usage.received)} tokens"
        meta += " · ctrl+c to interrupt"
        label = Text()
        label.append(f" {self._status}… ", style="hf.accent")
        label.append(f"({meta})", style="hf.faint")

        if self._spinner is None:
            return label
        self._spinner.update(text=label)
        return self._spinner


def _thousands(value: int) -> str:
    return f"{value / 1000:.1f}k" if value >= 1000 else str(value)


def brand_line(message: str) -> Text:
    text = Text()
    text.append(f"{SPARK} ", style="hf.accent")
    text.append(message, style="hf.muted")
    return text
