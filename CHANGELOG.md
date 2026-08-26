# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Project instructions.** `HUBBLEFLOW.md`, `AGENTS.md` or `CLAUDE.md` in the
  workspace root is appended to the system prompt. First match wins, since a
  repo with two of them almost always has the same text in each. Configurable
  with `HUBBLEFLOW_CONTEXT_FILES`.
- **Knowledge bundles.** An [OKF](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf)
  directory -- what OpenWiki 0.2 generates -- is mounted read-only at
  `/knowledge/` and searchable through a new `knowledge_lookup` tool, so the
  agent finds a page from its frontmatter instead of grepping the tree.
  `status: deprecated` and an elapsed `stale_after` are reported with every hit
  rather than filtered, because a bundle that has silently gone stale is worse
  than no bundle. Point it elsewhere with `HUBBLEFLOW_KNOWLEDGE`.
- **`/knowledge`** browses the bundle by type, filters it, and writes a
  standalone HTML node view with `--graph`.
- **`/skills`** lists loaded skills, prints one, and scaffolds a new project
  skill with `/skills new <name>`.
- **FreeToken** joins Gemini, NVIDIA, Mesh and Ollama. It serves MoE models from
  a consumer GPU over an OpenAI-compatible API on `localhost:1919`, so it needed
  a client and a discovery call, nothing more. `FREETOKEN_URL` moves it.
- Ollama sessions now request an explicit context window. Ollama sizes it from
  free VRAM when nobody asks and settles on 4096 for a large model, which the
  system prompt and tool declarations overrun before the first user turn. The
  default is 32k, overridable with `HUBBLEFLOW_NUM_CTX`.
- `./install.sh --dev` installs editable, so a contributor's edits to `src/`
  take effect without reinstalling.
- CI across Linux, macOS and Windows, so the Windows integration tests actually
  run somewhere.
- `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, and this changelog.

### Changed

- Hosted sessions compact at 256k tokens rather than 170k, and the number is now
  `HUBBLEFLOW_COMPACT_AFTER`. A million-token window has no reason to discard
  context that was already paid for and still fits.

### Fixed

- **Compaction could never fire on a local model.** The trigger was a single
  constant sized for Gemini, so a session in a 32k window died on context size
  long before the middleware would have summarised anything. Local triggers are
  now derived from the window, less what the summarisation call itself needs --
  it resends the transcript and writes a summary, so both the prompt and the
  output have to fit alongside.
- `install.sh` discarded uv's output, so a failed install reported only its own
  generic message. The error now reaches you.
- The mesh test fixture stubbed the node's API but not its console, so
  `mesh_posture()` reached whatever real node was listening on the machine and
  the consume-only guard failed the suite for anyone running `mesh-llm serve`.
  The stub now serves its own console.

## [0.1.0]

Initial release.

- A terminal harness over DeepAgents: streaming transcript, approval dialogs,
  slash commands, `@` path completion, session resume via SQLite checkpointer.
- Four providers discovered live — Gemini, NVIDIA, Mesh LLM, Ollama — with
  provider inferred from a bare model name.
- A Gemini `HarnessProfile`, since DeepAgents ships tuned profiles for
  Anthropic and OpenAI models and none for Google's.
- Rolling explicit context caching for Gemini, off by default. Measured at 92%
  of tokens served from cache, net 78% cheaper.
- `web_search` and `web_fetch`, which DeepAgents does not ship.
- MCP support reading `.mcp.json` in Claude Code's format, stdio and HTTP.
- Windows support: PowerShell rather than `sh`, cmdlet-aware read-only
  allowlist.
