"""Reaching the internet: a grounded search, and a page reader.

`web_search` runs on Gemini's Google Search grounding, so it needs no second
API account -- the key already in your environment does the work. `web_fetch`
is a plain HTTP GET with the page boiled down to markdown.
"""

from __future__ import annotations

import asyncio
import os
import re
from urllib.parse import urlparse

import httpx
from langchain_core.tools import tool

from hubbleflow import config as config_module

SEARCH_MODEL = os.getenv("HUBBLEFLOW_SEARCH_MODEL", "gemini-3.5-flash-lite")
FETCH_TIMEOUT = 20.0
REDIRECT_TIMEOUT = 4.0
MAX_BYTES = 5_000_000
MAX_CHARS = 20_000
USER_AGENT = "Mozilla/5.0 (compatible; hubbleflow/0.1; +https://github.com/)"

NO_KEY = (
    "Web search is unavailable: no Gemini API key is configured. It runs on Google Search "
    "grounding, which needs GOOGLE_API_KEY even when the session model is local."
)

_STRIP_TAGS = ("script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg")
_MAIN_TAGS = ("main", "article", '[role="main"]', "#content", ".content")


# --------------------------------------------------------------------------
# search
# --------------------------------------------------------------------------

async def search(query: str) -> str:
    """Ask Gemini the question with Google Search grounding switched on."""
    key = config_module.api_key()
    if not key:
        return NO_KEY

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=key)
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=SEARCH_MODEL,
            contents=query,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
            ),
        )
    except Exception as error:
        return f"Web search failed: {error}"

    summary = (response.text or "").strip() or "(the search returned no summary)"
    sources = await _sources(response)
    if not sources:
        return summary
    lines = "\n".join(f"  [{i}] {title} — {url}" for i, (title, url) in enumerate(sources, 1))
    return f"{summary}\n\nSources:\n{lines}"


async def _sources(response) -> list[tuple[str, str]]:
    """Pull the grounding citations out, resolving Google's redirect wrappers."""
    try:
        metadata = response.candidates[0].grounding_metadata
        chunks = getattr(metadata, "grounding_chunks", None) or []
    except (AttributeError, IndexError):
        return []

    raw = []
    for chunk in chunks:
        web = getattr(chunk, "web", None)
        if web and web.uri:
            raw.append((web.title or "", web.uri))
    if not raw:
        return []

    # Grounding hands back vertexaisearch redirect links; the real URL is more
    # use to the agent than a redirector it would have to follow blind.
    resolved = await asyncio.gather(*(_final_url(url) for _, url in raw))
    seen, out = set(), []
    for (title, original), final in zip(raw, resolved, strict=True):
        url = final or original
        if url in seen:
            continue
        seen.add(url)
        out.append((title or _host(url), url))
    return out


async def _final_url(url: str) -> str | None:
    if "grounding-api-redirect" not in url:
        return url
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=REDIRECT_TIMEOUT) as client:
            response = await client.head(url, headers={"User-Agent": USER_AGENT})
            return str(response.url)
    except Exception:
        return None


# --------------------------------------------------------------------------
# fetch
# --------------------------------------------------------------------------

async def fetch(url: str) -> str:
    """Download a page and return it as markdown."""
    if not urlparse(url).scheme:
        url = f"https://{url}"
    if urlparse(url).scheme not in {"http", "https"}:
        return f"Refusing to fetch {url}: only http and https are supported."

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=FETCH_TIMEOUT) as client:
            response = await client.get(url, headers={"User-Agent": USER_AGENT})
            response.raise_for_status()
            body = response.content[:MAX_BYTES]
            content_type = response.headers.get("content-type", "")
            final = str(response.url)
    except httpx.HTTPStatusError as error:
        return f"{url} returned HTTP {error.response.status_code}."
    except Exception as error:
        return f"Couldn't fetch {url}: {error}"

    text = body.decode(response.encoding or "utf-8", errors="replace")
    if "html" in content_type:
        text = _to_markdown(text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    header = final if final == url else f"{url}\n(redirected to {final})"
    if len(text) > MAX_CHARS:
        text = f"{text[:MAX_CHARS]}\n\n... [truncated; the page continues]"
    return f"{header}\n\n{text}"


def _to_markdown(html: str) -> str:
    try:
        from bs4 import BeautifulSoup
        from markdownify import markdownify
    except ImportError:
        return re.sub(r"<[^>]+>", " ", html)

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(list(_STRIP_TAGS)):
        tag.decompose()

    # Prefer the page's main region; a site's nav and chrome is noise to an agent.
    region = next((found for selector in _MAIN_TAGS if (found := soup.select_one(selector))), None)
    return markdownify(str(region or soup), heading_style="ATX", strip=["img"])


def _host(url: str) -> str:
    return urlparse(url).netloc or url


# --------------------------------------------------------------------------
# tools
# --------------------------------------------------------------------------

def make_web_tools() -> list:
    @tool("web_search", parse_docstring=True)
    async def web_search(query: str) -> str:
        """Search the web and get an answer grounded in current sources.

        Use this for anything that changed after your training data: library
        versions, release notes, error messages from a package you don't know,
        current documentation. Ask a full question rather than keywords.

        Args:
            query: What you want to know, phrased as a question.
        """
        return await search(query)

    @tool("web_fetch", parse_docstring=True)
    async def web_fetch(url: str) -> str:
        """Fetch a web page and return its main content as markdown.

        Use this to read a source that web_search surfaced, or a URL the user
        gave you. Prefer it over guessing at a page's contents.

        Args:
            url: The URL to fetch.
        """
        return await fetch(url)

    return [web_search, web_fetch]
