#!/usr/bin/env python3
"""
update_now.py
-------------
Fetches Avi's Notion Media Consumption databases (Movies/TV, Books, Games),
finds all "In progress" items, and patches the Now section of index.html.
Runs via GitHub Actions at 1 AM IST (19:30 UTC) every day.

Usage:
    NOTION_TOKEN=your_token python scripts/update_now.py
"""

import re
import random
from datetime import datetime

from notion_lib import (
    DB_MOVIES_TV, DB_BOOKS, DB_GAMES, IST,
    notion_query, get_in_progress,
    page_title, page_rating, page_start_date, page_type, fmt_date,
)

HTML_FILE = "index.html"
NOW_START = "<!-- NOW-SECTION-START -->"
NOW_END   = "<!-- NOW-SECTION-END -->"


def get_done(db_id: str, n: int = 10, pool: int = 60) -> list:
    """Fetch a pool of Done items then weighted-randomly pick n (rating + recency)."""
    pages = notion_query(db_id, {
        "filter": {"property": "Status", "status": {"equals": "Done"}},
        "sorts": [{"property": "Date", "direction": "descending"}],
        "page_size": pool,
    })
    pages = pages[:pool]
    if len(pages) <= n:
        return pages

    total = len(pages)
    weights = []
    for i, page in enumerate(pages):
        recency = (total - i) / total
        r = page_rating(page)
        try:
            rating = float(r) / 10.0 if r else 0.5
        except ValueError:
            rating = 0.5
        weights.append(0.4 * recency + 0.6 * (rating ** 2))

    selected, pool_pages, pool_weights = [], list(pages), list(weights)
    for _ in range(n):
        total_w = sum(pool_weights)
        r = random.random() * total_w
        cumulative = 0.0
        for j, w in enumerate(pool_weights):
            cumulative += w
            if r <= cumulative:
                selected.append(pool_pages.pop(j))
                pool_weights.pop(j)
                break
    return selected

# ── HTML GENERATION ───────────────────────────────────────────────────────────

EMOJI_GAME  = "⚔️"
EMOJI_WATCH = "📺"
EMOJI_READ  = "📖"


def card_html(data_type: str, delay: str, emoji: str, type_label: str,
              title: str, sub: str, progress_pct: int, extra_title: str = "") -> str:
    extra = (f' <span style="color:var(--fg-dim); font-size:13px; font-weight:400;">'
             f'+ {extra_title}</span>') if extra_title else ""
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


def build_now_section(games_ip, tv_ip, books_ip,
                      games_done, tv_done, books_done, updated_at) -> str:
    cards = []
    delays = ["reveal-delay-1", "reveal-delay-2", "reveal-delay-3", "reveal-delay-4"]
    card_idx = 0

    if games_ip:
        g = games_ip[0]
        title = page_title(g)
        start = fmt_date(page_start_date(g))
        sub = f"Started {start}. " if start else ""
        sub += ("FromSoftware vibes. Every death is a lesson."
                if "sekiro" in title.lower() else "Currently in the controller.")
        cards.append(card_html("game", delays[card_idx % 4], EMOJI_GAME, "Playing",
                                title, sub, 50,
                                page_title(games_ip[1]) if len(games_ip) > 1 else ""))
        card_idx += 1

    if tv_ip:
        shows = [p for p in tv_ip if page_type(p) in ("TV Show", "")]
        primary = shows[0] if shows else tv_ip[0]
        title = page_title(primary)
        start = fmt_date(page_start_date(primary))
        sub = f"Since {start}. " if start else ""
        where = primary["properties"].get("Where?", {})
        if where.get("type") == "select" and where.get("select"):
            sub += f"Watching on {where['select']['name']}."
        else:
            sub += "Currently watching."
        extra = (page_title(shows[1]) if len(shows) > 1
                 else (page_title(tv_ip[1]) if len(tv_ip) > 1 else ""))
        cards.append(card_html("watch", delays[card_idx % 4], EMOJI_WATCH, "Watching",
                                title, sub, 40, extra))
        card_idx += 1

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

    cards_html = "\n".join(cards)

    active_icons = []
    if games_ip:  active_icons.append("🎮 Playing")
    if tv_ip:     active_icons.append("📺 Watching")
    if books_ip:  active_icons.append("📖 Reading")
    icons_str = " &nbsp;·&nbsp; ".join(active_icons)
    intro_html = f'''  <div class="reveal" style="margin-bottom:48px;">
    <p style="color:var(--fg-dim); font-size:16px; max-width:480px; line-height:1.6; margin-bottom:14px;">
      What I&rsquo;m currently obsessing over.
    </p>
    <div style="color:var(--amber); font-family:\'Space Mono\',monospace; font-size:12px; letter-spacing:0.1em; white-space:nowrap;">{icons_str}</div>
  </div>'''

    def done_list_items(pages: list) -> str:
        html = ""
        for p in pages:
            t = page_title(p)
            r = page_rating(p)
            cls = 'done-top' if r and float(r) >= 9 else ''
            rating_html = (f'<span class="done-rating {cls}">{r}</span>'
                           if r else '<span class="done-rating">—</span>')
            html += f'<li><span class="done-title">{t}</span>{rating_html}</li>\n          '
        return html.strip()

    tv_done_html    = done_list_items(tv_done)
    books_done_html = done_list_items(books_done)
    games_done_html = done_list_items(games_done)

    cta_html = '''
  <div class="reveal" style="margin-top:56px; text-align:center;">
    <a href="/now" class="library-cta">
      View the full library &nbsp;&rarr;
    </a>
  </div>'''

    return f"""
{intro_html}
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
  </div>
{cta_html}"""

# ── PATCH HTML ────────────────────────────────────────────────────────────────

def patch_html(now_content: str) -> None:
    with open(HTML_FILE, "r", encoding="utf-8") as f:
        html = f.read()

    if NOW_START not in html or NOW_END not in html:
        raise ValueError(
            f"Could not find markers '{NOW_START}' / '{NOW_END}' in {HTML_FILE}."
        )

    days = (datetime.now(IST) - datetime(2026, 1, 1, tzinfo=IST)).days + 1
    html = re.sub(
        r'(<div class="stat-value" id="days-in-2026">)\d+(</div>)',
        rf'\g<1>{days}\2',
        html,
    )

    start_idx = html.find(NOW_START)
    end_idx   = html.find(NOW_END)
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

    games_ip = get_in_progress(DB_GAMES)
    tv_ip    = get_in_progress(DB_MOVIES_TV)
    books_ip = get_in_progress(DB_BOOKS)

    games_done = get_done(DB_GAMES, 10)
    tv_done    = get_done(DB_MOVIES_TV, 10)
    books_done = get_done(DB_BOOKS, 10)

    print(f"  🎮 Games in progress:  {[page_title(p) for p in games_ip]}")
    print(f"  📺 TV/Movies in progress: {[page_title(p) for p in tv_ip]}")
    print(f"  📖 Books in progress:  {[page_title(p) for p in books_ip]}")

    updated_at = datetime.now(IST).strftime("%-d %b %Y, %-I:%M %p IST")
    now_html = build_now_section(
        games_ip, tv_ip, books_ip,
        games_done, tv_done, books_done, updated_at,
    )
    patch_html(now_html)
    print(f"⏰ Updated at {updated_at}")


if __name__ == "__main__":
    main()
