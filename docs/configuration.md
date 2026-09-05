# Configuration

Every knob, and what it defaults to.

## Configuration

Everything is an environment variable, read from your shell, from
`~/.hubbleflow/.env`, or from a `.env` in the workspace, in that order of
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
| `HUBBLEFLOW_NUM_CTX` | `65536`, the context window asked of a local model |
| `HUBBLEFLOW_COMPACT_AFTER` | `256000`, where a *hosted* session compacts; local is derived from the window |
| `HUBBLEFLOW_SEARCH_MODEL` | `gemini-3.5-flash-lite`, the model behind `web_search` |

**Where things live**

| | Default |
|---|---|
| `HUBBLEFLOW_HOME` | `~/.hubbleflow`, keys, sessions, history, skills |
| `HUBBLEFLOW_CONTEXT_FILES` | `HUBBLEFLOW.md,AGENTS.md,CLAUDE.md`; empty disables the lookup |
| `HUBBLEFLOW_KNOWLEDGE` | `./openwiki` if it exists; empty disables it |
| `HUBBLEFLOW_RESEARCH` | `./.hubbleflow/knowledge`, where `/deep-research` files findings; empty disables it |

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
| `HUBBLEFLOW_CACHE` | off, `1` turns on Gemini context caching, same as `--cache` |
| `HUBBLEFLOW_AUTO_APPROVE` | off, `1` is `--yolo`, and nothing will ask before writing or running |
| `HUBBLEFLOW_UNSANDBOXED` | off, **`1` lets the agent read and write outside the workspace.** The sandbox is the thing standing between a confused agent and the rest of your disk; turning it off is a deliberate choice, not a convenience |
| `HUBBLEFLOW_PRICE_IN`<br>`HUBBLEFLOW_PRICE_OUT`<br>`HUBBLEFLOW_PRICE_CACHED` | per-million-token rates for `/usage`, for when the built-in table goes stale |

---

[← README](../README.md)
