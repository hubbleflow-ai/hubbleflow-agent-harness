---
name: web-research
description: Answer a question that depends on current information from the internet — library versions, release notes, breaking changes, unfamiliar error messages, live documentation, or anything that postdates your training data. Use when the answer must be true today rather than true in general.
---

# Web research

Your training data has a cutoff. Anything downstream of it — a version number, a
renamed API, a deprecation, a new error message — you do not know, and guessing
at it produces answers that read as confident and are wrong.

You have two tools:

- `web_search(query)` — a Google-grounded answer plus the sources behind it.
- `web_fetch(url)` — a page, as markdown.

## The workflow

**1. Search with a question, not keywords.**

The search is model-grounded, so it responds to a real question. Ask
`"What is the current stable release of the uv package manager?"`, not
`"uv version"`. Include the version or platform you care about when it narrows
the answer: `"Does pydantic v2 still support the __fields__ attribute?"`

**2. Read the primary source before you rely on it.**

The search summary is a starting point, not the answer. It can be stale, it can
blend two versions together, and it can be confidently wrong about a detail.
When the answer will shape code you write or advice you give, `web_fetch` the
source it cited — the changelog, the release page, the actual docs — and read
what it says.

Prefer, in order: official docs, the project's own repository and release notes,
the maintainer's own writing, then everything else. A blog post from three years
ago describing an API that has since changed is worse than no source at all.

**3. Corroborate anything surprising.**

If a result contradicts what you expect, that's the case that most needs a
second source — not the case to accept because it's newer. Two independent
sources agreeing is worth more than one source stated emphatically.

**4. Cross-check against the code in front of you.**

Research about a library is a claim about the world; the version in this
project's lockfile is a fact about this project. When they disagree, the
lockfile wins for the code you're about to write. Check it before applying what
you found — `uv.lock`, `package-lock.json`, `requirements.txt`, whatever the
project uses.

## Reporting back

State what you found and cite the URL you actually read, so the claim can be
checked. Say when a source is dated, when two sources disagreed, and when you
could not confirm something — an honest "the docs don't cover this" is more
useful than a plausible invention.

Don't paste page contents into your answer. Read them, then say what's true.

## When not to use this

Don't reach for the web to answer a question about this codebase — read the
code. Don't search for general programming knowledge that hasn't changed in
years. Searching is slower than thinking, and a search result about someone
else's project is not evidence about this one.
