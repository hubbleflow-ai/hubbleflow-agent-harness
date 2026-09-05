"""Topics: research that can be carried forward rather than answered once."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from hubbleflow import commands, knowledge, research
from hubbleflow.config import Config

PAGE = "# Rasuwa toll rises\n\nThe district reported further casualties overnight."
ANSWER = "The picture as it stands. " * 12


class _Transcript:
    def __init__(self):
        self.lines: list[str] = []

    def notice(self, message, style=""):
        self.lines.append(str(message))

    def user_echo(self, text):
        self.lines.append(text)

    def print(self, renderable="", indent=0):
        import io

        from rich.console import Console

        from hubbleflow.ui.theme import THEME

        if renderable == "":
            return
        console = Console(width=200, file=io.StringIO(), record=True, theme=THEME)
        console.print(renderable)
        self.lines.append(console.export_text())

    @property
    def text(self):
        return "\n".join(self.lines)


class _Graph:
    """A turn that fetched some pages and ended with an answer."""

    def __init__(self):
        self.answer = ANSWER
        self.fetched: list[tuple[str, str]] = []

    async def aget_state(self, config):
        from types import SimpleNamespace

        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

        messages = [HumanMessage("q")]
        for i, (url, body) in enumerate(self.fetched):
            messages.append(AIMessage(content="", tool_calls=[
                {"name": "web_fetch", "args": {"url": url}, "id": f"c{i}"}]))
            messages.append(ToolMessage(content=body, tool_call_id=f"c{i}"))
        if self.answer:
            messages.append(AIMessage(self.answer))
        return SimpleNamespace(values={"messages": messages})


@dataclass
class _App:
    config: Config
    transcript: _Transcript = field(default_factory=_Transcript)
    turns: list[str] = field(default_factory=list)
    saves: object = None

    def __post_init__(self):
        from types import SimpleNamespace

        self.harness = SimpleNamespace(graph=_Graph())

    async def turn(self, text):
        self.turns.append(text)
        if self.saves:
            self.saves()


def _app(tmp_path, research_root=...):
    root = tmp_path / "kb" if research_root is ... else research_root
    return _App(config=Config(
        model="ollama:x", workspace=tmp_path, session_db=tmp_path / "s", thread_id="t",
        resumed=False, auto_approve=False, cache=False, virtual_mode=True,
        global_skills=None, project_skills=None, knowledge=None, research=root,
    ))


def _meta(path: Path) -> dict:
    return yaml.safe_load(path.read_text().split("---")[1])


# --------------------------------------------------------------------------
# the layout
# --------------------------------------------------------------------------

def test_a_topic_is_a_directory_with_the_three_roles(tmp_path):
    d = tmp_path / "nepal-floods-2026"
    research.revise_overview(d, title="Nepal floods 2026", overview="what is true now")
    research.append_log(d, "Opened.")
    research.record_source(d, url="https://ndtv.com/x", title="Rasuwa toll", body=PAGE)

    assert (d / "overview.md").is_file()
    assert (d / "log.md").is_file()
    assert (d / "sources" / "rasuwa-toll.md").is_file()
    assert (d / "index.md").is_file()


def test_the_overview_is_a_concept_and_the_listing_is_not(tmp_path):
    """OKF reserves index.md as navigation and says it carries no frontmatter;
    treating it as a concept would put an empty twin beside the real topic."""
    d = tmp_path / "t"
    research.revise_overview(d, title="A topic", overview="body")
    concepts = knowledge.load(tmp_path, "research").concepts
    assert [c.path for c in concepts] == ["t/overview.md"]
    assert concepts[0].type == research.TOPIC_TYPE
    assert not (d / "index.md").read_text().startswith("---")


def test_a_source_page_keeps_the_link_and_the_page(tmp_path):
    d = tmp_path / "t"
    research.record_source(d, url="https://ndtv.com/x", title="Rasuwa toll", body=PAGE)
    meta = _meta(d / "sources" / "rasuwa-toll.md")
    assert meta["type"] == research.SOURCE_TYPE
    assert meta["resource"] == "https://ndtv.com/x"
    assert "further casualties" in (d / "sources" / "rasuwa-toll.md").read_text()


def test_a_long_page_is_truncated_rather_than_stored_whole(tmp_path):
    d = tmp_path / "t"
    research.record_source(d, url="https://x.com/a", title="Long", body="x" * 20_000)
    text = (d / "sources" / "long.md").read_text()
    assert "[...truncated" in text
    assert len(text) < research.MAX_SOURCE_CHARS + 1_000


def test_sources_link_back_from_the_overview_as_graph_edges(tmp_path):
    d = tmp_path / "topic"
    research.revise_overview(d, title="Topic", overview="body")
    path = research.record_source(d, url="https://a.com", title="A page", body=PAGE)
    research.link_sources(d, [path])

    bundle = knowledge.load(tmp_path, "research")
    assert ("topic/overview.md", "topic/sources/a-page.md") in bundle.edges()


# --------------------------------------------------------------------------
# the log
# --------------------------------------------------------------------------

def test_the_log_is_dated_and_appended_never_rewritten(tmp_path):
    d = tmp_path / "t"
    day_one = datetime(2026, 8, 26, tzinfo=timezone.utc)
    research.append_log(d, "Opened; 538 dead reported.", when=day_one)
    research.append_log(d, "Toll revised to 612.", when=day_one + timedelta(days=2))

    text = (d / "log.md").read_text()
    assert "## 2026-08-26" in text and "## 2026-08-28" in text
    assert "538 dead reported" in text, "the earlier entry must survive"
    assert text.index("2026-08-26") < text.index("2026-08-28")


def test_two_rounds_in_a_day_share_one_heading(tmp_path):
    d = tmp_path / "t"
    when = datetime(2026, 8, 26, tzinfo=timezone.utc)
    research.append_log(d, "first", when=when)
    research.append_log(d, "second", when=when)
    assert (d / "log.md").read_text().count("## 2026-08-26") == 1


def test_the_last_logged_date_is_readable(tmp_path):
    d = tmp_path / "t"
    research.append_log(d, "a", when=datetime(2026, 8, 26, tzinfo=timezone.utc))
    research.append_log(d, "b", when=datetime(2026, 8, 29, tzinfo=timezone.utc))
    assert research.last_logged(d) == "2026-08-29"


def test_an_empty_entry_is_not_logged(tmp_path):
    d = tmp_path / "t"
    research.append_log(d, "   ")
    assert not (d / "log.md").exists()


# --------------------------------------------------------------------------
# carrying a topic forward
# --------------------------------------------------------------------------

def test_a_question_finds_the_topic_it_belongs_to(tmp_path):
    research.revise_overview(tmp_path / "nepal-floods-2026", title="Nepal floods", overview="b")
    found = research.existing_topic(tmp_path, "nepal floods casualty figures")
    assert found is not None and found.name == "nepal-floods-2026"


def test_an_unrelated_question_opens_its_own_topic(tmp_path):
    research.revise_overview(tmp_path / "nepal-floods-2026", title="Nepal floods", overview="b")
    assert research.existing_topic(tmp_path, "how postgres vacuum works") is None


def test_revising_replaces_the_overview_rather_than_appending(tmp_path):
    d = tmp_path / "t"
    research.revise_overview(d, title="T", overview="the old picture")
    research.revise_overview(d, title="T", overview="the new picture")
    text = (d / "overview.md").read_text()
    assert "the new picture" in text and "the old picture" not in text


async def test_an_open_topic_is_carried_forward_not_restarted(tmp_path):
    app = _app(tmp_path)
    d = app.config.research / "nepal-floods-2026"
    research.revise_overview(d, title="Nepal floods 2026", overview="538 dead reported so far.")
    research.append_log(d, "Opened.")

    await commands._deep_research(app, "--again nepal floods 2026")
    brief = app.turns[0]
    assert "already open" in brief.lower()
    assert "538 dead reported so far." in brief, "the round must see what is known"
    assert "nepal-floods-2026" in brief


async def test_a_new_question_gets_the_opening_brief(tmp_path):
    app = _app(tmp_path)
    await commands._deep_research(app, "how does postgres vacuum work")
    assert "Work in rounds" in app.turns[0]
    assert "already open" not in app.turns[0].lower()


async def test_an_open_topic_is_reused_rather_than_researched_again(tmp_path):
    app = _app(tmp_path)
    research.revise_overview(app.config.research / "nepal-floods-2026",
                             title="Nepal floods 2026", overview="body")
    await commands._deep_research(app, "nepal floods 2026")
    assert app.turns == []
    assert "Already open" in app.transcript.text


async def test_an_expired_topic_is_carried_forward_without_asking(tmp_path):
    """A running story going stale is the case `stale_after` exists to catch."""
    app = _app(tmp_path)
    d = app.config.research / "nepal-floods-2026"
    research.revise_overview(d, title="Nepal floods 2026", overview="body", stale_after_days=1)
    past = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    text = (d / "overview.md").read_text().replace(
        str(_meta(d / "overview.md")["stale_after"]), past)
    (d / "overview.md").write_text(text)

    await commands._deep_research(app, "nepal floods 2026")
    assert len(app.turns) == 1


# --------------------------------------------------------------------------
# what the harness does after the round
# --------------------------------------------------------------------------

async def test_pages_opened_are_recorded_as_sources(tmp_path):
    app = _app(tmp_path)
    app.harness.graph.fetched = [("https://ndtv.com/x", PAGE)]
    await commands._deep_research(app, "nepal floods 2026")

    pages = list((app.config.research / "nepal-floods-2026" / "sources").glob("*.md"))
    assert [p.name for p in pages] == ["rasuwa-toll-rises.md"]
    assert _meta(pages[0])["resource"] == "https://ndtv.com/x"


async def test_a_link_only_mentioned_is_not_recorded(tmp_path):
    """Search results list pages that were never opened; filing those claims a
    provenance that does not exist."""
    app = _app(tmp_path)
    app.harness.graph.answer = ANSWER + " I also saw https://never-opened.example/p"
    app.harness.graph.fetched = [("https://ndtv.com/x", PAGE)]
    await commands._deep_research(app, "nepal floods 2026")

    urls = {_meta(p)["resource"]
            for p in (app.config.research / "nepal-floods-2026" / "sources").glob("*.md")}
    assert urls == {"https://ndtv.com/x"}


async def test_a_source_read_twice_is_not_filed_twice(tmp_path):
    app = _app(tmp_path)
    app.harness.graph.fetched = [("https://ndtv.com/x", PAGE)]
    await commands._deep_research(app, "nepal floods 2026")
    await commands._deep_research(app, "--again nepal floods 2026")

    pages = list((app.config.research / "nepal-floods-2026" / "sources").glob("*.md"))
    assert len(pages) == 1


async def test_a_forgotten_save_is_filed_by_the_harness(tmp_path):
    """A smaller model often researches well and never makes the tool call."""
    app = _app(tmp_path)
    await commands._deep_research(app, "nepal floods 2026")
    d = app.config.research / "nepal-floods-2026"
    assert (d / "overview.md").is_file()
    assert _meta(d / "overview.md")["status"] == "draft"
    assert "didn't call save_research" in app.transcript.text


async def test_the_harness_stays_out_of_the_way_when_the_model_filed(tmp_path):
    app = _app(tmp_path)
    d = app.config.research / "nepal-floods-2026"
    app.saves = lambda: research.revise_overview(
        d, title="Nepal floods 2026", overview="model wrote this")
    await commands._deep_research(app, "nepal floods 2026")
    assert "model wrote this" in (d / "overview.md").read_text()
    assert "didn't call save_research" not in app.transcript.text


async def test_a_conclusion_too_short_to_keep_is_not_filed(tmp_path):
    app = _app(tmp_path)
    app.harness.graph.answer = "Sorry, I couldn't find anything."
    await commands._deep_research(app, "an unanswerable question")
    assert not list(app.config.research.glob("*/overview.md"))
    assert "too little to keep" in app.transcript.text


async def test_a_model_that_never_wrote_prose_is_reported_as_such(tmp_path):
    """The transcript can be full of rendered tool output and still hold no
    answer; calling that "no conclusion worth keeping" blames research that was
    never written and leaves nothing to act on."""
    app = _app(tmp_path)
    app.harness.graph.answer = ""
    app.harness.graph.fetched = [("https://a.com", PAGE)]
    await commands._deep_research(app, "a question it gave up on")

    said = app.transcript.text
    assert "never wrote an answer" in said
    assert "tool output, not its conclusion" in said
    assert "web_fetch" in said, "it should say what the model actually did"


async def test_research_can_be_turned_off(tmp_path):
    app = _app(tmp_path, research_root=None)
    await commands._deep_research(app, "anything")
    assert app.turns == [] and "turned off" in app.transcript.text


# --------------------------------------------------------------------------
# finding it again
# --------------------------------------------------------------------------

def _lookup(knowledge_root, research_root):
    from hubbleflow.tools.knowledge import make_knowledge_tools

    tools = make_knowledge_tools(knowledge_root, "/knowledge/", research_root, "/research/")
    return next((t for t in tools if t.name == "knowledge_lookup"), None)


async def test_a_topic_is_findable_without_a_generated_bundle(tmp_path):
    root = tmp_path / "kb"
    research.revise_overview(root / "nepal-floods-2026", title="Nepal floods 2026",
                             overview="body", description="Glacier collapse dammed the Lhende.")
    answer = await _lookup(None, root).ainvoke({"query": "nepal"})
    assert "/research/nepal-floods-2026/overview.md" in answer


async def test_the_lookup_can_narrow_to_topics_over_sources(tmp_path):
    """A topic is the thing to read; its sources are what to check against."""
    root = tmp_path / "kb"
    d = root / "t"
    research.revise_overview(d, title="A topic", overview="body")
    research.record_source(d, url="https://a.com", title="A page", body=PAGE)

    topics = await _lookup(None, root).ainvoke({"query": "", "type": research.TOPIC_TYPE})
    assert "overview.md" in topics and "sources/" not in topics
