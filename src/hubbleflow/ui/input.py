"""The composer: a bordered, growing input box pinned below the transcript.

Built as a non-fullscreen prompt_toolkit `Application` so it redraws in place
while the transcript above it stays in the terminal's real scrollback.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from pathlib import Path

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import Completer, Completion, PathCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import (
    ConditionalContainer,
    Float,
    FloatContainer,
    HSplit,
    Layout,
    VSplit,
    Window,
)
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.lexers import SimpleLexer
from prompt_toolkit.styles import Style

from hubbleflow.ui.theme import ACCENT, FAINT, MUTED, SURFACE

PROMPT_MARK = "› "

STYLE = Style.from_dict(
    {
        "box": FAINT,
        "mark": f"{ACCENT} bold",
        "input": "",
        "hint": FAINT,
        "placeholder": FAINT,
        "completion-menu": f"bg:{SURFACE} {MUTED}",
        "completion-menu.completion": f"bg:{SURFACE} {MUTED}",
        "completion-menu.completion.current": f"bg:{ACCENT} #10131A bold",
        "completion-menu.meta.completion": f"bg:{SURFACE} {FAINT}",
        "completion-menu.meta.completion.current": f"bg:{ACCENT} #10131A",
        "scrollbar.background": f"bg:{SURFACE}",
        "scrollbar.button": f"bg:{FAINT}",
    }
)


class ComposerCompleter(Completer):
    """Completes `/commands` at the start of a line and `@paths` anywhere."""

    def __init__(self, commands: Callable[[], Iterable[tuple[str, str]]]) -> None:
        self._commands = commands
        self._paths = PathCompleter(expanduser=True)

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor
        if text.startswith("/") and "\n" not in text and " " not in text:
            yield from self._slash(text)
            return
        head, at, fragment = text.rpartition("@")
        if at and "\n" not in fragment and " " not in fragment:
            sub = Document(fragment, len(fragment))
            for completion in self._paths.get_completions(sub, complete_event):
                yield completion

    def _slash(self, text: str):
        for name, description in self._commands():
            if name.startswith(text):
                yield Completion(name, start_position=-len(text), display=name, display_meta=description)


class Composer:
    """One prompt, awaited once per turn."""

    def __init__(
        self,
        *,
        commands: Callable[[], Iterable[tuple[str, str]]],
        hint: Callable[[], str],
        history_file: Path,
    ) -> None:
        history_file.parent.mkdir(parents=True, exist_ok=True)
        self.buffer = Buffer(
            multiline=True,
            history=FileHistory(str(history_file)),
            completer=ComposerCompleter(commands),
            complete_while_typing=True,
            accept_handler=self._accept,
        )
        self._hint = hint
        self._app = self._build_app()

    # -- public API ---------------------------------------------------------

    async def ask(self) -> str:
        """Show the composer and return what the user typed. Raises on ctrl+c/d."""
        self.buffer.reset()
        return await self._app.run_async()

    # -- layout -------------------------------------------------------------

    def _build_app(self) -> Application:
        control = BufferControl(
            buffer=self.buffer,
            lexer=SimpleLexer("class:input"),
            include_default_input_processors=True,
        )
        editor = Window(control, wrap_lines=True, dont_extend_height=True, style="class:input")

        def fill(char: str, width=None):
            return Window(char=char, width=width, height=1, style="class:box") if width else Window(
                char=char, height=1, style="class:box"
            )

        body = VSplit(
            [
                Window(width=1, char="│", style="class:box"),
                Window(FormattedTextControl(lambda: [("class:mark", f" {PROMPT_MARK}")]), width=4, dont_extend_height=True),
                editor,
                Window(width=1, char=" ", style="class:box"),
                Window(width=1, char="│", style="class:box"),
            ]
        )

        box = HSplit(
            [
                VSplit([fill("╭", 1), fill("─"), fill("╮", 1)], height=1),
                body,
                VSplit([fill("╰", 1), fill("─"), fill("╯", 1)], height=1),
            ]
        )

        hint = ConditionalContainer(
            Window(
                FormattedTextControl(lambda: [("class:hint", f"  {self._hint()}")]),
                height=1,
                dont_extend_height=True,
            ),
            filter=Condition(lambda: bool(self._hint())),
        )

        root = FloatContainer(
            HSplit([box, hint]),
            floats=[
                Float(
                    xcursor=True,
                    ycursor=True,
                    content=CompletionsMenu(max_height=8, scroll_offset=1),
                )
            ],
        )

        return Application(
            layout=Layout(root, focused_element=editor),
            key_bindings=self._key_bindings(),
            style=STYLE,
            full_screen=False,
            erase_when_done=True,
            mouse_support=False,
        )

    # -- keys ---------------------------------------------------------------

    def _key_bindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("enter")
        def _submit(event) -> None:
            buf = event.current_buffer
            # While the completion menu is open, enter picks the highlighted item.
            if buf.complete_state and buf.complete_state.current_completion:
                buf.apply_completion(buf.complete_state.current_completion)
                return
            # A trailing backslash continues onto the next line, shell-style.
            if buf.document.text_before_cursor.endswith("\\"):
                buf.delete_before_cursor(1)
                buf.insert_text("\n")
                return
            buf.validate_and_handle()

        @kb.add("escape", "enter")
        @kb.add("c-j")
        def _newline(event) -> None:
            event.current_buffer.insert_text("\n")

        @kb.add("c-c")
        def _cancel(event) -> None:
            event.app.exit(exception=KeyboardInterrupt, style="class:exiting")

        @kb.add("c-d")
        def _eof(event) -> None:
            if event.current_buffer.text:
                event.current_buffer.delete()
                return
            event.app.exit(exception=EOFError, style="class:exiting")

        @kb.add("escape", eager=True)
        def _dismiss(event) -> None:
            buf = event.current_buffer
            if buf.complete_state:
                buf.cancel_completion()

        return kb

    def _accept(self, buffer: Buffer) -> bool:
        self._app.exit(result=buffer.text)
        return True  # keep the text in history


def supports_truecolor() -> bool:
    """Whether the terminal will honour the 24-bit colours in the theme."""
    return os.getenv("COLORTERM", "").lower() in {"truecolor", "24bit"}
