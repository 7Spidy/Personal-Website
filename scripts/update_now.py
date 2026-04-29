#!/usr/bin/env python3
"""
update_now.py
-------------
Fetches Avi's Notion Media Consumption databases (Movies/TV, Books, Games),
finds all "In progress" items, and patches the Now section of index.html.
Runs via GitHub Actions at 1 AM IST (19:30 UTC) every day.

Usage:
    NOTION_TOKEN=your_token python scripts/update_now.py

Required env var:
    NOTION_TOKEN  — Notion integration token (store as GitHub secret)
"""

import os
import re
import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta

# ── CONFIG ────────────────────────────────────────────────────────────────────

NOTION_TOKEN = os.environ["NOTION_TOKEN"]

# Database IDs (Notion API format: 8-4-4-4-12)
DB_MOVIES_TV = "29e03aac-efd1-80c7-b8f0-c7cce7f1dc78"
DB_BOOKS     = "29e03aac-efd1-80c4-8d53-ed7a09f88eff"
DB_GAMES     = "29e03aac-efd1-806f-99b8-dafa421215d5"

HTML_FILE = "index.html"
NOW_START = "<!-- NOW-SECTION-START -->"
NOW_END   = "<!-- NOW-SECTION-END -->"

IST = timezone(timedelta(hours=5, minutes=30))

# ── NOTION API ────────────────────────────────────────────────────────────────

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

def notion_query(database_id: str, filter_body: dict) -> list:
    """Query a Notion database and return all pages."""
    url = f"https://api.notion.com/v1/databases/{database_id}/query"
    payload = json.dumps(filter_body).encode()
    req = urllib.request.Request(url, data=payload, headers=HEADERS, method="POST")
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    results = data.get("results", [])
    # Handle pagination
    while data.get("has_more"):
        payload2 = json.dumps({**filter_body, "start_cursor": data["next_cursor"]}).encode()
        req2 = urllib.request.Request(url, data=payload2, headers=HEADERS, method="POST")
        with urllib.request.urlopen(req2) as resp2:
            data = json.loads(resp2.read())
        results.extend(data.get("results", []))
    return results

def get_in_progress(db_id: str) -> list:
    return notion_query(db_id, {
        "filter": {"property": "Status", "status": {"equals": "In progress"}},
        "sorts": [{"property": "Date", "direction": "descending"}],
    })

def get_done(db_id: str, limit: int = 8) -> list:
    pages = notion_query(db_id, {
        "filter": {"property": "Status", "status": {"equals": "Done"}},
        "sorts": [{"property": "Date", "direction": "descending"}],
        "page_size": limit,
    })
    return pages[:limit]

def page_title(page: dict) -> str:
    title_prop = page["properties"].get("Name") or page["properties"].get("title", {})
    if isinstance(title_prop, dict):
        rich = title_prop.get("title", []) or title_prop.get("rich_text", [])
        return "".join(t.get("plain_text", "") for t in rich)
    return "Unknown"

def page_rating(page: dict) -> str:
    rating = page["properties"].get("Rating")
    if not rating:
        return ""
    num = rating.get("number")
    if num is not None:
        return str(num)
    # might be a rich_text field
    rt = rating.get("rich_text", [])
    if rt:
        return rt[0].get("plain_text", "")
    return ""

def page_start_date(page: dict) -> str:
    date_prop = page["properties"].get("Date") or page["properties"].get("date:Date:start")
    if date_prop and date_prop.get("type") == "date":
        d = date_prop.get("date")
        if d:
            return d.get("start", "")
    return ""

def page_type(page: dict) -> str:
    """TV Show or Movie for movies/tv db."""
    t = page["properties"].get("Type")
    if t and t.get("type") == "select":
        sel = t.get("select")
        if sel:
            return sel.get("name", "")
    return ""

# ── HTML GENERATION ───────────────────────────────────────────────────────────

EMOJI_GAME  = "⚔️"
EMOJI_WATCH = "📺"
EMOJI_READ  = "📖"
EMOJI_LEARN = "🇯🇵"

