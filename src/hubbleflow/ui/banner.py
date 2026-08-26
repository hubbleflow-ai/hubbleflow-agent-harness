"""The launch screen: wordmark, session card, and starter prompts."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console, Group
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from hubbleflow.ui.theme import BULLET, SPARK

# A 5-row block wordmark. Nine glyphs are 4 cells wide, W is 5, joined by a
# single-cell gutter -> 50 columns, which fits an 80-column terminal.
_GLYPHS: dict[str, tuple[str, ...]] = {
    "H": ("█  █", "█  █", "████", "█  █", "█  █"),
    "U": ("█  █", "█  █", "█  █", "█  █", "████"),
    "B": ("███ ", "█  █", "███ ", "█  █", "███ "),
    "L": ("█   ", "█   ", "█   ", "█   ", "████"),
    "E": ("████", "█   ", "███ ", "█   ", "████"),
    "F": ("████", "█   ", "███ ", "█   ", "█   "),
    "O": ("████", "█  █", "█  █", "█  █", "████"),
    "W": ("█   █", "█   █", "█ █ █", "██ ██", "█   █"),
}

WORDMARK = "HUBBLEFLOW"

STARTERS: tuple[tuple[str, str], ...] = (
    ("explain the structure of this project", "get oriented in an unfamiliar repo"),
    ("find every TODO and summarise them", "sweep the codebase and report back"),
    ("add a test for the config loader", "it reads, edits and runs the suite"),
)

SLASH_HINTS: tuple[tuple[str, str], ...] = (
    ("/help", "list every slash command"),
    ("/model", "switch the Gemini model"),
    ("/tools", "show the tools the agent can reach"),
    ("/exit", "leave the harness"),
)


def render_wordmark(width: int) -> Text:
    """Build the block wordmark, or a compact fallback in a narrow terminal."""
    rows = _wordmark_rows()
    if rows and len(rows[0]) <= max(width - 4, 0):
        text = Text()
        for i, row in enumerate(rows):
            text.append(row, style="hf.brand")
            if i < len(rows) - 1:
                text.append("\n")
        return text
    return Text(f"{SPARK} {WORDMARK}", style="hf.brand")


def _wordmark_rows() -> list[str]:
    letters = [_GLYPHS[ch] for ch in WORDMARK]
    return [" ".join(letter[row] for letter in letters) for row in range(5)]


def render_banner(console: Console, *, model: str, workspace: Path, thread_id: str, resumed: bool) -> None:
    """Print the full launch screen."""
    console.print()
    console.print(Padding(render_wordmark(console.width), (0, 0, 0, 2)))
    console.print()

    session = Table.grid(padding=(0, 2))
    session.add_column(style="hf.muted", justify="right", no_wrap=True)
    session.add_column()
    session.add_row("model", Text(model, style="hf.accent"))
    session.add_row("workspace", Text(_tilde(workspace), style="default"))
    session.add_row("session", Text(f"{thread_id}{' (resumed)' if resumed else ''}", style="hf.muted"))

    header = Text.assemble((f"{SPARK} ", "hf.accent"), ("Welcome to Hubbleflow", "bold"))
    console.print(
        Panel(
            Group(header, Text(""), session),
            border_style="hf.rule",
            padding=(1, 3),
            expand=False,
        )
    )
    console.print()
    _print_hints(console)


def _print_hints(console: Console) -> None:
    console.print(Padding(Text("Try these to get started", style="hf.muted"), (0, 0, 1, 2)))
    tips = Table.grid(padding=(0, 2))
    tips.add_column(style="hf.accent", no_wrap=True)
    tips.add_column(no_wrap=False)
    tips.add_column(style="hf.faint")
    for prompt, why in STARTERS:
        tips.add_row(BULLET, Text(prompt, style="default"), why)
    tips.add_row("", "", "")
    for command, why in SLASH_HINTS:
        tips.add_row(BULLET, Text(command, style="hf.accent"), why)
    console.print(Padding(tips, (0, 0, 1, 2)))
    console.print(
        Padding(Text("Esc+Enter or \\ for a newline  ·  Ctrl+C to interrupt  ·  Ctrl+D to exit", style="hf.faint"), (0, 0, 1, 2))
    )


def _tilde(path: Path) -> str:
    try:
        return f"~/{path.relative_to(Path.home())}"
    except ValueError:
        return str(path)
