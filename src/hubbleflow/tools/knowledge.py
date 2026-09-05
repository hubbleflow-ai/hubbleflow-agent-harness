"""A lookup over the OKF bundle, so the agent finds a page instead of grepping.

Without this the bundle is just more files: the agent greps the tree, reads
whole pages to discover they were the wrong ones, and spends its window doing
it. The frontmatter already carries what it needs to choose -- type, title,
description, tags -- so the tool answers with paths and lets the file tools do
the reading.

Staleness travels with every hit on purpose. A knowledge bundle that has quietly
gone out of date is worse than none at all, and `stale_after` is the field that
lets the agent say so rather than repeat it confidently.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from langchain_core.tools import tool

from hubbleflow import knowledge as knowledge_module
from hubbleflow import research as research_module

MAX_HITS = 25
RESEARCH_TYPE = "Research"
MAX_SLUG = 60


def make_knowledge_tools(
    root: Path | None, route: str, research: Path | None = None, research_route: str = "/research/"
) -> list:
    """Lookup over whatever bundles exist, plus a way to add to our own.

    The lookup covers both: a generated wiki and the harness's own research are
    separate directories with separate owners, but nothing filed is worth less
    for having been researched here rather than generated elsewhere. Binding it
    only when a generated bundle exists -- which is what this used to do -- meant
    a workspace could file research all day and never find it again.
    """
    tools = []
    if research is not None:
        tools.append(_save_tool(research))
    if root is None and research is None:
        return tools

    @tool("knowledge_lookup", parse_docstring=True)
    async def knowledge_lookup(query: str = "", type: str = "", tag: str = "") -> str:
        """Find pages in this project's knowledge bundle.

        The bundle holds curated domain knowledge -- how this system is put
        together and why -- alongside anything `/deep-research` has already
        answered. Search it first, before reading source or reaching for the
        web: a question asked here before has been written down, with the
        sources it came from and a note of when it stops being trustworthy.

        Returns paths, not contents. Open the ones that look right with
        read_file.

        Args:
            query: Words to match against titles, descriptions, tags and paths.
            type: Restrict to one OKF type, e.g. "Runbook" or "BigQuery Table".
            tag: Restrict to one exact tag.
        """
        bundle = knowledge_module.merge(
            knowledge_module.load(root, "knowledge"),
            knowledge_module.load(research, "research"),
        )
        if not bundle:
            return "Nothing has been filed yet. /deep-research adds to this."

        hits = bundle.find(query, type=type, tag=tag)
        if not hits:
            types = ", ".join(bundle.types()) or "none"
            return f"Nothing matched. Types in this bundle: {types}."

        where = {"knowledge": route, "research": research_route}
        lines = [f"{len(hits)} match(es). Open one with read_file at the path shown."]
        for concept in hits[:MAX_HITS]:
            suspect = concept.suspect()
            detail = concept.description or concept.type or ""
            lines.append(
                f"  {where.get(concept.origin, route)}{concept.path}"
                + (f"  [{concept.type}]" if concept.type else "")
                + (f"  ({suspect})" if suspect else "")
                + (f"\n      {detail}" if detail else "")
            )
        if len(hits) > MAX_HITS:
            lines.append(f"  ... and {len(hits) - MAX_HITS} more; narrow with type= or tag=.")
        return "\n".join(lines)

    tools.append(knowledge_lookup)
    return tools


def file_research(
    research: Path,
    *,
    title: str,
    finding: str,
    description: str = "",
    tags: list[str] | None = None,
    sources: list[str] | None = None,
    stale_after_days: int = 0,
    status: str = "",
) -> tuple[bool, str]:
    """Write one OKF concept. Returns (written, message).

    Separate from the tool because the tool is not the only caller: a model that
    finished the research and forgot the last step still has to have its work
    kept, so `/deep-research` files it from here instead.
    """
    slug = _slug(title)
    if not slug:
        return False, "That title has no usable filename in it, give it words."

    now = datetime.now(timezone.utc)
    meta: dict = {
        "type": RESEARCH_TYPE,
        "title": title.strip(),
        "generated": {"by": _producer(), "at": now.isoformat(timespec="seconds")},
    }
    if description.strip():
        meta["description"] = " ".join(description.split())
    if status:
        meta["status"] = status
    if tags:
        meta["tags"] = [str(t).strip() for t in tags if str(t).strip()]
    if sources:
        meta["sources"] = [{"resource": str(u).strip()} for u in sources if str(u).strip()]
    if stale_after_days > 0:
        meta["stale_after"] = (now + timedelta(days=stale_after_days)).isoformat(timespec="seconds")

    page = "---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True) + "---\n\n" + finding.strip() + "\n"
    target = research / f"{slug}.md"
    try:
        research.mkdir(parents=True, exist_ok=True)
        existed = target.exists()
        target.write_text(page, encoding="utf-8")
    except OSError as error:
        return False, f"Couldn't write {target}: {error}"

    expiry = f", rechecked after {stale_after_days} days" if stale_after_days else ""
    return True, f"{'Updated' if existed else 'Filed'} {slug}.md{expiry}. It is in the knowledge bundle now."


def _save_tool(research: Path):
    @tool("save_research", parse_docstring=True)
    async def save_research(
        topic: str,
        title: str,
        overview: str,
        changed: str,
        description: str = "",
        tags: list[str] | None = None,
        stale_after_days: int = 0,
    ) -> str:
        """File what you found, as the current state of a topic.

        Call this once, at the end. What you write in `overview` replaces
        whatever was known before, so write the whole picture as it stands now
        rather than only the new part -- the new part goes in `changed`.

        Sources are recorded for you from the pages you opened. Refer to them
        in the overview by what they are -- the outlet and the date -- rather
        than pasting URLs.

        Args:
            topic: The subject, stable across updates. "nepal floods 2026", not
                "nepal floods august". Reuse the existing name when adding to a
                topic you have already opened.
            title: A readable name for the topic.
            overview: The full picture as it now stands, in markdown. Long
                enough that a reader need not open the sources to follow it.
            changed: One line for the log: what this round established or
                revised. "Toll revised to 612; three bridges confirmed lost."
            description: One line for the listing.
            tags: A few short labels.
            stale_after_days: When this needs looking at again. A running
                story is days; something settled is 0, meaning it does not
                expire.
        """
        directory = research_module.topic_dir(research, topic)
        written, message = research_module.revise_overview(
            directory, title=title, overview=overview, description=description,
            tags=tags, stale_after_days=stale_after_days,
        )
        if not written:
            return message
        research_module.append_log(directory, changed or "Reviewed.")
        return f"{message}. The topic is in the knowledge bundle now."

    return save_research


def _producer() -> str:
    """`<producer>/<version>`, the actor form the OKF spec asks for."""
    from hubbleflow.cli import __version__

    return f"hubbleflow/{__version__}"


def _slug(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:MAX_SLUG].rstrip("-")