def card_html(data_type: str, delay: str, emoji: str, type_label: str,
              title: str, sub: str, progress_pct: int, extra_title: str = "") -> str:
    extra = f' <span style="color:var(--fg-dim); font-size:13px; font-weight:400;">+ {extra_title}</span>' if extra_title else ""
    return f"""
    <div class="now-card reveal {delay}" data-type="{data_type}">
      <div class="now-type"><span class="now-type-dot"></span>{type_label}</div>
      <span class="now-emoji">{emoji}</span>
      <div class="now-title">{title}{extra}</div>
      <div class="now-sub">{sub}</div>
      <div class="now-progress">
        <div class="now-progress-fill" style="width:{progress_pct}%"></div>
      </div>
    </div>"""

def fmt_date(iso: str) -> str:
    if not iso:
        return ""
    try:
        d = datetime.fromisoformat(iso)
        return d.strftime("%-d %b")
    except Exception:
        return iso

def build_now_section(
    games_ip: list, tv_ip: list, books_ip: list,
    games_done: list, tv_done: list, books_done: list,
    updated_at: str
) -> str:
    cards = []
    delays = ["reveal-delay-1", "reveal-delay-2", "reveal-delay-3", "reveal-delay-4"]
    card_idx = 0

    # ── GAMES ──
    if games_ip:
        g = games_ip[0]
        title = page_title(g)
        start = fmt_date(page_start_date(g))
        sub = f"Started {start}. " if start else ""
        sub += "FromSoftware vibes. Every death is a lesson." if "sekiro" in title.lower() else "Currently in the controller."
        extra = f" + {page_title(games_ip[1])}" if len(games_ip) > 1 else ""
        cards.append(card_html("game", delays[card_idx % 4], EMOJI_GAME, "Playing",
                                page_title(g), sub, 50, page_title(games_ip[1]) if len(games_ip) > 1 else ""))
        card_idx += 1

    # ── TV / MOVIES ──
    if tv_ip:
        shows = [p for p in tv_ip if page_type(p) in ("TV Show", "")]
        movies = [p for p in tv_ip if page_type(p) == "Movie"]
        primary = shows[0] if shows else tv_ip[0]
        title = page_title(primary)
        start = fmt_date(page_start_date(primary))
        sub = f"Since {start}. " if start else ""
        where = primary["properties"].get("Where?", {})
        if where.get("type") == "select" and where.get("select"):
            sub += f"Watching on {where['select']['name']}."
        else:
            sub += "Currently watching."
        extra = page_title(shows[1]) if len(shows) > 1 else (page_title(tv_ip[1]) if len(tv_ip) > 1 else "")
        cards.append(card_html("watch", delays[card_idx % 4], EMOJI_WATCH, "Watching",
                                title, sub, 40, extra))
        card_idx += 1

    # ── BOOKS ──
    if books_ip:
        b = books_ip[0]
        title = page_title(b)
        start = fmt_date(page_start_date(b))
        author_prop = b["properties"].get("Author", {})
        author = ""
        if author_prop.get("type") == "multi_select":
            names = [s["name"] for s in author_prop.get("multi_select", [])]
            author = names[0] if names else ""
        sub = f"{author}. " if author else ""
        sub += f"Started {start}." if start else "Currently reading."
        cards.append(card_html("read", delays[card_idx % 4], EMOJI_READ, "Reading",
                                title, sub, 35))
        card_idx += 1

    # ── LEARNING (static — Japanese always on) ──
    cards.append(card_html("learn", delays[card_idx % 4], EMOJI_LEARN, "Learning",
                            "Japanese",
                            "Daily Duolingo. Slowly. The kind of side quest with no end screen.",
                            20))

    cards_html = "\n".join(cards)

    # ── RECENTLY COMPLETED ──
    def done_list_items(pages: list) -> str:
        html = ""
        for p in pages:
            t = page_title(p)
            r = page_rating(p)
            cls = 'done-top' if r and float(r) >= 9 else ''
            rating_html = f'<span class="done-rating {cls}">{r}</span>' if r else '<span class="done-rating">—</span>'
            html += f'<li><span class="done-title">{t}</span>{rating_html}</li>\n          '
        return html.strip()

    tv_done_html    = done_list_items(tv_done)
    books_done_html = done_list_items(books_done)
    games_done_html = done_list_items(games_done)

    return f"""
  <div class="now-grid">
{cards_html}
  </div>

  <!-- Recently Completed - smaller, denser -->
  <div style="margin-top:64px; max-width:1200px;">
    <div class="reveal" style="font-family:'Space Mono',monospace; font-size:11px; letter-spacing:0.2em; text-transform:uppercase; color:var(--fg-dim); margin-bottom:20px; display:flex; align-items:center; gap:14px;">
      <span>Recently Completed</span>
      <span style="font-size:10px; color:var(--amber); font-weight:600;">&mdash; auto-updated {updated_at}</span>
      <span style="flex:1; height:1px; background:linear-gradient(to right, rgba(240,237,230,0.08), transparent);"></span>
    </div>

    <div class="reveal" style="display:grid; grid-template-columns: repeat(3, 1fr); gap:36px;">

      <div>
        <div style="font-size:10px; letter-spacing:0.2em; text-transform:uppercase; color:var(--teal); margin-bottom:12px; font-family:'Space Mono',monospace;">📺 Watched</div>
        <ul class="done-list">
          {tv_done_html}
        </ul>
      </div>

      <div>
        <div style="font-size:10px; letter-spacing:0.2em; text-transform:uppercase; color:oklch(68% 0.14 160); margin-bottom:12px; font-family:'Space Mono',monospace;">📚 Read</div>
        <ul class="done-list">
          {books_done_html}
        </ul>
      </div>

      <div>
        <div style="font-size:10px; letter-spacing:0.2em; text-transform:uppercase; color:var(--amber); margin-bottom:12px; font-family:'Space Mono',monospace;">🎮 Played</div>
        <ul class="done-list">
          {games_done_html}
        </ul>
      </div>

    </div>
  </div>"""

