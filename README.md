# Hubbleflow

[![CI](https://github.com/hubbleflow-ai/hubbleflow-agent-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/hubbleflow-ai/hubbleflow-agent-harness/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-150-brightgreen.svg)](tests/)

A coding harness that runs on the GPU you already own — **Ollama**,
**FreeToken**, or a **Mesh LLM** node — with no API key and no account.
**Gemini** and **NVIDIA** are there when you want them, not assumed.

```
hubbleflow
```

## Demo

<!-- The clips live in videos/, which is gitignored -- git cannot delta-compress
     video and 26 MB per file would weigh on every clone. Attach them to a
     release, or drag them into an issue for a CDN link, then replace the URLs
     below. GitHub renders an .mp4 link inline once it is on its own CDN. -->

| | |
|---|---|
| [Walkthrough](https://github.com/hubbleflow-ai/hubbleflow-agent-harness/releases) | the harness end to end |
| [Local model](https://github.com/hubbleflow-ai/hubbleflow-agent-harness/releases) | running against Ollama, no key |

## What it is

A full coding agent in the terminal: it reads and edits files, runs commands,
searches the web, dispatches subagents, keeps a todo list, and asks before it
touches anything. What's different is where the model lives — a 12B on your own
SSD runs the same loop Gemini does, and switching between them is one command.

| Claude Code | here |
|---|---|
| Read / Write / Edit / Glob / Grep | a sandboxed filesystem rooted at your cwd |
| Bash | `hubbleflow/tools/shell.py` — persistent working directory, timeouts, truncation |
| Task (subagents) | a read-only `explore` subagent |
| TodoWrite | a checklist the agent keeps as it works |
| Permission prompts | `hubbleflow/permissions.py` — reads free, writes and commands ask |
| `--continue` | a SQLite checkpointer keyed by workspace |
| Auto-compaction | sized to the model's own window, not a fixed number |
| WebSearch / WebFetch | `tools/web.py` — grounded search, pages boiled down to markdown |
| MCP servers | `mcp.py`, reading `.mcp.json` in Claude Code's format |
| Model switching | seven providers, local and hosted, discovered live |
| Skills | a `web-research` skill in `~/.hubbleflow/skills`, plus `/skills` to add more |
| `CLAUDE.md` | `AGENTS.md`, `CLAUDE.md` or `HUBBLEFLOW.md`, read from the workspace root |
| — | `/knowledge`: an OKF bundle mounted read-only, with a lookup tool |

## Install

macOS and Linux:

```bash
./install.sh
```

Windows (PowerShell):

```powershell
.\install.ps1
```

The script installs [uv](https://docs.astral.sh/uv/) if you don't have it, puts
`hubbleflow` on your PATH, and creates `~/.hubbleflow/.env` for your keys. uv
fetches its own Python, so you don't need 3.13 installed first.

**No key is required to start.** A local Ollama model or a Mesh node runs
without one. For Gemini put `GOOGLE_API_KEY` in `~/.hubbleflow/.env`
([get one](https://aistudio.google.com/apikey)); for NVIDIA, `NVIDIA_API_KEY`
([get one](https://build.nvidia.com)). Keys are also read from your shell and
from a `.env` in the workspace, in that order of precedence.

Set `HUBBLEFLOW_HOME` to keep config somewhere other than `~/.hubbleflow`.

### Removing it

```bash
./uninstall.sh            # removes the command, keeps your config
./uninstall.sh --purge    # also deletes ~/.hubbleflow, after confirming
```

```powershell
.\uninstall.ps1
.\uninstall.ps1 -Purge
```

Uninstalling releases any active context caches first — storage bills by the
hour, and once the command is gone there's no way left to clean them up.

## Platforms

macOS and Linux are what this is developed and tested on. Windows is supported:
the agent's shell tool drives PowerShell rather than `sh`, the read-only
allowlist covers PowerShell cmdlets so `Get-ChildItem` doesn't prompt, and
`Ctrl+C` degrades to not cancelling a run (Python can't install an asyncio
signal handler there). The Windows *decisions* are covered by tests that run on
any platform — which shell is invoked, how its output is parsed, which cmdlets
skip approval — but the integration tests that need a real PowerShell skip off
Windows, so treat Windows as supported-but-unverified until someone runs it.

## Using it

```bash
hubbleflow                            # start in the current directory
hubbleflow -c                         # resume this directory's last session
hubbleflow -C ~/code/thing            # start somewhere else
hubbleflow -p "what does main.py do"  # one shot, print, exit
hubbleflow -m gemini-2.5-pro          # a Gemini model
hubbleflow -m gemma4:12b              # a local Ollama model, no key needed
hubbleflow -m meta/muse-glimmer-30b   # an NVIDIA-hosted model
hubbleflow --yolo                     # never ask before writing or running
hubbleflow --cache                    # cache the conversation prefix (off by default)
hubbleflow --purge-caches             # release every cache, then exit
```

In the composer: `/` for commands, `@` to complete a file path, `Esc+Enter` or a
trailing `\` for a newline, `Ctrl+C` to interrupt a run, `Ctrl+D` to leave.

`/help` `/model` `/models` `/local` `/mesh` `/cloud` `/skills` `/knowledge`
`/tools` `/mcp` `/permissions` `/allowed` `/cache` `/usage` `/cwd` `/clear`
`/exit`

Leave with `/exit` (or `Ctrl+D`). Either releases any active cache on the way
out — `Ctrl+C` never exits; it cancels the running turn.

## Models

Seven providers, discovered live rather than from a hardcoded list:

| | | key |
|---|---|---|
| **Gemini** | Google's hosted models | `GOOGLE_API_KEY` |
| **NVIDIA** | hosted NIM endpoints | `NVIDIA_API_KEY` |
| **Mesh** | [mesh-llm](https://github.com/Mesh-LLM/mesh-llm) pooling GPUs across machines | none |
| **vLLM**, **llama.cpp**, **FreeToken** | servers you run yourself, on your own hardware | none |
| **Ollama** | whatever you've pulled | none |

`/models` lists everything, `/local` is the self-hosted pair (Ollama and Mesh),
`/mesh` is just the mesh, `/cloud` is Gemini and NVIDIA.
They all take a filter — `/cloud muse` when you know roughly what you want, and
NVIDIA alone serves 80-odd chat models.

`/model <name>` switches without restarting and works the provider out from the
name. That's fiddlier than it sounds: Ollama tags contain colons (`gemma4:12b`)
so a colon doesn't imply a provider prefix, and both Ollama and NVIDIA use
slashes, so a pulled model always wins over the guess. A name in no catalogue at
all resolves to NVIDIA and is passed through — the model you want to try is
usually newer than any list of models.

The NVIDIA list comes from NVIDIA's own `/models` endpoint, deliberately not
`ChatNVIDIA.get_available_models()`: that one filters the live listing down to
models in the table bundled with the installed library, which hides everything
released since.

### Mesh LLM

`mesh-llm serve --auto` pools GPUs across machines behind one OpenAI-compatible
API, so there's no integration to write — an OpenAI client aimed at the node is
the whole thing. Point `MESH_LLM_URL` elsewhere if your node isn't on the
default `http://localhost:9337/v1`.

Mesh model names carry no distinguishing syntax, so like Ollama they're
recognised by asking the node what it serves. No node running means no mesh
models offered, and nothing breaks.

**Consume-only by default.** A mesh node can also give compute back — serving or
hosting models, publishing itself for discovery, offering VRAM. That's a
deliberate choice, so the harness won't make it for you: it reads the node's
console and declines to use one that is contributing, naming exactly why.
`/mesh` states the node's posture on every listing.

```
node  client-only  standby, 17 peers, giving no compute
```

Start the node as a pure consumer with `mesh-llm client --auto` — it needs no
local model at all. Set `HUBBLEFLOW_MESH_ALLOW_HOST=1` if you do want to use a
node that contributes.

`/mesh` also shows each model's context length, because most mesh models are too
small to hold this harness — the system prompt and tool declarations alone are
about 5k tokens, so anything under 8k can't work.

One thing worth being deliberate about: in mesh mode your prompts, and the file
contents the agent reads, are processed on other people's machines.

### Servers you run yourself

Three of them, and the harness treats them as one shape: a base URL with the
OpenAI protocol behind it. Start any of them and `/local` finds it.

| | Start it with | Default | Move it with |
|---|---|---|---|
| **vLLM** | `vllm serve <model>` | `:8000/v1` | `VLLM_URL` |
| **llama.cpp** | `llama-server -m <model.gguf>` | `:8080/v1` | `LLAMACPP_URL` |
| **[FreeToken](https://github.com/FlashML-org/FreeToken)** | `ft serve --model <path>` | `:1919/v1` | `FREETOKEN_URL` |

There is no integration to write for any of them — an OpenAI client aimed at the
right port is the whole thing, which is why they share one code path and differ
only by a row in `models.LOCAL_SERVERS`. Adding a fourth is that row.

Running two at once is fine; they're discovered independently and the ports
don't collide. The `_URL` variables also point at another machine, which matters
for FreeToken in particular — it targets NVIDIA RTX cards, and there is no CUDA
on an Apple Silicon Mac.

### Ollama

Anything you've pulled shows up automatically -- the list comes from the daemon's
`/api/tags`, so `ollama pull` is the whole install step. Embedding, reranking and
OCR models are filtered out, because a coding agent needs tool calling.

A GGUF you downloaded some other way -- from Hugging Face, or Unsloth -- is not
enough on its own. Ollama keeps a content-addressed store of its own and only
knows about models registered through `ollama pull` or `ollama create`; a loose
file, wherever it sits, is invisible to it. Point a `Modelfile` at the GGUF and
`ollama create` it, and it appears here on the next `/models`.

**Context window.** Ollama sizes the window from free VRAM when nobody asks, and
settles on 4096 for a large model. The system prompt and tool declarations spend
more than that before you have typed anything, so the first turn dies with
`exceed_context_size_error`. The harness asks for 32k instead, per request, which
also keeps the sizing here rather than in whatever the server was last started
with. Override with `HUBBLEFLOW_NUM_CTX`.

Prompt caching needs no setup: llama.cpp reuses the KV prefix between turns, and
a warm prefix reprocesses about 50x faster than a cold one. It holds only while
the prefix stays byte-identical, so anything that varies the system prompt --
a timestamp, a reordered tool list -- quietly pays full price every turn.

### Timeouts

Hosted models vary wildly — one NVIDIA model answered a one-line task in 5s and
another needed 70s. The NVIDIA client defaults to a 60s timeout and a
1024-token output cap, both of which cut real work short, so the harness sets
300s and 8192 instead. Override with `HUBBLEFLOW_TIMEOUT` and
`HUBBLEFLOW_MAX_TOKENS`.

Running a local or meshed model needs no API key at all. Web search still does — it's
Google Search grounding, which goes through the Gemini API whatever the session
model is.

### Compaction

An agentic loop resends the whole conversation every turn, so eventually it has
to be summarised. Where that happens depends on the window:

| | Compacts at |
|---|---|
| Gemini | 256k tokens |
| NVIDIA | 96k — its `/models` advertises no context length, and most NIM endpoints are 128k, a few 32k. The default has to assume the common case |
| Ollama, Mesh, vLLM, llama.cpp, FreeToken | derived from the window, ~18.5k at the 32k default |

`HUBBLEFLOW_COMPACT_AFTER` overrides the hosted numbers. `/usage` shows how much
of the window the conversation currently holds and whether it has compacted —
the sent counter can't tell you that, since it's cumulative spend rather than
transcript size.

The local number isn't a fraction picked by feel. Compacting is itself a model
call: it resends the transcript and writes a summary, so the trigger has to
clear the window by the prompt that always rides along *and* the output the
summary needs. A single constant tuned for a hosted window can never fire in a
32k one -- the request dies on context size first, which is what used to happen.

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

**Staleness travels with every result.** A bundle that has quietly gone out of
date is worse than no bundle, so `status: deprecated` and an elapsed
`stale_after` are reported on the page rather than filtered out -- the agent can
then say a page is stale instead of repeating it confidently.

The reader is deliberately forgiving, because the spec requires it to be: an
unknown `type`, an unrecognised key, broken YAML, a missing `index.md` or a
dangling cross-link all cost a page its metadata and nothing more. Nothing in a
malformed bundle stops the session.

## Web access

`web_search` is Gemini's Google Search grounding — no second API account, and it
returns real source URLs rather than the redirect wrappers grounding hands back.
`web_fetch` pulls a page down to markdown, dropping nav and script chrome.

Both live in `tools/web.py`. The `web-research`
skill in `~/.hubbleflow/skills` teaches the workflow around them: search with a
question, read the primary source before relying on it, and check findings
against the project's own lockfile.

A fetch asks for approval per host — saying yes to one page on `docs.astral.sh`
covers the rest of that domain. Search isn't gated.

## Context caching

Off unless you ask for it: `hubbleflow --cache` (or `--cache=1`, or
`HUBBLEFLOW_CACHE=1`). `/cache on` and `/cache off` flip it mid-session, and
`/cache` alone reports what it has saved.

An agentic loop resends the whole conversation on every model call, so most of
what you pay for is tokens the model has already seen. Gemini bills a cached
token at a tenth of a fresh one, so `cache.py` keeps a rolling explicit cache of
the stable prefix — system prompt, tool declarations, and the conversation up to
the last complete turn — and sends only the tail.

Measured on a real session: **$0.0407 of input became $0.0089**, with $0.0033 of
storage against it. 92% of tokens served from cache, net 78% cheaper.

Two things make it work. Gemini rejects a request that names a cache *and* sets
`system_instruction`, `tools` or `tool_config` — those have to live inside the
cache — so `CachingChatGoogle` intercepts request assembly to move them there.
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
down. `/mcp` shows what came up. Every MCP tool needs approval the first time —
they come from outside this codebase and can do whatever their server can.

## Approvals

Reads are free; writes and commands pause and ask. What counts as a read is
decided in `permissions.py`: `ls`, `git status`, `grep` and friends run
unattended, while anything that pipes, redirects, chains, or isn't on the list
prompts. Answering *"don't ask again"* allowlists the command's prefix for the
rest of the session — except for chained commands, which are allowlisted
verbatim so `rm -rf build && uv build` never quietly grants you bare `rm`.

`--yolo` (or `/permissions off`) turns the whole thing off.

## Why a harness profile

The agent loop this is built on ships prompt guidance tuned for Anthropic's
models and OpenAI Codex, and nothing for Gemini — so out of the box Google's
models run against instructions written for someone else's. `profile.py`
registers one at the `google_genai` and `google_vertexai` provider keys,
targeting the specific ways Gemini drifts in an agentic loop: serialising tool
calls it could batch, answering about code it hasn't opened, reprinting whole
files instead of editing them, and padding terminal output with preamble.

Retune it there rather than in the system prompt: it's the supported extension
point, and it survives upgrades to the underlying loop.

## Configuration

Everything is an environment variable, read from your shell, from
`~/.hubbleflow/.env`, or from a `.env` in the workspace — in that order of
precedence. Nothing here is required; the defaults are what the sections above
describe.

**Keys**

| | |
|---|---|
| `GOOGLE_API_KEY` | Gemini. `GEMINI_API_KEY` is accepted too, since that's the name AI Studio hands you |
| `NVIDIA_API_KEY` | NVIDIA-hosted models. `NVIDIA_API_TOKEN` also works |

Ollama, Mesh and FreeToken need no key at all.

**Model and limits**

| | Default |
|---|---|
| `HUBBLEFLOW_MODEL` | `google_genai:gemini-3.5-flash-lite` |
| `HUBBLEFLOW_TIMEOUT` | `300` seconds |
| `HUBBLEFLOW_MAX_TOKENS` | `8192` output tokens |
| `HUBBLEFLOW_NUM_CTX` | `32768` — the context window asked of a local model |
| `HUBBLEFLOW_COMPACT_AFTER` | `256000` — where a *hosted* session compacts; local is derived from the window |
| `HUBBLEFLOW_SEARCH_MODEL` | `gemini-3.5-flash-lite` — the model behind `web_search` |

**Where things live**

| | Default |
|---|---|
| `HUBBLEFLOW_HOME` | `~/.hubbleflow` — keys, sessions, history, skills |
| `HUBBLEFLOW_CONTEXT_FILES` | `HUBBLEFLOW.md,AGENTS.md,CLAUDE.md`; empty disables the lookup |
| `HUBBLEFLOW_KNOWLEDGE` | `./openwiki` if it exists; empty disables it |

**Providers**

| | Default |
|---|---|
| `OLLAMA_HOST` | `http://localhost:11434` |
| `MESH_LLM_URL` | `http://localhost:9337/v1` |
| `MESH_LLM_CONSOLE_PORT` | the port `/mesh` reads a node's posture from |
| `FREETOKEN_URL` | `http://localhost:1919/v1` |
| `VLLM_URL` | `http://localhost:8000/v1` |
| `LLAMACPP_URL` | `http://localhost:8080/v1` |
| `NVIDIA_BASE_URL` | NVIDIA's own endpoint |
| `HUBBLEFLOW_MESH_ALLOW_HOST` | `1` uses a mesh node that also contributes compute |

**Behaviour**

| | Default |
|---|---|
| `HUBBLEFLOW_CACHE` | off — `1` turns on Gemini context caching, same as `--cache` |
| `HUBBLEFLOW_AUTO_APPROVE` | off — `1` is `--yolo`, and nothing will ask before writing or running |
| `HUBBLEFLOW_UNSANDBOXED` | off — **`1` lets the agent read and write outside the workspace.** The sandbox is the thing standing between a confused agent and the rest of your disk; turning it off is a deliberate choice, not a convenience |
| `HUBBLEFLOW_PRICE_IN`<br>`HUBBLEFLOW_PRICE_OUT`<br>`HUBBLEFLOW_PRICE_CACHED` | per-million-token rates for `/usage`, for when the built-in table goes stale |

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
  tools/knowledge.py  knowledge_lookup over the OKF bundle
  ui/              banner, composer, approval dialog, transcript renderer
  ui/graph.py      the knowledge bundle as a standalone HTML page
skills/            the web-research skill, copied to ~/.hubbleflow/skills
tests/             the whole loop, driven by a scripted model — no API key needed
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

## Licence

[Apache 2.0](LICENSE).
