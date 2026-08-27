"""What /usage says about the window, and what errors say when it's exceeded."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from hubbleflow import commands
from hubbleflow import config as config_module
from hubbleflow.agent import _summarize_after
from hubbleflow.app import _explain
from hubbleflow.config import FREETOKEN, GEMINI, MESH, NVIDIA, OLLAMA, Config

SUMMARY = "Here is a summary of the conversation to date:\n\nyou asked about things"


class _Graph:
    def __init__(self, messages, fail=False):
        self._messages, self._fail = messages, fail

    async def aget_state(self, config):
        if self._fail:
            raise RuntimeError("checkpointer unavailable")
        return SimpleNamespace(values={"messages": self._messages})


@dataclass
class _App:
    config: Config
    harness: object


def _app(messages, model="ollama:gemma4:12b", fail=False, tmp_path=None):
    workspace = tmp_path or Path(".")
    config = Config(
        model=model, workspace=workspace, session_db=workspace / "s", thread_id="t",
        resumed=False, auto_approve=False, cache=False, virtual_mode=True,
        global_skills=None, project_skills=None, knowledge=None,
    )
    return _App(config=config, harness=SimpleNamespace(graph=_Graph(messages, fail)))


# --------------------------------------------------------------------------
# the window line
# --------------------------------------------------------------------------

async def test_an_empty_conversation_shows_nothing(tmp_path):
    assert await commands._window_line(_app([], tmp_path=tmp_path)) is None


async def test_a_short_conversation_reports_its_size_and_the_trigger(tmp_path):
    line = await commands._window_line(_app([HumanMessage("hi"), AIMessage("hello")], tmp_path=tmp_path))
    text = line.plain
    assert "before compaction" in text
    assert f"{_summarize_after(_app([], tmp_path=tmp_path).config):,}" in text


async def test_an_uncompacted_session_says_so(tmp_path):
    line = await commands._window_line(_app([HumanMessage("hi")], tmp_path=tmp_path))
    assert "not yet compacted" in line.plain


async def test_a_compacted_session_is_counted(tmp_path):
    messages = [HumanMessage(SUMMARY), AIMessage("ok"), HumanMessage(SUMMARY), AIMessage("ok")]
    assert "compacted 2×" in (await commands._window_line(_app(messages, tmp_path=tmp_path))).plain


async def test_a_message_merely_mentioning_a_summary_is_not_counted(tmp_path):
    messages = [HumanMessage("can you give me a summary of the conversation to date?")]
    assert "not yet compacted" in (await commands._window_line(_app(messages, tmp_path=tmp_path))).plain


async def test_an_unreadable_checkpointer_hides_the_line_rather_than_failing(tmp_path):
    assert await commands._window_line(_app([HumanMessage("hi")], fail=True, tmp_path=tmp_path)) is None


async def test_list_content_is_measured_not_crashed_on(tmp_path):
    """Multimodal turns carry a list of blocks rather than a string."""
    line = await commands._window_line(_app([HumanMessage([{"type": "text", "text": "x" * 400}])], tmp_path=tmp_path))
    assert line is not None and "before compaction" in line.plain


# --------------------------------------------------------------------------
# provider-aware compaction
# --------------------------------------------------------------------------

def test_gemini_keeps_the_large_trigger(tmp_path):
    assert config_module.compact_after(GEMINI) == config_module.DEFAULT_COMPACT_AFTER


def test_nvidia_assumes_the_common_window_not_the_best_one(tmp_path):
    """NVIDIA's /models advertises no context length, and most NIM endpoints
    are 128k -- so 256k would overflow before compaction ever ran."""
    assert config_module.compact_after(NVIDIA) < config_module.compact_after(GEMINI)
    assert config_module.compact_after(NVIDIA) < 128_000


def test_the_override_wins_for_every_provider(monkeypatch):
    monkeypatch.setenv("HUBBLEFLOW_COMPACT_AFTER", "111000")
    assert config_module.compact_after(GEMINI) == 111_000
    assert config_module.compact_after(NVIDIA) == 111_000


# --------------------------------------------------------------------------
# the error message
# --------------------------------------------------------------------------

_OVERFLOW = Exception(
    '{"error":{"code":400,"message":"request (5254 tokens) exceeds the available '
    'context size (4096 tokens)","type":"exceed_context_size_error"}}'
)


@pytest.mark.parametrize("provider", [OLLAMA, MESH, FREETOKEN])
def test_a_local_overflow_points_at_the_window(provider):
    assert "HUBBLEFLOW_NUM_CTX" in _explain(_OVERFLOW, provider)


@pytest.mark.parametrize("provider", [GEMINI, NVIDIA])
def test_a_hosted_overflow_points_at_compaction(provider):
    """A hosted window can't be raised, so the fix is to summarise sooner."""
    explained = _explain(_OVERFLOW, provider)
    assert "HUBBLEFLOW_COMPACT_AFTER" in explained
    assert "HUBBLEFLOW_NUM_CTX" not in explained


def test_the_original_error_is_kept(tmp_path):
    assert "exceed_context_size_error" in _explain(_OVERFLOW, OLLAMA)


def test_an_unrelated_error_is_left_alone():
    assert _explain(Exception("connection reset by peer"), OLLAMA) == "connection reset by peer"
