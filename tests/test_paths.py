"""The agent sees two path worlds; the backend has to accept both.

File tools are rooted at the workspace, so it is `/`. bash runs on the real
filesystem and prints real paths. The model reads those out of shell output and
feeds them straight back to `read_file`, so a workspace path and the real path
for the same file both have to resolve -- while anything outside the workspace
stays refused.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from hubbleflow.backend import ForgivingFilesystemBackend


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("print('hi')\n")
    (tmp_path / "README.md").write_text("# project\n")
    return tmp_path


@pytest.fixture
def backend(workspace):
    return ForgivingFilesystemBackend(root_dir=workspace, virtual_mode=True)


def test_the_virtual_spelling_works(backend):
    assert backend.read("/README.md").error is None
    assert backend.ls("/app").error is None


def test_the_real_path_works_too(backend, workspace):
    assert backend.read(f"{workspace}/README.md").error is None
    assert backend.ls(f"{workspace}/app").error is None


def test_the_workspace_root_itself_maps_to_slash(backend, workspace):
    listed = backend.ls(str(workspace))
    assert listed.error is None
    assert any(entry["path"] == "/README.md" for entry in listed.entries)


def test_a_tilde_path_is_expanded(backend, workspace, monkeypatch):
    # expanduser() reads HOME on POSIX and USERPROFILE on Windows; set both so
    # this exercises the same code path on either.
    monkeypatch.setenv("HOME", str(workspace.parent))
    monkeypatch.setenv("USERPROFILE", str(workspace.parent))
    assert backend.read(f"~/{workspace.name}/README.md").error is None


def test_a_path_outside_the_workspace_is_still_refused(backend):
    assert backend.read("/etc/passwd").error is not None
    assert backend.ls("/etc").error is not None


def test_traversal_is_still_refused(backend):
    """Traversal raises rather than returning an error result — it never resolves."""
    with pytest.raises(ValueError, match="traversal"):
        backend.read("/../../etc/passwd")


def test_relative_paths_are_untouched(backend):
    assert backend.read("README.md").error is None


def test_hubbleflow_home_moves_the_config_directory(tmp_path, monkeypatch):
    """The install scripts set this, so the app has to honour it."""
    from hubbleflow import config

    monkeypatch.setenv("HUBBLEFLOW_HOME", str(tmp_path / "elsewhere"))
    assert config._config_dir() == tmp_path / "elsewhere"

    monkeypatch.delenv("HUBBLEFLOW_HOME")
    assert config._config_dir() == Path.home() / ".hubbleflow"


def test_a_tilde_in_hubbleflow_home_is_expanded(monkeypatch):
    from hubbleflow import config

    monkeypatch.setenv("HUBBLEFLOW_HOME", "~/somewhere")
    assert config._config_dir() == Path.home() / "somewhere"
