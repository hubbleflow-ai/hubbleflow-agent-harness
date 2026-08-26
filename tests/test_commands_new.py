"""/skills and /knowledge, driven against a transcript that just records."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from hubbleflow import commands
from hubbleflow.config import Config


class _Transcript:
    """Captures what would have been printed, as plain text."""

    def __init__(self):
        self.lines: list[str] = []

    def notice(self, message, style=""):
        self.lines.append(str(message))

    def print(self, renderable="", indent=0):
        import io

        from rich.console import Console

        from hubbleflow.ui.theme import THEME

        if renderable == "":
            return
        # The real transcript's console carries the theme; without it the hf.*
        # style names don't resolve and every render raises.
        console = Console(width=200, file=io.StringIO(), record=True, theme=THEME)
        console.print(renderable)
        self.lines.append(console.export_text())

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@dataclass
class _App:
    config: Config
    transcript: _Transcript = field(default_factory=_Transcript)


def _app(workspace, **overrides):
    base = dict(
        model="ollama:x", workspace=workspace, session_db=workspace / "s", thread_id="t",
        resumed=False, auto_approve=False, cache=False, virtual_mode=True,
        global_skills=None, project_skills=None, knowledge=None,
    )
    return _App(config=Config(**{**base, **overrides}))


def _skill(root, name, description="does a thing"):
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\nbody\n")
    return path


# --------------------------------------------------------------------------
# /skills
# --------------------------------------------------------------------------

async def test_no_skills_says_so_and_points_at_new(tmp_path):
    app = _app(tmp_path)
    await commands._skills(app, "")
    assert "/skills new" in app.transcript.text


async def test_skills_are_listed_with_their_descriptions(tmp_path):
    root = tmp_path / "global"
    _skill(root, "web-research", "search before you guess")
    app = _app(tmp_path, global_skills=root)
    await commands._skills(app, "")
    assert "web-research" in app.transcript.text
    assert "search before you guess" in app.transcript.text


async def test_a_project_skill_is_labelled_separately(tmp_path):
    project = tmp_path / ".hubbleflow" / "skills"
    _skill(project, "house-style")
    app = _app(tmp_path, project_skills=project)
    await commands._skills(app, "")
    assert "project" in app.transcript.text


async def test_a_skill_can_be_read(tmp_path):
    root = tmp_path / "global"
    _skill(root, "web-research")
    app = _app(tmp_path, global_skills=root)
    await commands._skills(app, "web-research")
    assert "# web-research" in app.transcript.text


async def test_reading_an_unknown_skill_says_so(tmp_path):
    app = _app(tmp_path)
    await commands._skills(app, "nope")
    assert "No skill named nope" in app.transcript.text


async def test_new_scaffolds_a_project_skill(tmp_path):
    app = _app(tmp_path)
    await commands._skills(app, "new deploy-checks")
    written = tmp_path / ".hubbleflow" / "skills" / "deploy-checks" / "SKILL.md"
    assert written.is_file()
    assert "name: deploy-checks" in written.read_text()


async def test_new_refuses_to_overwrite(tmp_path):
    app = _app(tmp_path)
    await commands._skills(app, "new thing")
    await commands._skills(app, "new thing")
    assert "already exists" in app.transcript.text


@pytest.mark.parametrize("name", ["", "../escape", ".hidden", "a/b"])
async def test_new_rejects_a_name_that_is_not_one(tmp_path, name):
    app = _app(tmp_path)
    await commands._skills(app, f"new {name}")
    assert "Usage:" in app.transcript.text


async def test_a_broken_skill_still_appears_in_the_listing(tmp_path):
    root = tmp_path / "global"
    path = root / "broken" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\nname: [unclosed\n---\nbody")
    app = _app(tmp_path, global_skills=root)
    await commands._skills(app, "")
    assert "broken" in app.transcript.text


# --------------------------------------------------------------------------
# /knowledge
# --------------------------------------------------------------------------

def _bundle(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / "orders.md").write_text("---\ntype: BigQuery Table\ntitle: Orders\n"
                                    "description: One row per order.\ntags: [sales]\n---\nbody")
    (root / "restart.md").write_text("---\ntype: Runbook\ntitle: Restart\nstatus: deprecated\n---\nbody")
    return root


async def test_no_bundle_explains_how_to_get_one(tmp_path):
    app = _app(tmp_path)
    await commands._knowledge(app, "")
    assert "OpenWiki" in app.transcript.text or "HUBBLEFLOW_KNOWLEDGE" in app.transcript.text


async def test_concepts_are_grouped_by_type(tmp_path):
    app = _app(tmp_path, knowledge=_bundle(tmp_path / "openwiki"))
    await commands._knowledge(app, "")
    text = app.transcript.text
    assert "BigQuery Table" in text and "Runbook" in text and "Orders" in text


async def test_a_deprecated_page_is_flagged_in_the_listing(tmp_path):
    app = _app(tmp_path, knowledge=_bundle(tmp_path / "openwiki"))
    await commands._knowledge(app, "")
    assert "deprecated" in app.transcript.text


async def test_an_argument_filters(tmp_path):
    app = _app(tmp_path, knowledge=_bundle(tmp_path / "openwiki"))
    await commands._knowledge(app, "orders")
    assert "Orders" in app.transcript.text and "Restart" not in app.transcript.text


async def test_a_query_matching_nothing_says_so(tmp_path):
    app = _app(tmp_path, knowledge=_bundle(tmp_path / "openwiki"))
    await commands._knowledge(app, "zzzz")
    assert "matches" in app.transcript.text


async def test_an_empty_bundle_directory_is_reported(tmp_path):
    empty = tmp_path / "openwiki"
    empty.mkdir()
    app = _app(tmp_path, knowledge=empty)
    await commands._knowledge(app, "")
    assert "no readable concepts" in app.transcript.text
