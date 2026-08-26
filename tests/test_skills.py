"""Global skills live outside the workspace; the routed backend must reach them."""

from __future__ import annotations

from deepagents.backends import CompositeBackend

from hubbleflow import agent
from hubbleflow.config import Config


def test_global_skills_are_routed_not_sandboxed_out(tmp_path, monkeypatch):
    skills = tmp_path / "skills" / "web-research"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("---\nname: web-research\ndescription: x\n---\nbody\n")
    workspace = tmp_path / "work"
    workspace.mkdir()

    monkeypatch.setattr("hubbleflow.config.CONFIG_DIR", tmp_path)
    config = Config.load(workspace=workspace)
    assert config.global_skills == tmp_path / "skills"

    backend, sources = agent._backend(config)
    assert sources == [agent._SKILLS_ROUTE]
    # A plain workspace-rooted backend would refuse the path outright.
    assert isinstance(backend, CompositeBackend)
    assert agent._SKILLS_ROUTE in backend.routes


def test_skills_are_seeded_from_the_wheel_on_first_run(tmp_path, monkeypatch):
    """A plain `pip install` has to get skills without an install script."""
    config_dir = tmp_path / "fresh"
    monkeypatch.setattr("hubbleflow.config.CONFIG_DIR", config_dir)
    workspace = tmp_path / "work"
    workspace.mkdir()

    config = Config.load(workspace=workspace)
    assert config.global_skills == config_dir / "skills"
    assert (config_dir / "skills" / "web-research" / "SKILL.md").is_file()


def test_seeding_never_overwrites_a_skill_you_edited(tmp_path, monkeypatch):
    config_dir = tmp_path / "fresh"
    mine = config_dir / "skills" / "web-research"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("my own version\n")
    monkeypatch.setattr("hubbleflow.config.CONFIG_DIR", config_dir)
    workspace = tmp_path / "work"
    workspace.mkdir()

    Config.load(workspace=workspace)
    assert (mine / "SKILL.md").read_text() == "my own version\n"


def test_no_global_skills_means_no_composite(tmp_path, monkeypatch):
    """Nothing to route means the plain workspace backend, not a composite."""
    workspace = tmp_path / "work"
    workspace.mkdir()
    monkeypatch.setattr("hubbleflow.config.CONFIG_DIR", tmp_path / "empty")
    monkeypatch.setattr("hubbleflow.config._seed_skills", lambda: tmp_path / "empty" / "skills")

    backend, sources = agent._backend(Config.load(workspace=workspace))
    assert sources == []
    assert not isinstance(backend, CompositeBackend)
