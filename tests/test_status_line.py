"""The working indicator has to animate on two axes at once.

Rich's `Spinner` stamps its start time on first render, so a Spinner rebuilt on
every frame is pinned to frame zero -- while a status line built once never
updates its elapsed time. Getting one right previously broke the other, so both
are pinned here.
"""

from __future__ import annotations

import time

from rich.console import Console

from hubbleflow.ui.render import Transcript
from hubbleflow.ui.theme import THEME


def _frames(transcript: Transcript, count: int, gap: float) -> list[str]:
    console = Console(theme=THEME, width=70)
    rendered = []
    for _ in range(count):
        with console.capture() as capture:
            console.print(transcript._status_line())
        rendered.append(capture.get().rstrip())
        time.sleep(gap)
    return rendered


def _transcript() -> Transcript:
    return Transcript(Console(theme=THEME, force_terminal=True, width=70))


def test_the_spinner_advances_between_frames():
    transcript = _transcript()
    transcript.start("Working")
    try:
        glyphs = {line.strip()[0] for line in _frames(transcript, 6, 0.12)}
    finally:
        transcript.stop()

    assert len(glyphs) > 1, f"spinner frozen on {glyphs}"


def test_the_elapsed_time_counts_up():
    transcript = _transcript()
    transcript.start("Working")
    try:
        first, last = _frames(transcript, 2, 1.15)
    finally:
        transcript.stop()

    assert "(0s" in first
    assert "(0s" not in last, "elapsed time never advanced"


def test_the_spinner_instance_is_held_for_the_life_of_the_region():
    """The actual mechanism: one Spinner, reused, or the animation dies."""
    transcript = _transcript()
    transcript.start("Working")
    try:
        assert transcript._spinner is not None
        assert transcript._status_line() is transcript._spinner
        assert transcript._status_line() is transcript._spinner
    finally:
        transcript.stop()
    assert transcript._spinner is None


def test_the_status_line_is_indented_to_match_the_transcript():
    transcript = _transcript()
    transcript.start("Working")
    try:
        console = Console(theme=THEME, width=70)
        with console.capture() as capture:
            console.print(transcript._frame())
        first = capture.get().splitlines()[0]
    finally:
        transcript.stop()

    assert first.startswith("  "), f"status line hugs the left margin: {first!r}"
