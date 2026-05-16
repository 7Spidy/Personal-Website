#!/usr/bin/env python3
"""
notion_lib.py
-------------
Shared Notion API helpers used by both update_now.py (homepage Now teaser)
and build_library.py (full /now media library page).

Requires env var:
    NOTION_TOKEN  — Notion integration token (GitHub secret)
"""

import os
import json
import urllib.request
from datetime import datetime, timezone, timedelta

# ── CONFIG ────────────────────────────────────────────────────────────────────

NOTION_TOKEN = os.environ["NOTION_TOKEN"]

# Database IDs (Notion API format: 8-4-4-4-12)
DB_MOVIES_TV = "29e03aac-efd1-80c7-b8f0-c7cce7f1dc78"
DB_BOOKS     = "29e03aac-efd1-80c4-8d53-ed7a09f88eff"
DB_GAMES     = "29e03aac-efd1-806f-99b8-dafa421215d5"

IST = timezone(timedelta(hours=5, minutes=30))

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

# ── QUERY ─────────────────────────────────────────────────────────────────────

def notion_query(database_id: str, filter_body: dict) -> list:
    """Query a Notion database, following pagination, returning all pages."""
    url = f"https://api.notion.com/v1/databases/{database_id}/query"
    payload = json.dumps(filter_body).encode()
    req = urllib.request.Request(url, data=payload, headers=HEADERS, method="POST")
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    results = data.get("results", [])
    while data.get("has_more"):
        body = {**filter_body, "start_cursor": data["next_cursor"]}
        req2 = urllib.request.Request(
            url, data=json.dumps(body).encode(), headers=HEADERS, method="POST"
        )
        with urllib.request.urlopen(req2) as resp2:
            data = json.loads(resp2.read())
        results.extend(data.get("results", []))
    return results


def get_in_progress(db_id: str) -> list:
    return notion_query(db_id, {
        "filter": {"property": "Status", "status": {"equals": "In progress"}},
        "sorts": [{"property": "Date", "direction": "descending"}],
    })


def get_done_all(db_id: str) -> list:
    """Every Done item, newest first — full history, no sampling."""
    return notion_query(db_id, {
        "filter": {"property": "Status", "status": {"equals": "Done"}},
        "sorts": [{"property": "Date", "direction": "descending"}],
        "page_size": 100,
    })

# ── PROPERTY HELPERS ──────────────────────────────────────────────────────────

def page_title(page: dict) -> str:
    props = page.get("properties", {})
    title_prop = props.get("Name") or props.get("title", {})
    if isinstance(title_prop, dict):
        rich = title_prop.get("title", []) or title_prop.get("rich_text", [])
        return "".join(t.get("plain_text", "") for t in rich).strip()
    return "Unknown"


def page_rating(page: dict) -> str:
    rating = page.get("properties", {}).get("Rating")
    if not rating:
        return ""
    if rating.get("type") == "select":
        sel = rating.get("select")
        if sel:
            return sel.get("name", "")
    num = rating.get("number")
    if num is not None:
        return str(num)
    rt = rating.get("rich_text", [])
    if rt:
        return rt[0].get("plain_text", "")
    return ""


def _date_value(page: dict, prop_name: str) -> str:
    p = page.get("properties", {}).get(prop_name)
    if p and p.get("type") == "date":
        d = p.get("date")
        if d:
            return d.get("start", "") or ""
    return ""


def page_start_date(page: dict) -> str:
    return _date_value(page, "Date")


def page_end_date(page: dict) -> str:
    """Prefer 'End Date'; fall back to 'Date'."""
    return _date_value(page, "End Date") or _date_value(page, "Date")


def page_type(page: dict) -> str:
    """TV Show / Movie for the movies/tv db (select)."""
    return page_field(page, "Type")


def page_field(page: dict, name: str):
    """
    Generic getter. Returns:
      select        -> str (option name)
      multi_select  -> list[str]
      number        -> float
      rich_text     -> str
      date          -> str (start ISO)
    or "" / [] / None when absent.
    """
    p = page.get("properties", {}).get(name)
    if not p:
        return ""
    t = p.get("type")
    if t == "select":
        sel = p.get("select")
        return sel.get("name", "") if sel else ""
    if t == "multi_select":
        return [s["name"] for s in p.get("multi_select", [])]
    if t == "number":
        return p.get("number")
    if t == "rich_text":
        rt = p.get("rich_text", [])
        return rt[0].get("plain_text", "") if rt else ""
    if t == "date":
        d = p.get("date")
        return d.get("start", "") if d else ""
    return ""


def fmt_date(iso: str, fmt: str = "%-d %b") -> str:
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso).strftime(fmt)
    except Exception:
        return iso


def days_since(iso: str) -> int:
    """Whole days from an ISO date until now (IST). 0 if unknown/future."""
    if not iso:
        return 0
    try:
        d = datetime.fromisoformat(iso)
        if d.tzinfo is None:
            d = d.replace(tzinfo=IST)
        delta = (datetime.now(IST) - d).days
        return max(delta, 0)
    except Exception:
        return 0
