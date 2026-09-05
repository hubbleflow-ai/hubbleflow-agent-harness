# Models

Seven providers, discovered live rather than from a hardcoded list.

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
They all take a filter, `/cloud muse` when you know roughly what you want, and
NVIDIA alone serves 80-odd chat models.

`/model <name>` switches without restarting and works the provider out from the
name. That's fiddlier than it sounds: Ollama tags contain colons (`gemma4:12b`)
so a colon doesn't imply a provider prefix, and both Ollama and NVIDIA use
slashes, so a pulled model always wins over the guess. A name in no catalogue at
all resolves to NVIDIA and is passed through, the model you want to try is
usually newer than any list of models.

The NVIDIA list comes from NVIDIA's own `/models` endpoint, deliberately not
`ChatNVIDIA.get_available_models()`: that one filters the live listing down to
models in the table bundled with the installed library, which hides everything
released since.

### Mesh LLM

`mesh-llm serve --auto` pools GPUs across machines behind one OpenAI-compatible
API, so there's no integration to write, an OpenAI client aimed at the node is
the whole thing. Point `MESH_LLM_URL` elsewhere if your node isn't on the
default `http://localhost:9337/v1`.

Mesh model names carry no distinguishing syntax, so like Ollama they're
recognised by asking the node what it serves. No node running means no mesh
models offered, and nothing breaks.

**Consume-only by default.** A mesh node can also give compute back, serving or
hosting models, publishing itself for discovery, offering VRAM. That's a
deliberate choice, so the harness won't make it for you: it reads the node's
console and declines to use one that is contributing, naming exactly why.
`/mesh` states the node's posture on every listing.

```
node  client-only  standby, 17 peers, giving no compute
```

Start the node as a pure consumer with `mesh-llm client --auto`, it needs no
local model at all. Set `HUBBLEFLOW_MESH_ALLOW_HOST=1` if you do want to use a
node that contributes.

`/mesh` also shows each model's context length, because most mesh models are too
small to hold this harness, the system prompt and tool declarations alone are
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

There is no integration to write for any of them, an OpenAI client aimed at the
right port is the whole thing, which is why they share one code path and differ
only by a row in `models.LOCAL_SERVERS`. Adding a fourth is that row.

Running two at once is fine; they're discovered independently and the ports
don't collide. The `_URL` variables also point at another machine, which matters
for FreeToken in particular, it targets NVIDIA RTX cards, and there is no CUDA
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
`exceed_context_size_error`. The harness asks for **64k** instead, per request,
which also keeps the sizing here rather than in whatever the server was last
started with. Override with `HUBBLEFLOW_NUM_CTX`.

64k rather than 32k because compaction has to happen *inside* the window. At 32k
the conversation gets about 18k before it compacts, three `web_fetch` calls,
each capped at 20k characters, so research summarises away the sources it was
about to reason over. At 64k that budget is 51k, or roughly ten pages.

What it costs depends on the attention layout, and less than you would guess for
a model that mixes global and sliding-window layers. Measured on `gemma4:12b`,
where only 8 of 48 layers attend globally:

```
llama_kv_cache: 1024 MiB (65536 cells,  8 layers)   global
llama_kv_cache:  480 MiB ( 1536 cells, 40 layers)   sliding window
```

1.5 GB, against 7.6 GB of weights, so 64k costs about 750 MB more than 32k.
A model whose every layer attends globally pays several times that, which is
when to turn the window back down.

Prompt caching needs no setup: llama.cpp reuses the KV prefix between turns, and
a warm prefix reprocesses about 50x faster than a cold one. It holds only while
the prefix stays byte-identical, so anything that varies the system prompt --
a timestamp, a reordered tool list -- quietly pays full price every turn.

### Timeouts

Hosted models vary wildly, one NVIDIA model answered a one-line task in 5s and
another needed 70s. The NVIDIA client defaults to a 60s timeout and a
1024-token output cap, both of which cut real work short, so the harness sets
300s and 8192 instead. Override with `HUBBLEFLOW_TIMEOUT` and
`HUBBLEFLOW_MAX_TOKENS`.

Running a local or meshed model needs no API key at all. Web search still does, it's
Google Search grounding, which goes through the Gemini API whatever the session
model is.

### Compaction

An agentic loop resends the whole conversation every turn, so eventually it has
to be summarised. Where that happens depends on the window:

| | Compacts at |
|---|---|
| Gemini | 256k tokens |
| NVIDIA | 96k, its `/models` advertises no context length, and most NIM endpoints are 128k, a few 32k. The default has to assume the common case |
| Ollama, Mesh, vLLM, llama.cpp, FreeToken | derived from the window, 51k at the 64k default |

`HUBBLEFLOW_COMPACT_AFTER` overrides the hosted numbers. `/usage` shows how much
of the window the conversation currently holds and whether it has compacted -
the sent counter can't tell you that, since it's cumulative spend rather than
transcript size.

The local number isn't a fraction picked by feel. Compacting is itself a model
call: it resends the transcript and writes a summary, so the trigger has to
clear the window by the prompt that always rides along *and* the output the
summary needs. A single constant tuned for a hosted window can never fire in a
32k one -- the request dies on context size first, which is what used to happen.

---

[← README](../README.md)
