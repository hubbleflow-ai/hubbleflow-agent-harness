"""OKF knowledge bundles: what the agent knows, as opposed to how it works.

Skills tune behaviour. A knowledge bundle is domain material -- what this
system is, why a table has the shape it does, which runbook applies -- and it
is usually generated and maintained by something else. OpenWiki writes one, in
the Open Knowledge Format that Google Cloud published: a directory tree of
markdown files, each carrying YAML frontmatter, where `type` is the only
required field and everything else is the producer's business.

The spec's consumer rules (SPEC.md, section 11) are unusually explicit that a
reader must be forgiving: it may not reject a concept for an unknown `type`, an
unrecognised frontmatter key, a broken cross-link, or a missing index. So
nothing here raises on bad input. A malformed page loses its metadata and keeps
its path, because a page you can still open beats a bundle that won't load.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

# Reserved by the spec: a directory listing and a changelog, not concepts.
RESERVED = ("index.md", "log.md")
# Producers have written both; accept either rather than miss a changelog.
RESERVED_ALIASES = ("logs.md",)

MAX_CONCEPTS = 2_000
_STATUSES = ("draft", "stable", "deprecated")


@dataclass(frozen=True, slots=True)
class Concept:
    """One page of a bundle, reduced to the fields worth querying."""

    path: str
    # Which bundle this came from, so a merged view can say where to read it and
    # a link only resolves against pages from the same tree.
    origin: str = ""
    type: str = ""
    title: str = ""
    description: str = ""
    tags: tuple[str, ...] = ()
    status: str = "stable"
    stale_after: datetime | None = None
    sources: tuple[str, ...] = ()
    generated_by: str = ""

    @property
    def name(self) -> str:
        return self.title or Path(self.path).stem

    def stale(self, *, now: datetime | None = None) -> bool:
        """Past its own expiry. Unstamped pages are never stale, by omission."""
        if self.stale_after is None:
            return False
        return (now or datetime.now(timezone.utc)) > self.stale_after

    def suspect(self) -> str:
        """Why this page shouldn't be trusted at face value, if it shouldn't.

        Surfaced rather than filtered: the spec says a failing page is reported,
        not silently dropped, and an agent that knows a page is stale can say so
        instead of confidently repeating it.
        """
        reasons = []
        if self.status == "deprecated":
            reasons.append("deprecated")
        elif self.status == "draft":
            reasons.append("draft")
        if self.stale():
            reasons.append(f"stale since {self.stale_after:%Y-%m-%d}")
        return ", ".join(reasons)


@dataclass(slots=True)
class Bundle:
    """A loaded bundle, plus what went wrong loading it."""

    root: Path
    concepts: list[Concept] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.concepts)

    def types(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for c in self.concepts:
            counts[c.type or "untyped"] = counts.get(c.type or "untyped", 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    def tags(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for c in self.concepts:
            for t in c.tags:
                counts[t] = counts.get(t, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    def find(self, needle: str = "", *, type: str = "", tag: str = "") -> list[Concept]:
        """Match on substring across the queryable fields, then narrow.

        Deliberately not a ranked search: the point is to hand back a short list
        of paths for the agent to open, not to answer from metadata alone.
        """
        needle, type, tag = needle.lower(), type.lower(), tag.lower()
        hits = []
        for c in self.concepts:
            if type and type not in c.type.lower():
                continue
            if tag and not any(tag == t.lower() for t in c.tags):
                continue
            if needle:
                haystack = " ".join((c.path, c.type, c.title, c.description, *c.tags)).lower()
                if needle not in haystack:
                    continue
            hits.append(c)
        return sorted(hits, key=lambda c: (c.type, c.name))

    def edges(self) -> list[tuple[str, str]]:
        """(from, to) between concepts, from `sources` entries naming a page.

        A source may be a URL, a bundle-relative path, or a free-form scope
        descriptor. Only the ones that resolve to a page in this bundle become
        edges; the rest are provenance, not structure.
        """
        known = {(c.origin, c.path) for c in self.concepts}
        out = []
        for c in self.concepts:
            for source in c.sources:
                target = source.lstrip("./")
                if (c.origin, target) in known and target != c.path:
                    out.append((c.path, target))
        return out


def merge(*bundles: Bundle) -> Bundle:
    """One view over several bundles.

    A workspace can have a generated wiki and the harness's own research at the
    same time. They are separate directories with separate owners, but a lookup
    should search both -- knowing a thing is worth the same whoever wrote it.
    """
    live = [b for b in bundles if b is not None]
    merged = Bundle(root=live[0].root if live else Path())
    for bundle in live:
        merged.concepts.extend(bundle.concepts)
        merged.unreadable.extend(bundle.unreadable)
    return merged


def load(root: Path | None, origin: str = "") -> Bundle:
    """Read every concept in a bundle. Never raises on a bad page."""
    if root is None or not root.is_dir():
        return Bundle(root=root or Path())

    bundle = Bundle(root=root)
    for path in sorted(root.rglob("*.md")):
        name = path.name.lower()
        if name in RESERVED or name in RESERVED_ALIASES:
            continue
        if len(bundle.concepts) >= MAX_CONCEPTS:
            break
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            bundle.unreadable.append(str(path.relative_to(root)))
            continue
        bundle.concepts.append(_concept(path.relative_to(root).as_posix(), text, origin))
    return bundle


def _concept(rel_path: str, text: str, origin: str = "") -> Concept:
    meta = _frontmatter(text)
    if not isinstance(meta, dict):
        return Concept(path=rel_path, origin=origin)

    status = str(meta.get("status") or "stable").strip().lower()
    return Concept(
        path=rel_path,
        origin=origin,
        type=_text(meta.get("type")),
        title=_text(meta.get("title")),
        description=_text(meta.get("description")),
        tags=_strings(meta.get("tags")),
        status=status if status in _STATUSES else "stable",
        stale_after=_when(meta.get("stale_after")),
        sources=_sources(meta.get("sources")),
        generated_by=_text((meta.get("generated") or {}).get("by") if isinstance(meta.get("generated"), dict) else ""),
    )


def _frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    _, _, rest = text.partition("---")
    block, sep, _ = rest.partition("\n---")
    if not sep:
        return {}
    try:
        return yaml.safe_load(block) or {}
    except yaml.YAMLError:
        return {}


def _text(value) -> str:
    return " ".join(str(value).split()) if value else ""


def _strings(value) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value if v)
    return ()


def _sources(value) -> tuple[str, ...]:
    """Each entry requires a `resource`; a bare string is accepted too."""
    out = []
    for entry in _as_list(value):
        if isinstance(entry, dict):
            resource = entry.get("resource")
            if resource:
                out.append(str(resource))
        elif entry:
            out.append(str(entry))
    return tuple(out)


def _as_list(value) -> list:
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _when(value) -> datetime | None:
    """Parse an ISO 8601 instant, treating a naive one as UTC."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
