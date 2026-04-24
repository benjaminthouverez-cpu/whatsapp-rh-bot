"""Notion API client that recursively searches the Big Mamma HR hub."""

import os
import logging
from typing import Optional

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


def _extract_text_from_block(block: dict) -> str:
    """Pull plain text from a Notion block."""
    block_type = block.get("type", "")
    data = block.get(block_type, {})

    # Most text blocks have a "rich_text" array
    rich_text = data.get("rich_text", [])
    if rich_text:
        return "".join(segment.get("plain_text", "") for segment in rich_text)

    # Child page / child database titles
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


def get_page_content(page_id: str, max_blocks: int = 200) -> str:
    """Fetch all block content from a Notion page, with pagination."""
    blocks: list[str] = []
    url = f"{NOTION_API}/blocks/{page_id}/children?page_size=100"

    while url and len(blocks) < max_blocks:
        resp = requests.get(url, headers=_headers(), timeout=15)
        if resp.status_code != 200:
            logger.warning("Failed to fetch blocks for %s: %s", page_id, resp.status_code)
            break

        data = resp.json()
        for block in data.get("results", []):
            text = _extract_text_from_block(block)
            if text.strip():
                blocks.append(text.strip())

        next_cursor = data.get("next_cursor")
        if data.get("has_more") and next_cursor:
            url = f"{NOTION_API}/blocks/{page_id}/children?page_size=100&start_cursor={next_cursor}"
        else:
            url = None

    return "\n".join(blocks)


def get_child_pages(page_id: str) -> list[dict]:
    """Get all child page blocks under a given page."""
    pages = []
    url = f"{NOTION_API}/blocks/{page_id}/children?page_size=100"

    while url:
        resp = requests.get(url, headers=_headers(), timeout=15)
        if resp.status_code != 200:
            break

        data = resp.json()
        for block in data.get("results", []):
            if block.get("type") in ("child_page", "child_database"):
                pages.append({
                    "id": block["id"],
                    "title": _extract_text_from_block(block),
                    "type": block["type"],
                })

        next_cursor = data.get("next_cursor")
        if data.get("has_more") and next_cursor:
            url = f"{NOTION_API}/blocks/{page_id}/children?page_size=100&start_cursor={next_cursor}"
        else:
            url = None

    return pages


def search_notion_hr(query: str, max_results: int = 3) -> list[str]:
    """Search the Notion HR hub for pages matching the query.

    Strategy:
    1. Use Notion search API scoped to the workspace (filtered to pages).
    2. Also scan direct children of the HR hub root page for title matches.
    3. Fetch content from the best matching pages.
    """
    root_page_id = os.environ.get(
        "NOTION_PAGE_ID", "34c5d2b79c458197986ce0a69e6c053f"
    )

    results: list[str] = []
    seen_ids: set[str] = set()

    # --- 1. Notion search API (workspace-wide, filtered to pages) ---
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

    # --- 2. Scan child pages of the HR hub root for title keyword match ---
    if len(results) < max_results:
        try:
            children = get_child_pages(root_page_id)
            query_lower = query.lower()
            query_words = query_lower.split()

            for child in children:
                if child["id"] in seen_ids:
                    continue
                title_lower = child["title"].lower()
                if any(word in title_lower for word in query_words if len(word) > 2):
                    content = get_page_content(child["id"])
                    if content.strip():
                        seen_ids.add(child["id"])
                        results.append(f"## {child['title']}\n\n{content}")
                    if len(results) >= max_results:
                        break
        except requests.RequestException:
            logger.exception("Notion child page scan failed")

    return results[:max_results]
