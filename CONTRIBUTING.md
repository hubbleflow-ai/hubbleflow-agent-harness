# Contributing

Thanks for looking. This is a small codebase, about 3,600 lines, and it stays
readable by being fussy about a few things.

## Getting set up

```bash
git clone https://github.com/hubbleflow-ai/hubbleflow-agent-harness
cd hubbleflow
uv run pytest          # 79 tests, no API key or running service required
```

uv fetches its own Python, so you don't need 3.13 installed first. There is no
separate install step for development, `uv run` resolves the environment.

To try your changes in a real session:

```bash
uv run hubbleflow -m gemma4:12b     # a local Ollama model needs no key
```

## The tests

They run offline and must stay that way. Nothing reaches a provider: the mesh
tests spin up a stub OpenAI-compatible server, the agent tests drive a scripted
`BaseChatModel` from `tests/conftest.py`, and the Windows decisions are exercised
from any platform.

Two rules follow from that, and both have been broken before:

- **Stub the whole surface, not just the part you're thinking about.** The mesh
  fixture used to redirect the node's API but not its console, so the suite
  failed for anyone who happened to be running `mesh-llm serve`. If your fixture
  can be perturbed by something already listening on the machine, it isn't done.
- **Name the test after the behaviour.** `test_declining_stops_the_write`, not
  `test_permissions_2`. The suite reads as a description of what the harness
  promises, and that's worth keeping.

## Style

Match what's around you. Some specifics that aren't obvious:

- Comments explain **why**, not what. Most of the ones here exist because
  something surprising is true, Gemini rejecting a cached request that also
  sets `system_instruction`, Ollama sizing a context window from free VRAM. If a
  comment restates the code, delete it.
- Provider quirks belong in `profile.py` or the provider's own constructor in
  `agent.py`, not the system prompt. The profile is the supported extension
  point and composes with whatever DeepAgents adds later.
- New env vars follow the existing shape in `config.py`: a `DEFAULT_` constant
  with a comment saying why that value, and a reader that falls back on
  `KeyError, ValueError`.

## Pull requests

Keep them focused, one concern per PR. Say what changed and why; if it fixes
something, say how it failed. New behaviour needs a test, and CI runs the suite
on Linux, macOS and Windows.

## Licence

Contributions are accepted under [Apache 2.0](LICENSE), the licence this project
ships under.
