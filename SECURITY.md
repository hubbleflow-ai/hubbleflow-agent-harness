# Security

## What this tool does

Hubbleflow is an agentic coding harness. In normal use it **reads your files,
writes to them, and runs shell commands on your machine**, driven by a language
model. That is the feature, and it is also the threat model.

Before reporting, please be clear which of these you've found:

- **Working as designed.** The agent ran a command you approved. `--yolo` and
  `/permissions off` disable approvals entirely, by request.
- **A bug.** Something bypassed an approval that should have prompted, escaped
  the workspace sandbox, or leaked a key.

The second kind is what this document is for.

## Reporting a vulnerability

Please report privately through
[GitHub Security Advisories](https://github.com/hubbleflow-ai/hubbleflow-agent-harness/security/advisories/new)
rather than opening a public issue. If you would rather not use GitHub, or don't
have an account, email **aseem@hubbleflow.ai** (or **codewithaseem@gmail.com**)
instead.

Include what you did, what happened, what you expected, and the version. A
failing test is the fastest possible report.

You can expect an acknowledgement within a week, and an assessment within two.
If it's confirmed, you'll be credited in the advisory unless you'd rather not be.

## Areas worth attention

If you're looking for somewhere to start, these carry the most risk:

| Area | Why |
|---|---|
| `permissions.py` | Decides what runs without asking. A command that reads as read-only but isn't is a real bug, chained commands are allowlisted verbatim precisely to avoid granting a bare prefix. |
| `backend.py` | Enforces the workspace sandbox while accepting both virtual and host spellings of a path. Traversal out of the workspace is the thing to break. |
| `tools/shell.py` | Runs the commands. Argument construction differs on Windows (PowerShell) and POSIX. |
| `mcp.py` | MCP servers are third-party code with their own capabilities. Every MCP tool prompts on first use for that reason. |
| `config.py`, `cache.py` | Handle API keys and send conversation prefixes to Gemini. Keys must never reach a log, a cache display name, or a prompt. |

Two environment variables switch protections off deliberately, and are worth
knowing about before you report behaviour that follows from them:
`HUBBLEFLOW_AUTO_APPROVE=1` skips every approval prompt (the same as `--yolo`),
and `HUBBLEFLOW_UNSANDBOXED=1` lets the file tools reach outside the workspace.
Both are documented in the README's Configuration section.

## Things that are not vulnerabilities

- The agent running a destructive command you approved.
- `--yolo` skipping approvals. That is what it is for.
- Prompt injection causing the model to *propose* something dangerous, the
  approval layer is the control, and a report is only interesting if it shows
  that layer being bypassed.
- A mesh node seeing your prompts. `README.md` states this; mesh inference runs
  on other people's machines by design.
