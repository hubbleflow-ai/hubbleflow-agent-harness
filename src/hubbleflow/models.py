"""Finding out which models you can actually run.

Two catalogues: whatever the Gemini key serves, and whatever Ollama has pulled
locally. Both are looked up live, because neither list is knowable in advance.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from functools import lru_cache

import httpx

from hubbleflow import config as config_module
from hubbleflow.config import (
    FREETOKEN,
    GEMINI,
    LLAMACPP,
    MESH,
    NVIDIA,
    OLLAMA,
    PROVIDERS,
    VLLM,
)

OLLAMA_TIMEOUT = 1.5
GEMINI_TIMEOUT = 10.0

# Ollama serves plenty of models that aren't chat models; a coding agent needs
# tool calling, and these families can't do it.
_NOT_CHAT = ("embed", "bge-", "-ocr", "rerank", "whisper", "clip", "guard", "-tts", "-asr", "retrieval")


def ollama_host() -> str:
    host = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    return host if host.startswith("http") else f"http://{host}"


def list_ollama() -> list[str]:
    """Models pulled locally, or an empty list when Ollama isn't running."""
    return [name for name, _ in list_ollama_details()]


def list_ollama_details() -> list[tuple[str, float]]:
    """Local models as (name, size in GB); empty when Ollama isn't running."""
    try:
        response = httpx.get(f"{ollama_host()}/api/tags", timeout=OLLAMA_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return []

    found = []
    for entry in payload.get("models") or []:
        name = entry.get("model") or entry.get("name") or ""
        if name and not any(marker in name.lower() for marker in _NOT_CHAT):
            found.append((name, round((entry.get("size") or 0) / 1e9, 1)))
    return sorted(found)


def list_gemini() -> list[str]:
    """Gemini models this key can call, or an empty list if the API is unreachable."""
    try:
        from google import genai

        client = genai.Client(api_key=config_module.api_key())
        names = []
        for model in client.models.list():
            actions = getattr(model, "supported_actions", None) or []
            if actions and "generateContent" not in actions:
                continue
            name = (model.name or "").removeprefix("models/")
            if name.startswith("gemini") and not any(m in name for m in ("image", "tts", "embed")):
                names.append(name)
        return sorted(set(names))
    except Exception:
        return []


NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_TIMEOUT = 8.0


def nvidia_base_url() -> str:
    """Override to point at a self-hosted NIM instead of NVIDIA's endpoint."""
    return os.getenv("NVIDIA_BASE_URL", NVIDIA_BASE_URL).rstrip("/")


def list_nvidia() -> list[str]:
    """Chat models NVIDIA is serving, asked of NVIDIA rather than a bundled table.

    `ChatNVIDIA.get_available_models()` answers from the static table shipped
    with the library, so anything released since that release is invisible --
    `meta/muse-glimmer-30b` among them. The `/models` endpoint is the source of
    truth, and it answers unauthenticated; a key narrows it to your account.
    """
    key = config_module.nvidia_api_key()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        response = httpx.get(f"{nvidia_base_url()}/models", headers=headers, timeout=NVIDIA_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return []

    names = [
        entry["id"]
        for entry in payload.get("data") or []
        if entry.get("id") and not any(marker in entry["id"].lower() for marker in _NOT_CHAT)
    ]
    return sorted(set(names))


MESH_URL = "http://localhost:9337/v1"
MESH_TIMEOUT = 2.0


def mesh_url() -> str:
    """Where the local mesh-llm node exposes its OpenAI-compatible API."""
    return os.getenv("MESH_LLM_URL", MESH_URL).rstrip("/")


def mesh_installed() -> bool:
    """Whether the mesh-llm CLI is on PATH at all."""
    return shutil.which("mesh-llm") is not None


# Our system prompt plus tool declarations is around 5k tokens, so a model
# needs meaningfully more than that before any conversation fits.
MIN_USABLE_CONTEXT = 8192

# The mesh is used as a consumer only. A node that is also serving or hosting
# models is doing work for other people on this machine's hardware, which is a
# deliberate choice and not one to make by accident -- so the harness declines
# to use such a node unless explicitly told otherwise.
MESH_CONSOLE_PORT = 3131


@dataclass(frozen=True, slots=True)
class MeshPosture:
    """What a mesh node is doing, beyond answering our requests."""

    reachable: bool = False
    node_state: str = ""
    serving: tuple[str, ...] = ()
    hosting: tuple[str, ...] = ()
    published: bool = False
    local_serving: bool = False
    vram_offered: float = 0.0
    peers: int = 0

    @property
    def contributing(self) -> bool:
        """True when this node is giving compute to the mesh, not just taking."""
        return bool(
            self.serving or self.hosting or self.published or self.local_serving or self.vram_offered > 0
        )

    def why(self) -> str:
        reasons = []
        if self.serving:
            reasons.append(f"serving {', '.join(self.serving)}")
        if self.hosting:
            reasons.append(f"hosting {', '.join(self.hosting)}")
        if self.local_serving:
            reasons.append("local_serving is on")
        if self.published:
            reasons.append("published for discovery")
        if self.vram_offered > 0:
            reasons.append(f"offering {self.vram_offered:.0f}GB of VRAM")
        return "; ".join(reasons) or "client-only"


def mesh_console_url() -> str:
    base = mesh_url().rsplit(":", 1)[0] if mesh_url().count(":") > 1 else "http://localhost"
    return f"{base}:{os.getenv('MESH_LLM_CONSOLE_PORT', str(MESH_CONSOLE_PORT))}"


def mesh_posture() -> MeshPosture:
    """Ask the node's console what it is doing for the mesh."""
    try:
        response = httpx.get(f"{mesh_console_url()}/api/status", timeout=MESH_TIMEOUT)
        response.raise_for_status()
        d = response.json()
    except Exception:
        return MeshPosture()

    caps = (d.get("runtime") or {}).get("capabilities") or {}
    return MeshPosture(
        reachable=True,
        node_state=str(d.get("node_status") or d.get("node_state") or ""),
        serving=tuple(_names(d.get("serving_models"))),
        hosting=tuple(_names(d.get("hosted_models"))),
        published=str(d.get("publication_state", "")).lower() not in {"", "private", "unpublished"},
        local_serving=bool(caps.get("local_serving")),
        vram_offered=float(d.get("my_vram_gb") or 0.0),
        peers=len(d.get("peers") or []),
    )


def _names(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [v if isinstance(v, str) else str(v.get("id") or v.get("name") or v) for v in value]


def mesh_client_only_required() -> bool:
    """Set HUBBLEFLOW_MESH_ALLOW_HOST=1 to use a node that also contributes."""
    return os.getenv("HUBBLEFLOW_MESH_ALLOW_HOST", "").strip().lower() not in {"1", "true", "yes", "on"}


def list_mesh() -> list[str]:
    """Models available through the mesh, when the node is one we'll use."""
    return [name for name, _ in list_mesh_details()]


def list_mesh_details() -> list[tuple[str, int]]:
    """Mesh models as (name, context length). Context is 0 when unadvertised.

    Returns nothing when the node is contributing compute rather than only
    consuming it, so a host node is never used by accident.
    """
    if mesh_client_only_required() and mesh_posture().contributing:
        return []
    try:
        response = httpx.get(f"{mesh_url()}/models", timeout=MESH_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return []

    found = []
    for entry in payload.get("data") or []:
        name = entry.get("id")
        if not name or any(m in name.lower() for m in _NOT_CHAT):
            continue
        context = int((entry.get("metadata") or {}).get("context_length") or 0)
        found.append((name, context))
    return sorted(set(found))


@dataclass(frozen=True, slots=True)
class LocalServer:
    """A server you run yourself that speaks the OpenAI protocol.

    vLLM, llama.cpp's `llama-server` and FreeToken are different programs with
    different reasons to exist, but from here they are the same thing: a base
    URL with `/models` and `/chat/completions` behind it. Giving each its own
    discovery function and its own client would be three copies of one idea,
    so they get one of each and differ only in this table.
    """

    provider: str
    label: str
    blurb: str
    default_url: str
    env: str
    start: str


# Default ports are each project's own: vLLM serves 8000, llama-server 8080,
# FreeToken 1919. Nothing here assumes only one of them is running.
LOCAL_SERVERS: tuple[LocalServer, ...] = (
    LocalServer(
        FREETOKEN, "FreeToken", "MoE models on your own GPU",
        "http://localhost:1919/v1", "FREETOKEN_URL", "ft serve --model <path>",
    ),
    LocalServer(
        VLLM, "vLLM", "high-throughput serving, one model per server",
        "http://localhost:8000/v1", "VLLM_URL", "vllm serve <model>",
    ),
    LocalServer(
        LLAMACPP, "llama.cpp", "llama-server, a GGUF straight off disk",
        "http://localhost:8080/v1", "LLAMACPP_URL", "llama-server -m <model.gguf>",
    ),
)

SERVER_TIMEOUT = 2.0

_BY_PROVIDER = {server.provider: server for server in LOCAL_SERVERS}


def local_server(provider: str) -> LocalServer | None:
    return _BY_PROVIDER.get(provider)


def server_url(server: LocalServer) -> str:
    """Where it listens. The env var moves it, including to another machine."""
    return os.getenv(server.env, server.default_url).rstrip("/")


def list_server(server: LocalServer) -> list[str]:
    """Models it is holding, or nothing at all when it isn't running."""
    return [name for name, _ in list_server_details(server)]


def list_server_details(server: LocalServer) -> list[tuple[str, int]]:
    """Models as (name, context length); context is 0 when unadvertised.

    vLLM reports `max_model_len`, FreeToken `context_length`, llama.cpp neither.
    All three are optional in the OpenAI schema, so a missing one is normal.
    """
    try:
        response = httpx.get(f"{server_url(server)}/models", timeout=SERVER_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return []

    found = []
    for entry in payload.get("data") or []:
        name = entry.get("id")
        if not name or any(m in name.lower() for m in _NOT_CHAT):
            continue
        meta = entry.get("metadata") or {}
        context = int(
            entry.get("max_model_len")
            or entry.get("context_length")
            or meta.get("context_length")
            or 0
        )
        found.append((str(name), context))
    return sorted(set(found))


def catalogue() -> list[tuple[str, list[str]]]:
    """Everything reachable right now, grouped by provider."""
    return [(GEMINI, list_gemini()), (NVIDIA, list_nvidia()), (MESH, list_mesh()),
            *[(server.provider, list_server(server)) for server in LOCAL_SERVERS],
            (OLLAMA, list_ollama())]


def resolve(spec: str) -> str:
    """Turn what the user typed into a LangChain model spec.

    Ollama tags contain colons (`gemma4:12b`), so a colon alone doesn't mean a
    provider prefix -- a local model wins over reading `gemma4` as a provider.
    """
    spec = spec.strip()
    provider, _, rest = spec.partition(":")
    if rest and provider in PROVIDERS:
        return spec

    # A mesh model has no distinguishing syntax, so like Ollama it has to be
    # recognised by asking what is actually being served.
    if spec in _mesh_models():
        return f"{MESH}:{spec}"

    # Same reasoning as the mesh: a served model id carries no marker, so the
    # only way to recognise one is to ask each server what it has loaded. First
    # match wins, and the order is the table's -- running two servers with the
    # same model on both is not a case worth disambiguating.
    for server in LOCAL_SERVERS:
        if spec in _server_models(server.provider):
            return f"{server.provider}:{spec}"

    local = _local_models()
    if spec in local:
        return f"{OLLAMA}:{spec}"
    if f"{spec}:latest" in local:
        return f"{OLLAMA}:{spec}:latest"
    # `gemma4` when the only local tag is `gemma4:12b` -- unambiguous, so take it.
    tagged = [name for name in local if name.partition(":")[0] == spec]
    if len(tagged) == 1:
        return f"{OLLAMA}:{tagged[0]}"
    # NVIDIA publishes `vendor/model` names. Gemini ids never contain a slash,
    # and a local tag that does was already matched above, so this is safe --
    # and it passes through names too new to be in any catalogue.
    if "/" in spec:
        return f"{NVIDIA}:{spec}"
    return f"{GEMINI}:{spec}"


def split(spec: str) -> tuple[str, str]:
    """Split a spec into (provider, model name), keeping colons in the name."""
    provider, _, rest = spec.partition(":")
    if rest and provider in PROVIDERS:
        return provider, rest
    return GEMINI, spec


@lru_cache(maxsize=1)
def _mesh_models() -> frozenset[str]:
    return frozenset(list_mesh())


@lru_cache(maxsize=len(LOCAL_SERVERS))
def _server_models(provider: str) -> frozenset[str]:
    server = _BY_PROVIDER[provider]
    return frozenset(list_server(server))


@lru_cache(maxsize=1)
def _local_models() -> frozenset[str]:
    # Cached: `resolve` runs on every model switch and shouldn't pay for a
    # round trip -- or a timeout -- each time.
    return frozenset(list_ollama())


def forget_local_models() -> None:
    """Drop cached listings, so a freshly pulled or newly meshed model shows up."""
    _local_models.cache_clear()
    _mesh_models.cache_clear()
    _server_models.cache_clear()
