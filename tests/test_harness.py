"""End-to-end checks over the real graph, the real renderer, the real policy."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from rich.console import Console

from hubbleflow import agent as agent_module
from hubbleflow.app import Hubbleflow
from hubbleflow.config import Config
from hubbleflow.permissions import PermissionPolicy
from hubbleflow.ui import approve
from hubbleflow.ui.render import Transcript
from hubbleflow.ui.theme import THEME

from .conftest import ScriptedModel


def _tool_call(name: str, args: dict, call_id: str = "c1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


async def _drive(workspace, script, *, monkeypatch, auto_approve=False, answer=None) -> str:
    """Run one turn against a scripted model and return what hit the terminal."""
    console = Console(theme=THEME, width=100, record=True, force_terminal=False)
    config = Config.load(workspace=workspace, auto_approve=auto_approve)

    app = Hubbleflow.__new__(Hubbleflow)  # skip the composer, which needs a tty
    app.config = config
    app.console = console
    app.transcript = Transcript(console)
    app._checkpointer = None

    if answer is not None:
        monkeypatch.setattr(approve.ApprovalPrompt, "ask", _constant(answer))
        monkeypatch.setattr(Hubbleflow, "_ask_reason", _constant(""))

    async with AsyncSqliteSaver.from_conn_string(":memory:") as checkpointer:
        app._checkpointer = checkpointer
        app.harness = await agent_module.build(
            config, checkpointer, PermissionPolicy(auto_approve=auto_approve), model=ScriptedModel(script=script)
        )
        await app.turn("go")

    return console.export_text()


def _constant(value):
    async def _fn(*args, **kwargs):
        return value

    return _fn


async def test_read_only_command_runs_without_asking(workspace, monkeypatch):
    script = [_tool_call("bash", {"command": "cat hello.txt"}), AIMessage(content="The file says hello.")]
    output = await _drive(workspace, script, monkeypatch=monkeypatch)

    assert "Bash(cat hello.txt)" in output
    assert "hello from the workspace" in output
    assert "The file says hello." in output


async def test_write_pauses_for_approval_and_proceeds_on_yes(workspace, monkeypatch):
    script = [
        _tool_call("write_file", {"file_path": "notes.md", "content": "# notes\n"}),
        AIMessage(content="Created notes.md."),
    ]
    output = await _drive(workspace, script, monkeypatch=monkeypatch, answer=approve.APPROVE)

    assert "Write(notes.md)" in output
    assert (workspace / "notes.md").read_text() == "# notes\n"
    assert "Created notes.md." in output


async def test_declining_stops_the_write(workspace, monkeypatch):
    script = [
        _tool_call("write_file", {"file_path": "notes.md", "content": "# notes\n"}),
        AIMessage(content="Understood, leaving it alone."),
    ]
    output = await _drive(workspace, script, monkeypatch=monkeypatch, answer=approve.REJECT)

    assert not (workspace / "notes.md").exists()
    assert "declined" in output


async def test_always_allow_adds_a_session_allowlist_entry(workspace, monkeypatch):
    console = Console(theme=THEME, width=100, record=True)
    config = Config.load(workspace=workspace)
    policy = PermissionPolicy()

    app = Hubbleflow.__new__(Hubbleflow)
    app.config, app.console, app.transcript = config, console, Transcript(console)
    async with AsyncSqliteSaver.from_conn_string(":memory:") as checkpointer:
        app.harness = await agent_module.build(config, checkpointer, policy, model=ScriptedModel(script=[AIMessage(content="hi")]))
        monkeypatch.setattr(approve.ApprovalPrompt, "ask", _constant(approve.ALWAYS))
        decision = await app._ask_one({"name": "bash", "args": {"command": "git commit -m wip"}})

    assert decision == {"type": "approve"}
    assert "git commit" in policy.always_allow_commands
    assert not policy.needs_review("bash", {"command": "git commit -m later"})


async def test_yolo_skips_approval_entirely(workspace, monkeypatch):
    script = [_tool_call("write_file", {"file_path": "auto.md", "content": "auto\n"}), AIMessage(content="done")]
    await _drive(workspace, script, monkeypatch=monkeypatch, auto_approve=True)

    assert (workspace / "auto.md").read_text() == "auto\n"
