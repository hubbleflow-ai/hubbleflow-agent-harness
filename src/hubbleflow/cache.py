"""Rolling explicit context caching for Gemini.

Gemini bills a cached token at a tenth of a fresh one, and storage at $1 per
million tokens per hour of TTL. In an agentic loop the same conversation prefix
is resent on every model call, so caching it is worth doing whenever the session
makes more than ~4 calls per hour of cache lifetime.

The API is strict about one thing: a request that names a cache may not also set
`system_instruction`, `tools` or `tool_config` -- those must live *inside* the
cache. `CachingChatGoogle` intercepts request assembly to honour that, moving the
stable prefix into the cache and sending only the tail.

Off unless asked for: `hubbleflow --cache`.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import PrivateAttr

logger = logging.getLogger(__name__)

# Gemini 3.x refuses to cache anything smaller than this.
MIN_CACHE_TOKENS = 4096
# Storage is billed on the TTL you ask for, not the time you use, so ask small
# and rebuild. Five minutes comfortably covers a working turn, and every cache
# is deleted on rebuild and at session end.
TTL_SECONDS = 300
# Rebuild once this many conversation entries have accumulated outside the
# cache; each rebuild is a round trip, so don't do it every call.
REFRESH_AFTER = 6
# Stamped on every cache we create so a later session can recognise -- and
# release -- one that a crash left behind still accruing storage charges.
DISPLAY_NAME = "hubbleflow"


@dataclass
class CacheStats:
    """What the cache did, for `/usage` to report."""

    builds: int = 0
    reused: int = 0
    failures: int = 0
    tokens_cached: int = 0
    token_hours: float = 0.0
    """Storage exposure: tokens held x hours of TTL bought, summed over builds."""
    last_error: str = ""
    _names: set[str] = field(default_factory=set)

    @property
    def active(self) -> bool:
        return self.builds > 0

    def storage_cost(self, price_per_million_hour: float = 1.0) -> float:
        return self.token_hours / 1e6 * price_per_million_hour


class GeminiCache:
    """Owns one session's cache objects and decides when to rebuild."""

    def __init__(self, model: str, api_key: str | None, stats: CacheStats) -> None:
        self.model = model
        self.api_key = api_key
        self.stats = stats
        self._name: str | None = None
        self._prefix_len = 0
        self._fingerprint = ""
        self._client: Any = None
        self._disabled = False

    # -- the hook ------------------------------------------------------------

    def apply(self, request: dict) -> dict:
        """Rewrite a prepared request to read from the cache where it can."""
        if self._disabled:
            return request

        config = request.get("config")
        contents = request.get("contents") or []
        if config is None or not contents:
            return request

        split = _split_point(contents, self._prefix_len)
        name = self._ensure(config, contents, split)
        if not name:
            return request

        # The API rejects a cached request that also declares these.
        config.cached_content = name
        config.system_instruction = None
        config.tools = None
        config.tool_config = None
        request["contents"] = contents[self._prefix_len :]
        return request

    # -- cache lifecycle -----------------------------------------------------

    def _ensure(self, config: Any, contents: list, split: int) -> str | None:
        if self._name:
            # Validate the slice that is actually IN the cache, not the longer
            # one we could cache now -- otherwise every call looks stale and we
            # rebuild each time, paying a fresh TTL for nothing.
            unchanged = _fingerprint(config, contents[: self._prefix_len]) == self._fingerprint
            if unchanged and split - self._prefix_len < REFRESH_AFTER:
                self.stats.reused += 1
                return self._name
            self._delete(self._name)
            self._name = None

        prefix = contents[:split]
        name = self._create(config, prefix)
        if not name:
            return None
        self._name = name
        self._prefix_len = split
        self._fingerprint = _fingerprint(config, prefix)
        return name

    def _create(self, config: Any, prefix: list) -> str | None:
        client = self._get_client()
        if client is None:
            return None
        try:
            from google.genai import types

            cache = client.caches.create(
                model=self.model,
                config=types.CreateCachedContentConfig(
                    system_instruction=config.system_instruction,
                    tools=config.tools,
                    tool_config=config.tool_config,
                    contents=list(prefix) or None,
                    ttl=f"{TTL_SECONDS}s",
                    display_name=DISPLAY_NAME,
                ),
            )
        except Exception as error:
            return self._give_up(error)

        self.stats.builds += 1
        self.stats.tokens_cached = cache.usage_metadata.total_token_count
        self.stats.token_hours += self.stats.tokens_cached * (TTL_SECONDS / 3600)
        self.stats._names.add(cache.name)
        return cache.name

    def _delete(self, name: str) -> None:
        client = self._get_client()
        if client is None:
            return
        try:
            client.caches.delete(name=name)
            self.stats._names.discard(name)
        except Exception as error:  # a cache that already expired is fine
            logger.debug("cache delete failed: %s", error)

    def sweep(self) -> int:
        """Release caches an earlier run left behind. Returns how many."""
        client = self._get_client()
        if client is None:
            return 0
        released = 0
        try:
            for entry in client.caches.list():
                if getattr(entry, "display_name", None) != DISPLAY_NAME:
                    continue
                client.caches.delete(name=entry.name)
                released += 1
        except Exception as error:
            logger.debug("cache sweep failed: %s", error)
        return released

    def close(self) -> None:
        """Drop every cache this session created, so storage stops accruing."""
        for name in list(self.stats._names):
            self._delete(name)
        self._name = None

    # -- plumbing ------------------------------------------------------------

    def _get_client(self) -> Any:
        if self._client is None and not self._disabled:
            try:
                from google import genai

                self._client = genai.Client(api_key=self.api_key)
            except Exception as error:
                return self._give_up(error)
        return self._client

    def _give_up(self, error: Exception) -> None:
        """One failure disables caching for the session rather than retrying."""
        self._disabled = True
        self.stats.failures += 1
        self.stats.last_error = " ".join(str(error).split())[:200]
        logger.debug("context caching disabled: %s", self.stats.last_error)
        return None


def _split_point(contents: list, current: int) -> int:
    """How many leading entries to cache: everything up to the last user turn.

    Cutting mid-turn would put half an exchange in the cache and half in the
    request, so walk back to a `user` boundary. Never cache the final entry --
    a request needs something left to send.
    """
    limit = len(contents) - 1
    for index in range(limit, current, -1):
        if getattr(contents[index], "role", None) == "user":
            return index
    return current


def _fingerprint(config: Any, prefix: list) -> str:
    """Detect a rewritten history -- compaction invalidates the whole cache."""
    digest = hashlib.sha256()
    for part in (config.system_instruction, config.tools, config.tool_config):
        digest.update(repr(part).encode("utf-8", "replace"))
    for entry in prefix:
        digest.update(repr(entry).encode("utf-8", "replace"))
    return digest.hexdigest()


class CachingChatGoogle(ChatGoogleGenerativeAI):
    """A Gemini chat model that keeps its stable prefix in an explicit cache."""

    _cache: GeminiCache | None = PrivateAttr(default=None)

    def attach(self, cache: GeminiCache) -> "CachingChatGoogle":
        self._cache = cache
        return self

    def _prepare_request(self, messages, **kwargs):  # type: ignore[override]
        request = super()._prepare_request(messages, **kwargs)
        return self._cache.apply(request) if self._cache else request
