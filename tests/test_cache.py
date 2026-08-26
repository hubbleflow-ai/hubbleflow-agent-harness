"""Rolling-cache bookkeeping, driven against a fake Gemini caches API."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from hubbleflow import cache as cache_module
from hubbleflow.cache import REFRESH_AFTER, CacheStats, GeminiCache, _split_point


@dataclass
class FakeContent:
    role: str
    text: str = "x"


@dataclass
class FakeConfig:
    system_instruction: object = "system"
    tools: object = "tools"
    tool_config: object = None
    cached_content: str | None = None


@dataclass
class FakeCaches:
    """Stands in for client.caches, recording what was created and deleted."""

    created: list = field(default_factory=list)
    deleted: list = field(default_factory=list)
    counter: int = 0

    def create(self, *, model, config):
        self.counter += 1
        name = f"cachedContents/{self.counter}"
        self.created.append((name, list(config.contents or [])))
        return type("C", (), {"name": name, "usage_metadata": type("U", (), {"total_token_count": 5000})()})()

    def delete(self, *, name):
        self.deleted.append(name)


@pytest.fixture
def cache(monkeypatch):
    caches = FakeCaches()
    client = type("Client", (), {"caches": caches})()
    stats = CacheStats()
    instance = GeminiCache("gemini-3.5-flash-lite", "key", stats)
    monkeypatch.setattr(instance, "_get_client", lambda: client)
    # Bypass the real google.genai config object.
    monkeypatch.setattr(
        cache_module, "_fingerprint",
        lambda config, prefix: repr((config.system_instruction, config.tools, [p.text for p in prefix])),
    )
    original_create = instance._create

    def create(config, prefix):
        c = client.caches.create(model="m", config=type("Cfg", (), {"contents": prefix})())
        stats.builds += 1
        stats.tokens_cached = 5000
        stats._names.add(c.name)
        return c.name

    monkeypatch.setattr(instance, "_create", create)
    instance.fake = caches  # type: ignore[attr-defined]
    return instance


def _request(n_pairs: int) -> dict:
    """A conversation of alternating user/model entries."""
    contents = []
    for i in range(n_pairs):
        contents += [FakeContent("user", f"u{i}"), FakeContent("model", f"m{i}")]
    return {"config": FakeConfig(), "contents": contents}


def test_split_point_cuts_at_a_user_boundary_and_never_the_last_entry():
    contents = [FakeContent("user"), FakeContent("model"), FakeContent("user"), FakeContent("model")]
    split = _split_point(contents, 0)
    assert split == 2 and contents[split].role == "user"
    assert split < len(contents)


def test_apply_moves_the_prefix_into_the_cache_and_strips_it_from_the_request(cache):
    request = cache.apply(_request(3))
    config = request["config"]

    assert config.cached_content is not None
    # The API rejects a cached request that also declares these.
    assert config.system_instruction is None
    assert config.tools is None
    assert config.tool_config is None
    assert len(request["contents"]) < 6


def test_an_unchanged_prefix_is_reused_rather_than_rebuilt(cache):
    cache.apply(_request(3))
    for _ in range(3):
        cache.apply(_request(3))

    assert cache.stats.builds == 1
    assert cache.stats.reused == 3
    assert cache.fake.deleted == []


def test_growth_past_the_threshold_triggers_one_rebuild(cache):
    cache.apply(_request(2))
    built_first = cache.stats.builds
    cache.apply(_request(2 + REFRESH_AFTER))

    assert cache.stats.builds == built_first + 1
    assert len(cache.fake.deleted) == 1  # the superseded cache is released


def test_a_rewritten_history_invalidates_the_cache(cache):
    cache.apply(_request(4))
    rewritten = _request(4)
    rewritten["contents"][0].text = "compacted"  # summarisation rewrote the head

    cache.apply(rewritten)
    assert cache.stats.builds == 2
    assert len(cache.fake.deleted) == 1


def test_close_releases_every_cache_it_created(cache):
    cache.apply(_request(2))
    cache.apply(_request(2 + REFRESH_AFTER))
    cache.close()

    assert set(cache.fake.deleted) == {name for name, _ in cache.fake.created}


def test_a_failure_disables_caching_for_the_session(cache, monkeypatch):
    monkeypatch.setattr(cache, "_create", lambda config, prefix: cache._give_up(RuntimeError("quota")))

    request = cache.apply(_request(3))
    assert request["config"].cached_content is None
    assert request["config"].tools == "tools"  # untouched, so the call still works
    assert cache.stats.failures == 1

    cache.apply(_request(4))
    assert cache.stats.failures == 1  # disabled, not retried every call
