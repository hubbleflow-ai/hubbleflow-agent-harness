"""Colour and glyph vocabulary for the harness.

Monochrome with a single neon accent. The greys stay flat so the accent is the
only thing competing with tool output and code, and it is deliberately cool --
far enough round the wheel from DANGER's red that an error still reads as one.
"""

from __future__ import annotations

from rich.theme import Theme

ACCENT = "#00E5FF"
MUTED = "#6E7481"
FAINT = "#4A4F59"
# One step up from the terminal background: the band behind a submitted prompt,
# and the completion menu. Dark enough that the transcript still reads as flat.
SURFACE = "#1B1D22"
SUCCESS = "#5DBE8A"
DANGER = "#E06C6C"
WARN = "#D9A05B"

THEME = Theme(
    {
        "hf.accent": ACCENT,
        "hf.brand": f"bold {ACCENT}",
        "hf.muted": MUTED,
        "hf.faint": FAINT,
        "hf.rule": FAINT,
        "hf.ok": SUCCESS,
        "hf.err": DANGER,
        "hf.warn": WARN,
        "hf.user": "bold #C9CDD6",
        # A band, not a glyph background -- applied to a Padding so it reaches
        # the full width of the terminal rather than stopping at the text.
        "hf.userline": f"on {SURFACE}",
        "hf.tool": "bold #C9CDD6",
        "hf.toolargs": MUTED,
        "hf.result": MUTED,
        "hf.thinking": f"italic {FAINT}",
        # Rich's markdown styles, toned down to match the rest of the shell.
        "markdown.code": ACCENT,
        "markdown.item.bullet": ACCENT,
        "markdown.h1": f"bold {ACCENT}",
        "markdown.h2": f"bold {ACCENT}",
        "markdown.h3": "bold",
    }
)

# Glyphs. Kept ASCII-adjacent so they render in any Nerd-font-less terminal.
DOT = "●"          # ● an agent turn / a tool call
ELBOW = "└─"  # └─ a tool result hanging off its call
BULLET = "›"       # › list bullets in the tips block
SPARK = "✻"        # ✻ the brand mark
CHECK = "✓"
CROSS = "✗"
