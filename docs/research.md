# Knowledge and research

What the agent knows, as opposed to how it works.

## Project instructions

At startup the harness reads the first of these it finds in the workspace root:

```
HUBBLEFLOW.md    AGENTS.md    CLAUDE.md
```

and appends it to the system prompt, so a repo's conventions don't have to be
re-explained every session. First match wins rather than all of them
concatenated -- a project with both `AGENTS.md` and `CLAUDE.md` almost always
has the same text in each. `HUBBLEFLOW_CONTEXT_FILES` takes a comma-separated
list, and an empty value turns the lookup off for a workspace whose `AGENTS.md`
was written for somebody else's tool.

## Knowledge

Skills are how the agent works. A knowledge bundle is what it knows -- domain
material about the system in front of it, usually generated and maintained by
something else.

The format is [OKF](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf),
the Open Knowledge Format Google Cloud published: a directory of markdown files
carrying YAML frontmatter, where `type` is the only required field.
[OpenWiki](https://github.com/langchain-ai/openwiki) writes one, and as of its
0.2 that is the only shape it writes:

```bash
npx openwiki --init          # generates ./openwiki
```

The harness picks up `openwiki/` automatically, mounts it read-only at
`/knowledge/`, and adds a `knowledge_lookup` tool so the agent finds the page it
needs from frontmatter instead of grepping the tree and reading the wrong files
to find out. `HUBBLEFLOW_KNOWLEDGE` points somewhere else, or at nothing.

```
/knowledge              every concept, grouped by type
/knowledge <words>      just the matching ones
/knowledge --graph      a standalone HTML node view
```

## Deep research

```
/deep-research nepal floods 2026
```

Research runs in rounds, plan the sub-questions, search each as a question,
read the primary source before relying on it, corroborate anything surprising,
then ask what is still unanswered and go again if the gap matters.

**A topic is a directory, not a file**, because most subjects worth researching
do not stop happening:

```
nepal-floods-2026/
  overview.md    what is true now        rewritten each round
  log.md         what changed, and when  appended, never rewritten
  sources/       one page per source     written once, kept
  index.md       a listing               regenerated
```

Each answers a different question. *"What do I know about the floods?"* reads
`overview.md`, which stays short because it is only ever the current state.
*"What changed this week?"* reads `log.md`. *"Where did 612 come from?"* opens
the page in `sources/` that it was read from.

`index.md` and `log.md` are the two filenames OKF reserves, so the layout is the
spec's rather than one invented here.

### Carrying a topic forward

Ask about an open topic and it does not start again:

```
/deep-research --again nepal floods 2026
```

The round is given the current overview and the date it was last touched, and
researches forward from there. It appends a line to the log, records any new
sources, and rewrites the overview to what is now true. So the topic
accumulates instead of resetting, and `stale_after` stops meaning "wrong" and
starts meaning "due for another round", an expired topic is carried forward
without being asked.

### What the harness does rather than the model

**Sources are recorded from the pages actually opened**, read back out of the
`web_fetch` calls the round made. Not from a list the model provides: a link
seen in a search result and never read is not a source, and asking a model to
remember which were which is how the citations came out wrong the first time.

And if the model finishes the research and never calls `save_research`, which
a smaller one often does, since the call sits at the end of a long chain, the
harness files the answer itself, marked `draft` because the model's own
judgment about tags and expiry is the part that is missing. The research is
never the thing that gets lost.

Notes are written to `.hubbleflow/knowledge/`, deliberately **not** inside a
generated bundle: `openwiki --update` is entitled to rewrite its own directory,
and two producers sharing a folder ends one way. Both are read as one view.
`HUBBLEFLOW_RESEARCH` moves it, or turns it off when empty.

**Staleness travels with every result.** A bundle that has quietly gone out of
date is worse than no bundle, so `status: deprecated` and an elapsed
`stale_after` are reported on the page rather than filtered out -- the agent can
then say a page is stale instead of repeating it confidently.

The reader is deliberately forgiving, because the spec requires it to be: an
unknown `type`, an unrecognised key, broken YAML, a missing `index.md` or a
dangling cross-link all cost a page its metadata and nothing more. Nothing in a
malformed bundle stops the session.

---

[← README](../README.md)
