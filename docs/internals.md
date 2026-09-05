# Internals

How the pieces fit, for anyone changing them.

## Context caching

Off unless you ask for it: `hubbleflow --cache` (or `--cache=1`, or
`HUBBLEFLOW_CACHE=1`). `/cache on` and `/cache off` flip it mid-session, and
`/cache` alone reports what it has saved.

An agentic loop resends the whole conversation on every model call, so most of
what you pay for is tokens the model has already seen. Gemini bills a cached
token at a tenth of a fresh one, so `cache.py` keeps a rolling explicit cache of
the stable prefix, system prompt, tool declarations, and the conversation up to
the last complete turn, and sends only the tail.

Measured on a real session: **$0.0407 of input became $0.0089**, with $0.0033 of
storage against it. 92% of tokens served from cache, net 78% cheaper.

Two things make it work. Gemini rejects a request that names a cache *and* sets
`system_instruction`, `tools` or `tool_config`, those have to live inside the
cache, so `CachingChatGoogle` intercepts request assembly to move them there.
And storage is billed on the **TTL you request, not the time you use**, so the
TTL is short (5 minutes), caches are released when superseded, and everything is
deleted when the session ends.

Every cache is stamped `display_name="hubbleflow"`, so a session sweeps any that
an earlier crash left behind, and `hubbleflow --purge-caches` releases the lot
without starting a session. The cache rebuilds when the prefix grows past a
threshold or when compaction rewrites history. `/usage` shows cached-vs-fresh tokens and whether caching is
actually paying for itself. Any API failure disables caching for the session
rather than retrying on every call.

Implicit caching is worth knowing about and doesn't help here: it needs no setup
but did not fire on this account across repeated tests, so explicit is the only
mechanism that works.

## Web access

`web_search` is Gemini's Google Search grounding, no second API account, and it
returns real source URLs rather than the redirect wrappers grounding hands back.
`web_fetch` pulls a page down to markdown, dropping nav and script chrome.

Both live in `tools/web.py`. The `web-research`
skill in `~/.hubbleflow/skills` teaches the workflow around them: search with a
question, read the primary source before relying on it, and check findings
against the project's own lockfile.

A fetch asks for approval per host, saying yes to one page on `docs.astral.sh`
covers the rest of that domain. Search isn't gated.

## MCP

Servers are declared in `~/.hubbleflow/mcp.json` (all workspaces) or `.mcp.json`
(this one), in the same shape Claude Code uses:

```json
{
  "mcpServers": {
    "fetch": { "command": "uvx", "args": ["mcp-server-fetch"] },
    "github": { "url": "https://api.githubcopilot.com/mcp/" }
  }
}
```

Both stdio and HTTP transports work. Each server is connected independently, so
one that won't start is reported and skipped rather than taking the session
down. `/mcp` shows what came up. Every MCP tool needs approval the first time -
they come from outside this codebase and can do whatever their server can.

## Approvals

Reads are free; writes and commands pause and ask. What counts as a read is
decided in `permissions.py`: `ls`, `git status`, `grep` and friends run
unattended, while anything that pipes, redirects, chains, or isn't on the list
prompts. Answering *"don't ask again"* allowlists the command's prefix for the
rest of the session, except for chained commands, which are allowlisted
verbatim so `rm -rf build && uv build` never quietly grants you bare `rm`.

`--yolo` (or `/permissions off`) turns the whole thing off.

## Why a harness profile

The agent loop this is built on ships prompt guidance tuned for Anthropic's
models and OpenAI Codex, and nothing for Gemini, so out of the box Google's
models run against instructions written for someone else's. `profile.py`
registers one at the `google_genai` and `google_vertexai` provider keys,
targeting the specific ways Gemini drifts in an agentic loop: serialising tool
calls it could batch, answering about code it hasn't opened, reprinting whole
files instead of editing them, and padding terminal output with preamble.

Retune it there rather than in the system prompt: it's the supported extension
point, and it survives upgrades to the underlying loop.

## Layout

```
src/hubbleflow/
  cli.py           argument parsing, entry point
  app.py           the REPL: streaming, interrupts, permission round-trips
  agent.py         create_deep_agent wiring
  profile.py       the Gemini HarnessProfile
  permissions.py   what needs approval
  commands.py      slash commands
  config.py        model, workspace, session, API key resolution
  models.py        provider discovery (Gemini, NVIDIA, Mesh, FreeToken, Ollama)
  knowledge.py     reads an OKF bundle -- types, tags, staleness, links
  mcp.py           MCP server config and connection
  cache.py         rolling Gemini context cache
  backend.py       filesystem that accepts workspace and real paths alike
  tools/shell.py   the bash tool
  tools/web.py     web_search and web_fetch
  tools/knowledge.py  knowledge_lookup, and save_research that writes OKF
  ui/              banner, composer, approval dialog, transcript renderer
  ui/graph.py      the knowledge bundle as a standalone HTML page
skills/            the web-research skill, copied to ~/.hubbleflow/skills
tests/             the whole loop, driven by a scripted model, no API key needed
```

```bash
uv run pytest          # 150 tests, no API key or running service required
./install.sh --dev     # install editable, so the working tree is what runs
```

The suite runs entirely offline: the mesh tests spin up a stub OpenAI-compatible
server -- serving its console as well as its API, so a real `mesh-llm` running on
the same machine can't change the result -- the agent tests drive a scripted
`BaseChatModel`, and nothing reaches a provider. Two tests skip unless you're on
Windows.

---

[← README](../README.md)