# ── PATCH HTML ────────────────────────────────────────────────────────────────

def patch_html(now_content: str) -> None:
    with open(HTML_FILE, "r", encoding="utf-8") as f:
        html = f.read()

    start_idx = html.find(NOW_START)
    end_idx   = html.find(NOW_END)

    if start_idx == -1 or end_idx == -1:
        raise ValueError(
            f"Could not find markers '{NOW_START}' and/or '{NOW_END}' in {HTML_FILE}.\n"
            "Add these comment markers around the Now section content in index.html."
        )

    new_html = (
        html[:start_idx + len(NOW_START)]
        + now_content
        + "\n  " + html[end_idx:]
    )

    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(new_html)

    print(f"✅ Patched {HTML_FILE} successfully.")

# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print("🔍 Fetching Notion Media Consumption databases...")

    games_ip  = get_in_progress(DB_GAMES)
    tv_ip     = get_in_progress(DB_MOVIES_TV)
    books_ip  = get_in_progress(DB_BOOKS)

    games_done = get_done(DB_GAMES, 5)
    tv_done    = get_done(DB_MOVIES_TV, 7)
    books_done = get_done(DB_BOOKS, 7)

    print(f"  🎮 Games in progress:  {[page_title(p) for p in games_ip]}")
    print(f"  📺 TV/Movies in progress: {[page_title(p) for p in tv_ip]}")
    print(f"  📖 Books in progress:  {[page_title(p) for p in books_ip]}")

    updated_at = datetime.now(IST).strftime("%-d %b %Y, %-I:%M %p IST")

    now_html = build_now_section(
        games_ip, tv_ip, books_ip,
        games_done, tv_done, books_done,
        updated_at
    )

    patch_html(now_html)
    print(f"⏰ Updated at {updated_at}")

if __name__ == "__main__":
    main()
