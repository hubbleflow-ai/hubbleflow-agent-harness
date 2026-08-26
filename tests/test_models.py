"""Model resolution across providers, and the permission scopes for web access."""

from __future__ import annotations

import pytest

from hubbleflow import models
from hubbleflow.permissions import PermissionPolicy


@pytest.fixture
def local(monkeypatch):
    """Pretend Ollama has these three pulled, without touching the network."""
    names = frozenset({"gemma4:12b", "qwen3.5:9b", "llama4:latest", "someone/custom-model:q8"})
    monkeypatch.setattr(models, "_local_models", lambda: names)
    return names


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("gemma4:12b", "ollama:gemma4:12b"),            # exact local tag
        ("gemma4", "ollama:gemma4:12b"),                # bare family, one tag
        ("llama4", "ollama:llama4:latest"),             # bare family, :latest
        ("qwen3.5:9b", "ollama:qwen3.5:9b"),            # a dot and a colon
        ("gemini-2.5-pro", "google_genai:gemini-2.5-pro"),
        ("meta/muse-glimmer-30b", "nvidia:meta/muse-glimmer-30b"),   # a slash means NVIDIA
        ("deepseek-ai/deepseek-v3.2", "nvidia:deepseek-ai/deepseek-v3.2"),
        ("nvidia:meta/muse-glimmer-30b", "nvidia:meta/muse-glimmer-30b"),
        ("ollama:gemma4:12b", "ollama:gemma4:12b"),     # already qualified
        ("google_genai:gemini-3.5-flash-lite", "google_genai:gemini-3.5-flash-lite"),
    ],
)
def test_resolve(local, typed, expected):
    assert models.resolve(typed) == expected


def test_split_keeps_colons_in_the_tag():
    assert models.split("ollama:gemma4:12b") == ("ollama", "gemma4:12b")
    assert models.split("google_genai:gemini-2.5-pro") == ("google_genai", "gemini-2.5-pro")


def test_unknown_bare_name_falls_through_to_gemini(local):
    assert models.resolve("some-new-model") == "google_genai:some-new-model"


def test_a_local_tag_containing_a_slash_stays_with_ollama(local):
    """Ollama names can look like NVIDIA's; a pulled model wins over the guess."""
    assert models.resolve("someone/custom-model:q8") == "ollama:someone/custom-model:q8"


def test_an_unlisted_nvidia_name_still_resolves(local):
    """Newer than any catalogue is the normal case for a model you want to try."""
    assert models.resolve("vendor/brand-new-model") == "nvidia:vendor/brand-new-model"


def test_split_handles_an_nvidia_name():
    assert models.split("nvidia:meta/muse-glimmer-30b") == ("nvidia", "meta/muse-glimmer-30b")


def test_web_fetch_is_scoped_to_a_host():
    policy = PermissionPolicy()
    page = {"url": "https://docs.astral.sh/uv/guides/install"}
    assert PermissionPolicy.signature_for("web_fetch", page) == "docs.astral.sh"
    assert policy.needs_review("web_fetch", page)

    policy.allow_always("web_fetch", page)
    assert not policy.needs_review("web_fetch", {"url": "https://docs.astral.sh/uv/reference"})
    assert policy.needs_review("web_fetch", {"url": "https://elsewhere.example/page"})
