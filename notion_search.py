"""Notion API client that indexes the Big Mamma HR hub and searches it.

On first query, crawls the entire page tree under the root HR hub page,
fetches all block content (including nested blocks), and caches everything
in memory. Subsequent queries search the cache instantly.
"""

import os
import time
import logging

import requests

logger = logging.getLogger(__name__)

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

# In-memory cache: list of {"id", "title", "content"} dicts
_page_cache: list[dict] = []
_cache_timestamp: float = 0
CACHE_TTL = 600  # rebuild cache every 10 minutes


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
        "Notion-Version": NOTION_VERSION,
    }


def _api_get(url: str) -> dict | None:
    """GET request with error logging. Returns JSON or None."""
    try:
        resp = requests.get(url, headers=_headers(), timeout=30)
    except requests.RequestException:
        logger.exception("Notion API request failed: %s", url)
        return None

    if resp.status_code == 200:
        return resp.json()

    logger.warning("Notion API %s returned %s: %s", url, resp.status_code, resp.text[:200])
    return None


# ---------------------------------------------------------------------------
# Text extraction from blocks
# ---------------------------------------------------------------------------

def _block_text(block: dict) -> str:
    """Extract plain text from a single Notion block."""
    btype = block.get("type", "")
    data = block.get(btype, {})

    # Most block types: paragraph, heading_1/2/3, bulleted_list_item,
    # numbered_list_item, to_do, toggle, callout, quote, code, table_row
    rich_text = data.get("rich_text")
    if rich_text:
        return "".join(seg.get("plain_text", "") for seg in rich_text)

    # Table rows store cells as an array of rich_text arrays
    if btype == "table_row":
        cells = data.get("cells", [])
        parts = []
        for cell in cells:
            parts.append("".join(seg.get("plain_text", "") for seg in cell))
        return " | ".join(parts)

    # Child page / child database — just the title
    if btype == "child_page":
        return data.get("title", "")
    if btype == "child_database":
        return data.get("title", "")

    return ""


def _fetch_block_texts(block_id: str, depth: int = 0, max_depth: int = 4) -> list[str]:
    """Recursively fetch all text from blocks under a page/block.

    Recurses into any block with has_children (toggles, callouts, columns,
    synced blocks, etc.) but stops at child_page boundaries — those are
    indexed as separate pages.
    """
    texts: list[str] = []
    url = f"{NOTION_API}/blocks/{block_id}/children?page_size=100"

    while url:
        data = _api_get(url)
        if data is None:
            break

        for block in data.get("results", []):
            text = _block_text(block)
            if text.strip():
                texts.append(text.strip())

            # Recurse into nested containers, but not into child pages
            if (
                block.get("has_children")
                and block.get("type") not in ("child_page", "child_database")
                and depth < max_depth
            ):
                texts.extend(_fetch_block_texts(block["id"], depth + 1, max_depth))

        cursor = data.get("next_cursor")
        url = (
            f"{NOTION_API}/blocks/{block_id}/children?page_size=100&start_cursor={cursor}"
            if data.get("has_more") and cursor
            else None
        )

    return texts


# ---------------------------------------------------------------------------
# Recursive page tree crawl
# ---------------------------------------------------------------------------

def _crawl_pages(page_id: str, depth: int = 0, max_depth: int = 5) -> list[dict]:
    """Walk the page tree and collect {id, title, content} for every page.

    Recurses up to max_depth levels. Each child_page block is followed to
    discover its own children.
    """
    pages: list[dict] = []
    url = f"{NOTION_API}/blocks/{page_id}/children?page_size=100"

    while url:
        data = _api_get(url)
        if data is None:
            break

        for block in data.get("results", []):
            if block.get("type") == "child_page":
                child_id = block["id"]
                title = block["child_page"].get("title", "")

                # Fetch this page's block content
                texts = _fetch_block_texts(child_id)
                content = "\n".join(texts)

                pages.append({
                    "id": child_id,
                    "title": title,
                    "content": content,
                })
                logger.info(
                    "Indexed page [depth=%d]: %s (%d chars)",
                    depth, title, len(content),
                )

                # Recurse to find sub-pages
                if depth < max_depth:
                    pages.extend(_crawl_pages(child_id, depth + 1, max_depth))

        cursor = data.get("next_cursor")
        url = (
            f"{NOTION_API}/blocks/{page_id}/children?page_size=100&start_cursor={cursor}"
            if data.get("has_more") and cursor
            else None
        )

    return pages


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------

def _build_cache() -> list[dict]:
    """Crawl the entire HR hub and return the page index."""
    root_page_id = os.environ.get(
        "NOTION_PAGE_ID", "34c5d2b79c458197986ce0a69e6c053f"
    )
    logger.info("Building Notion cache from root page %s ...", root_page_id)

    # Also fetch the root page's own block content
    root_texts = _fetch_block_texts(root_page_id)
    pages = [{
        "id": root_page_id,
        "title": "(HR Hub root)",
        "content": "\n".join(root_texts),
    }]

    pages.extend(_crawl_pages(root_page_id))
    logger.info("Notion cache built: %d pages indexed", len(pages))
    return pages


def _get_cache() -> list[dict]:
    """Return the cached page index, rebuilding if stale."""
    global _page_cache, _cache_timestamp
    if not _page_cache or (time.time() - _cache_timestamp) > CACHE_TTL:
        _page_cache = _build_cache()
        _cache_timestamp = time.time()
    return _page_cache


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search_notion_hr(query: str, max_results: int = 3) -> list[str]:
    """Search the cached HR hub pages for content matching the query.

    Scores each page by keyword overlap: title matches count 3x, content
    matches count 1x. Returns the top pages formatted as markdown sections.
    """
    cache = _get_cache()
    if not cache:
        logger.error("Notion cache is empty — check NOTION_TOKEN and NOTION_PAGE_ID")
        return []

    query_words = [w for w in query.lower().split() if len(w) > 2]
    if not query_words:
        query_words = query.lower().split()

    scored: list[tuple[int, dict]] = []
    for page in cache:
        title_lower = page["title"].lower()
        content_lower = page["content"].lower()
        score = 0
        for word in query_words:
            if word in title_lower:
                score += 3
            if word in content_lower:
                score += 1
        if score > 0 and page["content"].strip():
            scored.append((score, page))

    scored.sort(key=lambda x: x[0], reverse=True)

    results = []
    for _score, page in scored[:max_results]:
        # Truncate very long pages to keep the Claude prompt reasonable
        content = page["content"]
        if len(content) > 3000:
            content = content[:3000] + "\n[...]"
        results.append(f"## {page['title']}\n\n{content}")

    logger.info(
        "Search for %r: %d pages scored, returning %d results",
        query, len(scored), len(results),
    )
    return results
