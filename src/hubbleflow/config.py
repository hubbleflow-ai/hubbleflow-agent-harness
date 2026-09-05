"""Runtime configuration for the Hubbleflow harness."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

GEMINI = "google_genai"
OLLAMA = "ollama"
NVIDIA = "nvidia"
MESH = "mesh"
# Four servers that speak the OpenAI protocol on your own hardware. They
# differ only in where they listen, so the harness treats them as one shape --
# see `models.LOCAL_SERVERS`.
FREETOKEN = "freetoken"
VLLM = "vllm"
LLAMACPP = "llamacpp"
MLX = "mlx"
PROVIDERS = (GEMINI, "google_vertexai", OLLAMA, NVIDIA, MESH, FREETOKEN, VLLM, LLAMACPP, MLX)
CLOUD_PROVIDERS = (GEMINI, NVIDIA)

# Check `/models` before pinning a different id -- an AI Studio key does not
# necessarily serve every model in Google's lineup.
DEFAULT_MODEL = f"{GEMINI}:gemini-3.5-flash-lite"

# A coding turn on a large hosted model can genuinely take minutes; the NVIDIA
# client's own default is 60s, which is short enough to cut off real work.
DEFAULT_TIMEOUT = 300.0
# Its default output cap is 1024 tokens -- too small for a file write.
DEFAULT_MAX_TOKENS = 8192
# Ollama sizes the context window from free VRAM when nobody asks, and settles
# on 4096 for a large model. The system prompt and tool schemas spend more than
# that before the first user turn, so ask rather than accept the guess.
DEFAULT_NUM_CTX = 32768

# A project's own instructions, read from the workspace root. First match wins
# rather than all of them concatenated: a repo with both AGENTS.md and CLAUDE.md
# almost always has the same text in each -- OpenWiki writes its pointer block
# into both -- so reading every one would just repeat it. Most specific first.
# Where a hosted model compacts. Gemini serves a million tokens, so compacting
# early there throws away context that was already paid for and still fits.
DEFAULT_COMPACT_AFTER = 256_000
# NVIDIA is a catalogue, not a model: most NIM endpoints are 128k and a few are
# 32k. Its `/models` returns id and owner and nothing about the window, so there
# is nothing to derive from -- the default has to assume the common case rather
# than the best one, and `HUBBLEFLOW_COMPACT_AFTER` covers the rest.
DEFAULT_COMPACT_AFTER_NVIDIA = 96_000

# Where OpenWiki writes its bundle. Any OKF producer can be pointed at instead
# with HUBBLEFLOW_KNOWLEDGE; the format is a spec, not one tool's output.
DEFAULT_KNOWLEDGE_DIR = "openwiki"

CONTEXT_FILES = ("HUBBLEFLOW.md", "AGENTS.md", "CLAUDE.md")
# The file rides along on every request. On a 32k local window a long one would
# crowd out the conversation it is supposed to inform, so it is capped.
MAX_CONTEXT_CHARS = 16_000
def _config_dir() -> Path:
    """Where keys, sessions, history and skills live.

    `HUBBLEFLOW_HOME` moves the lot, which the install scripts rely on and which
    makes it possible to keep separate setups side by side.
    """
    override = os.getenv("HUBBLEFLOW_HOME", "").strip()
    return Path(override).expanduser() if override else Path.home() / ".hubbleflow"


CONFIG_DIR = _config_dir()
SESSION_DB = CONFIG_DIR / "sessions.sqlite"
STATE_FILE = CONFIG_DIR / "state.json"

# langchain-google-genai reads GOOGLE_API_KEY; people commonly export the key
# under the name the AI Studio console hands them, so accept both.
_KEY_NAMES = ("GOOGLE_API_KEY", "GEMINI_API_KEY")
_NVIDIA_KEY_NAMES = ("NVIDIA_API_KEY", "NVIDIA_API_TOKEN")


@dataclass(slots=True)
class Config:
    """Everything the harness needs to boot, resolved once at startup."""

    model: str
    workspace: Path
    session_db: Path
    thread_id: str
    resumed: bool
    auto_approve: bool
    cache: bool
    virtual_mode: bool
    global_skills: Path | None
    project_skills: Path | None
    knowledge: Path | None = None

    @property
    def model_name(self) -> str:
        """The bare model id. Ollama tags contain colons, so split only once."""
        provider, _, rest = self.model.partition(":")
        return rest if rest and provider in PROVIDERS else self.model

    @property
    def provider(self) -> str:
        provider, _, rest = self.model.partition(":")
        return provider if rest and provider in PROVIDERS else GEMINI

    @property
    def is_local(self) -> bool:
        """Runs on hardware you control, so nothing is billed per token."""
        return self.provider in (OLLAMA, MESH, FREETOKEN, VLLM, LLAMACPP, MLX)

    @classmethod
    def load(
        cls,
        *,
        workspace: Path | None = None,
        model: str | None = None,
        continue_session: bool = False,
        auto_approve: bool = False,
        cache: bool | None = None,
    ) -> "Config":
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        ws = (workspace or Path.cwd()).resolve()

        # Project .env wins over a stale shell export, matching how the rest of
        # Aseem's projects are wired.
        load_dotenv(ws / ".env", override=False)
        load_dotenv(CONFIG_DIR / ".env", override=False)
        _normalise_api_key()

        thread_id, resumed = _resolve_thread(ws, continue_session)

        return cls(
            model=model or os.getenv("HUBBLEFLOW_MODEL") or DEFAULT_MODEL,
            workspace=ws,
            session_db=SESSION_DB,
            thread_id=thread_id,
            resumed=resumed,
            auto_approve=auto_approve or _env_flag("HUBBLEFLOW_AUTO_APPROVE"),
            cache=_env_flag("HUBBLEFLOW_CACHE") if cache is None else cache,
            virtual_mode=not _env_flag("HUBBLEFLOW_UNSANDBOXED"),
            global_skills=_maybe_dir(_seed_skills()),
            project_skills=_maybe_dir(ws / ".hubbleflow" / "skills"),
            knowledge=_knowledge_root(ws),
        )


def request_timeout() -> float:
    try:
        return float(os.environ["HUBBLEFLOW_TIMEOUT"])
    except (KeyError, ValueError):
        return DEFAULT_TIMEOUT


def max_output_tokens() -> int:
    try:
        return int(os.environ["HUBBLEFLOW_MAX_TOKENS"])
    except (KeyError, ValueError):
        return DEFAULT_MAX_TOKENS


def compact_after(provider: str = GEMINI) -> int:
    """Transcript size, in tokens, at which a hosted session compacts.

    Local sessions ignore this: their trigger is derived from the window they
    actually have, which is far smaller than any sensible value here.
    """
    try:
        return int(os.environ["HUBBLEFLOW_COMPACT_AFTER"])
    except (KeyError, ValueError):
        return DEFAULT_COMPACT_AFTER_NVIDIA if provider == NVIDIA else DEFAULT_COMPACT_AFTER


def context_files() -> tuple[str, ...]:
    """Filenames to look for in the workspace root, highest precedence first.

    `HUBBLEFLOW_CONTEXT_FILES` takes a comma-separated list; empty disables the
    lookup entirely, for a workspace where someone else's AGENTS.md would only
    mislead this harness.
    """
    names = os.getenv("HUBBLEFLOW_CONTEXT_FILES")
    if names is None:
        return CONTEXT_FILES
    return tuple(n.strip() for n in names.split(",") if n.strip())


def context_window() -> int:
    try:
        return int(os.environ["HUBBLEFLOW_NUM_CTX"])
    except (KeyError, ValueError):
        return DEFAULT_NUM_CTX


def _knowledge_root(workspace: Path) -> Path | None:
    """The OKF bundle for this workspace, if there is one.

    An explicit path is taken as given -- an empty value turns the lookup off,
    which is how you decline a bundle a repo ships but you don't trust.
    """
    override = os.getenv("HUBBLEFLOW_KNOWLEDGE")
    if override is not None:
        override = override.strip()
        return _maybe_dir(Path(override).expanduser()) if override else None
    return _maybe_dir(workspace / DEFAULT_KNOWLEDGE_DIR)


def _maybe_dir(path: Path) -> Path | None:
    return path if path.is_dir() else None


def _seed_skills() -> Path:
    """Copy the skills bundled in the wheel into the user's config, once.

    Existing skills are never overwritten -- yours are yours, and an upgrade
    only fills in ones you don't have.
    """
    target = CONFIG_DIR / "skills"
    packaged = Path(__file__).parent / "skills"
    if not packaged.is_dir():
        return target

    try:
        for source in packaged.iterdir():
            if source.is_dir() and not (target / source.name).exists():
                shutil.copytree(source, target / source.name)
    except OSError:
        pass  # a read-only home shouldn't stop the harness from starting
    return target


def api_key() -> str | None:
    """Return the resolved Gemini API key, if one is configured."""
    return os.getenv("GOOGLE_API_KEY")


def nvidia_api_key() -> str | None:
    """Return the resolved NVIDIA API key, if one is configured."""
    return os.getenv("NVIDIA_API_KEY")


def key_for(provider: str) -> str | None:
    """The credential a provider needs, or None when it needs none."""
    if provider == GEMINI:
        return api_key()
    if provider == NVIDIA:
        return nvidia_api_key()
    return None


def _normalise_api_key() -> None:
    _adopt(_KEY_NAMES, "GOOGLE_API_KEY")
    _adopt(_NVIDIA_KEY_NAMES, "NVIDIA_API_KEY")


def _adopt(candidates: tuple[str, ...], canonical: str) -> None:
    """Accept any of the names a provider's console might hand you."""
    for name in candidates:
        if value := os.getenv(name):
            os.environ[canonical] = value
            return


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _resolve_thread(workspace: Path, continue_session: bool) -> tuple[str, bool]:
    """Pick a thread id, resuming this workspace's last one when asked."""
    state = _read_state()
    key = str(workspace)
    if continue_session and (previous := state.get("last_thread", {}).get(key)):
        return previous, True

    thread_id = uuid.uuid4().hex[:12]
    state.setdefault("last_thread", {})[key] = thread_id
    _write_state(state)
    return thread_id, False


def _read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _write_state(state: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except OSError:
        pass  # a read-only home shouldn't stop the harness from running
