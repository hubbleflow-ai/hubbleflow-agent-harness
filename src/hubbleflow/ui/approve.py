"""The permission prompt shown when the agent wants to write or execute."""

from __future__ import annotations

from dataclasses import dataclass

from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style

from hubbleflow.ui.theme import ACCENT, DANGER, FAINT, MUTED

STYLE = Style.from_dict(
    {
        "box": FAINT,
        "title": f"{ACCENT} bold",
        "option": MUTED,
        "option.current": f"{ACCENT} bold",
        "key": FAINT,
        "danger": DANGER,
    }
)


@dataclass(slots=True)
class Choice:
    key: str
    label: str
    value: str


APPROVE = "approve"
ALWAYS = "always"
REJECT = "reject"


def choices_for(tool_name: str, signature: str) -> list[Choice]:
    """The three answers, with the 'always' option naming what it will allow."""
    if tool_name in {"bash", "execute"}:
        scope = f"`{signature}` commands"
    elif tool_name == "web_fetch":
        scope = signature
    else:
        scope = tool_name
    return [
        Choice("1", "Yes", APPROVE),
        Choice("2", f"Yes, and don't ask again for {scope}", ALWAYS),
        Choice("3", "No, tell the agent what to do instead", REJECT),
    ]


class ApprovalPrompt:
    """A small arrow-key menu. Returns one of APPROVE / ALWAYS / REJECT."""

    def __init__(self, title: str, choices: list[Choice]) -> None:
        self.title = title
        self.choices = choices
        self.index = 0
        self._app = self._build()

    async def ask(self) -> str:
        return await self._app.run_async()

    def _build(self) -> Application:
        window = Window(
            FormattedTextControl(self._render, focusable=True),
            height=len(self.choices) + 4,
            dont_extend_height=True,
        )
        return Application(
            layout=Layout(HSplit([window])),
            key_bindings=self._keys(),
            style=STYLE,
            full_screen=False,
            erase_when_done=True,
        )

    def _render(self):
        # `inner` is the width between the two verticals; every row is padded to it.
        inner = max(len(self.title) + 4, max(len(c.label) for c in self.choices) + 10)

        out = [("class:box", "  \u256d"), ("class:box", "\u2500 "), ("class:title", self.title)]
        out += [("class:box", " " + "\u2500" * (inner - len(self.title) - 3) + "\u256e\n")]
        for i, choice in enumerate(self.choices):
            current = i == self.index
            marker = "\u276f" if current else " "
            style = "class:option.current" if current else "class:option"
            out += [
                ("class:box", "  \u2502"),
                (style, f"  {marker} {choice.key}. {choice.label}".ljust(inner)),
                ("class:box", "\u2502\n"),
            ]
        out += [("class:box", "  \u2570" + "\u2500" * inner + "\u256f\n")]
        out += [("class:key", "     \u2191\u2193 to move \u00b7 enter to confirm \u00b7 esc to reject\n")]
        return out

    def _keys(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("up")
        @kb.add("c-p")
        def _up(event) -> None:
            self.index = (self.index - 1) % len(self.choices)

        @kb.add("down")
        @kb.add("c-n")
        @kb.add("tab")
        def _down(event) -> None:
            self.index = (self.index + 1) % len(self.choices)

        @kb.add("enter")
        def _accept(event) -> None:
            event.app.exit(result=self.choices[self.index].value)

        @kb.add("escape", eager=True)
        @kb.add("c-c")
        def _reject(event) -> None:
            event.app.exit(result=REJECT)

        for i, choice in enumerate(self.choices):
            @kb.add(choice.key)
            def _pick(event, _value=choice.value) -> None:
                event.app.exit(result=_value)

        return kb
