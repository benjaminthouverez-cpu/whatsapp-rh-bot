"""Notion API client that recursively searches the Big Mamma HR hub."""

import os
import logging

import requests

logger = logging.getLogger(__name__)

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Block / page text extraction
# ---------------------------------------------------------------------------

def _extract_text_from_block(block: dict) -> str:
    """Pull plain text from a Notion block."""
    block_type = block.get("type", "")
    data = block.get(block_type, {})

    rich_text = data.get("rich_text", [])
    if rich_text:
        return "".join(seg.get("plain_text", "") for seg in rich_text)

    if block_type == "child_page":
        return data.get("title", "")
    if block_type == "child_database":
        return data.get("title", "")

    return ""


def _extract_page_title(page: dict) -> str:
    """Extract the title from a Notion page object."""
    props = page.get("properties", {})
    for prop in props.values():
        if prop.get("type") == "title":
            return "".join(
                t.get("plain_text", "") for t in prop.get("title", [])
            )
    return ""


# ---------------------------------------------------------------------------
# Recursive block fetching
# ---------------------------------------------------------------------------

def _fetch_blocks(block_id: str, depth: int = 0, max_depth: int = 3) -> list[str]:
    """Recursively fetch text from all blocks under a given block/page.

    Follows nested children (toggles, callouts, columns, synced blocks, etc.)
    up to *max_depth* levels to capture content hidden inside collapsible or
    grouped structures.
    """
    texts: list[str] = []
    url = f"{NOTION_API}/blocks/{block_id}/children?page_size=100"

    while url:
        resp = requests.get(url, headers=_headers(), timeout=15)
        if resp.status_code != 200:
            logger.warning("Failed to fetch blocks for %s: %s", block_id, resp.status_code)
            break

        data = resp.json()
        for block in data.get("results", []):
            text = _extract_text_from_block(block)
            if text.strip():
                texts.append(text.strip())

            # Recurse into nested children (toggles, callouts, columns…)
            if block.get("has_children") and block.get("type") != "child_page" and depth < max_depth:
                texts.extend(_fetch_blocks(block["id"], depth + 1, max_depth))

        next_cursor = data.get("next_cursor")
        if data.get("has_more") and next_cursor:
            url = f"{NOTION_API}/blocks/{block_id}/children?page_size=100&start_cursor={next_cursor}"
        else:
            url = None

    return texts


def get_page_content(page_id: str) -> str:
    """Fetch all text content from a Notion page, recursing into nested blocks."""
    parts = _fetch_blocks(page_id)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Recursive child-page discovery
# ---------------------------------------------------------------------------

def get_all_pages(page_id: str, depth: int = 0, max_depth: int = 4) -> list[dict]:
    """Recursively collect all child pages under *page_id*.

    Returns a flat list of {id, title, depth} dicts, walking up to
    *max_depth* levels deep so the entire HR hub tree is indexed.
    """
    pages: list[dict] = []
    url = f"{NOTION_API}/blocks/{page_id}/children?page_size=100"

    while url:
        resp = requests.get(url, headers=_headers(), timeout=15)
        if resp.status_code != 200:
            break

        data = resp.json()
        for block in data.get("results", []):
            if block.get("type") == "child_page":
                child = {
                    "id": block["id"],
                    "title": block["child_page"].get("title", ""),
                    "depth": depth,
                }
                pages.append(child)
                if depth < max_depth:
                    pages.extend(get_all_pages(block["id"], depth + 1, max_depth))

        next_cursor = data.get("next_cursor")
        if data.get("has_more") and next_cursor:
            url = f"{NOTION_API}/blocks/{page_id}/children?page_size=100&start_cursor={next_cursor}"
        else:
            url = None

    return pages


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def _score_page(title: str, content: str, query_words: list[str]) -> int:
    """Score a page by how many query words appear in its title or content."""
    title_lower = title.lower()
    content_lower = content.lower()
    score = 0
    for word in query_words:
        if word in title_lower:
            score += 3  # title matches are worth more
        if word in content_lower:
            score += 1
    return score


def search_notion_hr(query: str, max_results: int = 3) -> list[str]:
    """Search the Notion HR hub for pages matching the query.

    Strategy:
    1. Notion search API (workspace-wide) — fast but often misses subpages.
    2. Recursive walk of the HR hub tree — finds every child page, scores them
       by keyword overlap with the query, and returns the best matches.
    Results from both passes are merged and deduplicated.
    """
    root_page_id = os.environ.get(
        "NOTION_PAGE_ID", "34c5d2b79c458197986ce0a69e6c053f"
    )
    query_words = [w for w in query.lower().split() if len(w) > 2]

    results: list[str] = []
    seen_ids: set[str] = set()

    # --- Pass 1: Notion search API ---
    try:
        resp = requests.post(
            f"{NOTION_API}/search",
            headers=_headers(),
            json={
                "query": query,
                "filter": {"property": "object", "value": "page"},
                "page_size": max_results * 2,
            },
            timeout=15,
        )
        if resp.status_code == 200:
            for page in resp.json().get("results", []):
                page_id = page["id"]
                if page_id in seen_ids:
                    continue
                title = _extract_page_title(page)
                content = get_page_content(page_id)
                if content.strip():
                    seen_ids.add(page_id)
                    results.append(f"## {title}\n\n{content}")
                if len(results) >= max_results:
                    break
    except requests.RequestException:
        logger.exception("Notion search API failed")

    if len(results) >= max_results:
        return results[:max_results]

    # --- Pass 2: Recursive tree walk + keyword scoring ---
    try:
        all_pages = get_all_pages(root_page_id)
        logger.info("Indexed %d pages under HR hub", len(all_pages))

        # Score every page we haven't already returned
        scored: list[tuple[int, dict]] = []
        for page in all_pages:
            if page["id"] in seen_ids:
                continue
            title_score = _score_page(page["title"], "", query_words)
            if title_score > 0:
                scored.append((title_score, page))

        # Sort best-first, then fetch content only for top candidates
        scored.sort(key=lambda x: x[0], reverse=True)

        for _score, page in scored:
            if len(results) >= max_results:
                break
            content = get_page_content(page["id"])
            if not content.strip():
                continue
            # Re-score with content to confirm relevance
            full_score = _score_page(page["title"], content, query_words)
            if full_score > 0:
                seen_ids.add(page["id"])
                results.append(f"## {page['title']}\n\n{content}")

    except requests.RequestException:
        logger.exception("Notion tree walk failed")

    return results[:max_results]
