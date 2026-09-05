"""Topics: how research is kept so it can grow.

A finding written as one flat file answers a question once. That is fine for a
question with an answer -- does this library support that -- and wrong for
anything still happening, where the answer next week is a revision of this one
rather than a different question.

So a topic is a directory, and it holds three kinds of thing:

    nepal-floods-2026/
      overview.md     what is true now      rewritten each round
      log.md          what changed, when    appended, never rewritten
      sources/        one page per source   written once, kept
      index.md        a listing             regenerated

`overview.md` is what gets read when someone asks about the topic, and it stays
short because it is only ever the current state. `log.md` grows forever and
nobody loads it unless they ask what changed. `sources/` is where a claim goes
to be checked -- the failure that prompted all this was a finding citing
thirty-five links with no way to tell which sentence came from which.

`index.md` and `log.md` are the two filenames OKF reserves, so the layout is the
spec's rather than one invented here. The spec also says `index.md` carries no
frontmatter, which is why the topic's substance lives in `overview.md` and not
in the listing.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

TOPIC_TYPE = "Topic"
SOURCE_TYPE = "Source"

OVERVIEW = "overview.md"
LOG = "log.md"
INDEX = "index.md"
SOURCES = "sources"

MAX_SLUG = 60
# A source page keeps enough to check a claim against, not the whole web page.
MAX_SOURCE_CHARS = 4_000
_STATUSES = ("draft", "stable", "deprecated")


# --------------------------------------------------------------------------
# where things live
# --------------------------------------------------------------------------

def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:MAX_SLUG].rstrip("-")


def topic_dir(root: Path, topic: str) -> Path:
    return root / slug(topic)


def existing_topic(root: Path | None, question: str) -> Path | None:
    """The topic directory this question belongs to, if one is already open.

    Matched on word overlap with the topic's own name, so a question phrased
    freely still lands on the topic it is about. A new phrasing of an open
    subject should extend it, not start a second directory beside it.
    """
    if root is None or not root.is_dir():
        return None
    asked = _words(question)
    if not asked:
        return None

    best, score = None, 0.0
    for candidate in sorted(root.iterdir()):
        if not (candidate / OVERVIEW).is_file():
            continue
        overlap = len(asked & _words(candidate.name.replace("-", " "))) / len(asked)
        if overlap > score:
            best, score = candidate, overlap
    return best if score >= 0.5 else None


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


# --------------------------------------------------------------------------
# reading, so a second round knows what the first one concluded
# --------------------------------------------------------------------------

def read_overview(directory: Path) -> str:
    """The body of the current overview, without its frontmatter."""
    try:
        text = (directory / OVERVIEW).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    return text.split("---", 2)[2].strip() if text.startswith("---") else text.strip()


def last_logged(directory: Path) -> str:
    """The most recent date in the log, so a round can research only what is new."""
    try:
        text = (directory / LOG).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    dates = re.findall(r"^##\s*(\d{4}-\d{2}-\d{2})", text, re.MULTILINE)
    return max(dates) if dates else ""


def known_sources(directory: Path) -> set[str]:
    """URLs already recorded, so a re-read is not filed twice."""
    found = set()
    for path in sorted((directory / SOURCES).glob("*.md")) if (directory / SOURCES).is_dir() else []:
        try:
            meta = _frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        if isinstance(meta, dict) and meta.get("resource"):
            found.add(str(meta["resource"]))
    return found


# --------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------

def record_source(directory: Path, *, url: str, title: str, body: str, when: datetime | None = None) -> str | None:
    """Keep one page that was read. Returns its bundle-relative path.

    Written by the harness from what `web_fetch` actually returned rather than
    by the model, because a page that was read is a fact and should not depend
    on anything remembering to say so.
    """
    name = slug(title or url) or slug(_host(url)) or "source"
    target = directory / SOURCES / f"{name}.md"
    stamp = (when or datetime.now(timezone.utc)).isoformat(timespec="seconds")

    meta = {
        "type": SOURCE_TYPE,
        "title": (title or url).strip(),
        "resource": url.strip(),
        "generated": {"by": producer(), "at": stamp},
    }
    excerpt = body.strip()
    if len(excerpt) > MAX_SOURCE_CHARS:
        excerpt = excerpt[:MAX_SOURCE_CHARS].rstrip() + "\n\n[...truncated; the page continues]"

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_page(meta, excerpt), encoding="utf-8")
    except OSError:
        return None
    return f"{directory.name}/{SOURCES}/{target.name}"


def revise_overview(
    directory: Path,
    *,
    title: str,
    overview: str,
    description: str = "",
    tags: list[str] | None = None,
    sources: list[str] | None = None,
    stale_after_days: int = 0,
    status: str = "",
) -> tuple[bool, str]:
    """Replace what is true now. The previous version is not kept.

    Nothing is lost by overwriting: what changed belongs in the log, and the
    evidence is in `sources/`. Keeping every past overview would leave the
    reader deciding which one to believe.
    """
    if not slug(title):
        return False, "That title has no usable filename in it — give it words."

    now = datetime.now(timezone.utc)
    meta: dict = {
        "type": TOPIC_TYPE,
        "title": title.strip(),
        "generated": {"by": producer(), "at": now.isoformat(timespec="seconds")},
    }
    if description.strip():
        meta["description"] = " ".join(description.split())
    if status in _STATUSES:
        meta["status"] = status
    if tags:
        meta["tags"] = [str(t).strip() for t in tags if str(t).strip()]
    if sources:
        meta["sources"] = [{"resource": str(s).strip()} for s in sources if str(s).strip()]
    if stale_after_days > 0:
        meta["stale_after"] = (now + timedelta(days=stale_after_days)).isoformat(timespec="seconds")

    try:
        directory.mkdir(parents=True, exist_ok=True)
        existed = (directory / OVERVIEW).exists()
        (directory / OVERVIEW).write_text(_page(meta, overview.strip()), encoding="utf-8")
    except OSError as error:
        return False, f"Couldn't write {directory / OVERVIEW}: {error}"

    write_index(directory)
    return True, f"{'Revised' if existed else 'Opened'} {directory.name}"


def append_log(directory: Path, entry: str, *, when: datetime | None = None) -> None:
    """Add a dated line to the topic's history. Never rewrites what is there.

    Dates head the entries in the ISO form the spec asks for, and a second round
    on the same day joins that day's heading rather than repeating it.
    """
    entry = " ".join(entry.split())
    if not entry:
        return
    day = (when or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    path = directory / LOG
    try:
        directory.mkdir(parents=True, exist_ok=True)
        text = path.read_text(encoding="utf-8") if path.exists() else f"# Log\n"
        heading = f"\n## {day}\n"
        if heading not in text:
            text += heading
        # append under today's heading, which is always the last one
        text = text.rstrip() + f"\n- {entry}\n"
        path.write_text(text, encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return


def link_sources(directory: Path, paths: list[str]) -> None:
    """Point the overview at the source pages that were recorded for it.

    Done after the round rather than during it: the pages are written from what
    `web_fetch` returned, which the harness only sees once the turn is over.
    Being bundle-relative, these resolve as graph edges.
    """
    path = directory / OVERVIEW
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    meta = _frontmatter(text)
    if not meta:
        return
    body = text.split("---", 2)[2].strip()
    existing = [e.get("resource") for e in (meta.get("sources") or []) if isinstance(e, dict)]
    merged = list(dict.fromkeys([*existing, *paths]))
    meta["sources"] = [{"resource": p} for p in merged if p]
    try:
        path.write_text(_page(meta, body), encoding="utf-8")
    except OSError:
        return


def write_index(directory: Path) -> None:
    """Regenerate the topic's listing.

    Carries no frontmatter: the spec reserves `index.md` as navigation, and a
    reader that treated it as a concept would find a second, emptier copy of
    the topic sitting beside the real one.
    """
    lines = [f"# {directory.name.replace('-', ' ')}", ""]
    if (directory / OVERVIEW).is_file():
        lines += ["- [Overview](overview.md) — what is true now"]
    if (directory / LOG).is_file():
        lines += ["- [Log](log.md) — what changed, and when"]

    pages = sorted((directory / SOURCES).glob("*.md")) if (directory / SOURCES).is_dir() else []
    if pages:
        lines += ["", f"## Sources ({len(pages)})", ""]
        for page in pages:
            lines.append(f"- [{page.stem.replace('-', ' ')}]({SOURCES}/{page.name})")
    try:
        (directory / INDEX).write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        return


# --------------------------------------------------------------------------

def producer() -> str:
    """`<producer>/<version>`, the actor form the spec asks for."""
    from hubbleflow.cli import __version__

    return f"hubbleflow/{__version__}"


def _page(meta: dict, body: str) -> str:
    return "---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True) + "---\n\n" + body + "\n"


def _frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    block, sep, _ = text.partition("---")[2].partition("\n---")
    if not sep:
        return {}
    try:
        return yaml.safe_load(block) or {}
    except yaml.YAMLError:
        return {}


def _host(url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(url).netloc.replace("www.", "")
