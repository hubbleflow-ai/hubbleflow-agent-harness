"""Project context files, and compaction sized to the window the model has."""

from __future__ import annotations

import pytest

from hubbleflow import config as config_module
from hubbleflow.agent import _project_context, _summarize_after
from hubbleflow.config import Config


def _config(workspace, model="ollama:qwen3:8b"):
    return Config(
        model=model, workspace=workspace, session_db=workspace / "s.sqlite",
        thread_id="t", resumed=False, auto_approve=False, cache=False,
        virtual_mode=True, global_skills=None, project_skills=None,
    )


# --------------------------------------------------------------------------
# context files
# --------------------------------------------------------------------------

def test_a_workspace_without_one_contributes_nothing(tmp_path):
    assert _project_context(_config(tmp_path)) == ""


def test_the_file_is_appended_with_its_name(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Run make check before pushing.")
    context = _project_context(_config(tmp_path))
    assert "# AGENTS.md" in context
    assert "Run make check before pushing." in context


@pytest.mark.parametrize(
    ("present", "expected"),
    [
        (("CLAUDE.md",), "CLAUDE.md"),
        (("AGENTS.md", "CLAUDE.md"), "AGENTS.md"),
        (("HUBBLEFLOW.md", "AGENTS.md", "CLAUDE.md"), "HUBBLEFLOW.md"),
    ],
)
def test_the_most_specific_file_wins(tmp_path, present, expected):
    for name in present:
        (tmp_path / name).write_text(f"from {name}")
    assert f"# {expected}" in _project_context(_config(tmp_path))


def test_only_one_file_is_read(tmp_path):
    """OpenWiki writes the same block into both; concatenating would repeat it."""
    (tmp_path / "AGENTS.md").write_text("shared block")
    (tmp_path / "CLAUDE.md").write_text("shared block")
    assert _project_context(_config(tmp_path)).count("shared block") == 1


def test_an_empty_file_falls_through_to_the_next(tmp_path):
    (tmp_path / "AGENTS.md").write_text("   \n")
    (tmp_path / "CLAUDE.md").write_text("real guidance")
    assert "# CLAUDE.md" in _project_context(_config(tmp_path))


def test_an_oversized_file_is_truncated_not_dropped(tmp_path):
    (tmp_path / "AGENTS.md").write_text("x" * (config_module.MAX_CONTEXT_CHARS + 5_000))
    context = _project_context(_config(tmp_path))
    assert "[...truncated]" in context
    assert len(context) < config_module.MAX_CONTEXT_CHARS + 1_000


def test_an_unreadable_file_does_not_stop_the_session(tmp_path):
    (tmp_path / "AGENTS.md").write_bytes(b"\xff\xfe\x00binary")
    (tmp_path / "CLAUDE.md").write_text("readable")
    assert "# CLAUDE.md" in _project_context(_config(tmp_path))


def test_the_lookup_can_be_turned_off(tmp_path, monkeypatch):
    (tmp_path / "AGENTS.md").write_text("someone else's harness")
    monkeypatch.setenv("HUBBLEFLOW_CONTEXT_FILES", "")
    assert _project_context(_config(tmp_path)) == ""


def test_the_filenames_can_be_replaced(tmp_path, monkeypatch):
    (tmp_path / "NOTES.md").write_text("house rules")
    monkeypatch.setenv("HUBBLEFLOW_CONTEXT_FILES", "NOTES.md")
    assert "# NOTES.md" in _project_context(_config(tmp_path))


# --------------------------------------------------------------------------
# compaction
# --------------------------------------------------------------------------

def test_a_cloud_session_keeps_the_large_trigger(tmp_path):
    assert _summarize_after(_config(tmp_path, "google_genai:gemini-3.5-flash")) == config_module.DEFAULT_COMPACT_AFTER


def test_a_cloud_session_does_not_compact_before_256k(tmp_path):
    """A hosted window is hundreds of thousands of tokens; compacting earlier
    discards context that was already paid for and still fits."""
    assert _summarize_after(_config(tmp_path, "google_genai:gemini-3.5-flash")) >= 256_000


def test_the_cloud_trigger_is_configurable(tmp_path, monkeypatch):
    monkeypatch.setenv("HUBBLEFLOW_COMPACT_AFTER", "400000")
    assert _summarize_after(_config(tmp_path, "google_genai:gemini-3.5-flash")) == 400_000


def test_a_local_session_ignores_the_cloud_trigger(tmp_path, monkeypatch):
    monkeypatch.setenv("HUBBLEFLOW_COMPACT_AFTER", "400000")
    assert _summarize_after(_config(tmp_path)) < config_module.context_window()


def test_compacting_leaves_room_for_the_summarisation_call(tmp_path):
    """Compaction resends the transcript and writes a summary, so the trigger
    has to clear the window by the prompt and the output as well."""
    from hubbleflow.agent import _PROMPT_OVERHEAD_TOKENS

    window = config_module.context_window()
    trigger = _summarize_after(_config(tmp_path))
    assert trigger + _PROMPT_OVERHEAD_TOKENS + config_module.max_output_tokens() <= window


@pytest.mark.parametrize("model", ["ollama:qwen3:8b", "mesh:GLM-4.7-Flash"])
def test_a_local_session_compacts_inside_its_window(tmp_path, model):
    """170k can never be reached in a 32k window -- the request dies first."""
    trigger = _summarize_after(_config(tmp_path, model))
    assert trigger < config_module.context_window()
    assert trigger > 0


def test_the_local_trigger_follows_the_configured_window(tmp_path, monkeypatch):
    monkeypatch.setenv("HUBBLEFLOW_NUM_CTX", "8192")
    assert _summarize_after(_config(tmp_path)) < 8192
