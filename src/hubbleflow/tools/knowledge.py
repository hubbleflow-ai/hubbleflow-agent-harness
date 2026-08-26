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

from pathlib import Path

from langchain_core.tools import tool

from hubbleflow import knowledge as knowledge_module

MAX_HITS = 25


def make_knowledge_tools(root: Path | None, route: str) -> list:
    """The lookup tool, or nothing at all when the workspace has no bundle."""
    if root is None:
        return []

    @tool("knowledge_lookup", parse_docstring=True)
    async def knowledge_lookup(query: str = "", type: str = "", tag: str = "") -> str:
        """Find pages in this project's knowledge bundle.

        The bundle is curated domain knowledge about this system -- how it is
        put together, why, and what the moving parts mean. Search it before
        reading source to answer a "what is this" or "why does it work this
        way" question; the answer is often already written down.

        Returns paths, not contents. Open the ones that look right with
        read_file.

        Args:
            query: Words to match against titles, descriptions, tags and paths.
            type: Restrict to one OKF type, e.g. "Runbook" or "BigQuery Table".
            tag: Restrict to one exact tag.
        """
        bundle = knowledge_module.load(root)
        if not bundle:
            return "The knowledge bundle is empty."

        hits = bundle.find(query, type=type, tag=tag)
        if not hits:
            types = ", ".join(bundle.types()) or "none"
            return f"Nothing matched. Types in this bundle: {types}."

        lines = [f"{len(hits)} match(es). Read them at {route}<path>."]
        for concept in hits[:MAX_HITS]:
            suspect = concept.suspect()
            detail = concept.description or concept.type or ""
            lines.append(
                f"  {concept.path}"
                + (f"  [{concept.type}]" if concept.type else "")
                + (f"  ({suspect})" if suspect else "")
                + (f"\n      {detail}" if detail else "")
            )
        if len(hits) > MAX_HITS:
            lines.append(f"  ... and {len(hits) - MAX_HITS} more; narrow with type= or tag=.")
        return "\n".join(lines)

    return [knowledge_lookup]
