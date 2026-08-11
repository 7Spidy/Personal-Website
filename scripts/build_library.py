#!/usr/bin/env python3
"""
build_library.py
----------------
Builds the dedicated media library page (now.html) from Notion data,
using cover images set directly on each Notion page. Covers are
cached to assets/covers/ so nightly runs stay fast and idempotent.

Usage:
    NOTION_TOKEN=... python scripts/build_library.py
"""

import re
import json
import html
import ssl
import hashlib
import pathlib
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
import random
from datetime import datetime

from notion_lib import (
    DB_MOVIES_TV, DB_BOOKS, DB_GAMES, IST,
    get_in_progress, get_done_all,
    page_title, page_rating, page_start_date,
    page_type, page_field, page_poster, fmt_date, days_since,
)

ROOT       = pathlib.Path(__file__).resolve().parent.parent
OUT_FILE   = ROOT / "now.html"
COVERS_DIR = ROOT / "assets" / "covers"
OVERRIDES  = pathlib.Path(__file__).resolve().parent / "cover_overrides.json"
YEAR       = datetime.now(IST).year

CAT_META = {
    "games": {"emoji": "🎮", "label": "Played",  "accent": "var(--amber)"},
    "tv":    {"emoji": "📺", "label": "Watched", "accent": "var(--teal)"},
    "books": {"emoji": "📚", "label": "Read",    "accent": "var(--green)"},
}

# ── COVER RESOLUTION ──────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "untitled"


_SSL_CTX = ssl.create_default_context()
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AviLibraryBot/1.0)"}


def _get(url: str, extra_headers: dict = None, timeout: int = 20) -> bytes:
    h = {**_HEADERS, **(extra_headers or {})}
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as r:
        return r.read()


def _get_json(url: str, extra_headers: dict = None) -> dict:
    try:
        return json.loads(_get(url, extra_headers))
    except Exception:
        return {}


def load_overrides() -> dict:
    try:
        data = json.loads(OVERRIDES.read_text(encoding="utf-8"))
        return {k: v for k, v in data.items() if not k.startswith("_")}
    except Exception:
        return {}


def resolve_cover(item: dict, overrides: dict) -> str:
    """Return a site-absolute cover path, downloading+caching once. '' on miss."""
    title = item["title"]
    cat = item["cat"]
    disambig = item.get("type") or item.get("author") or item.get("system") or ""
    h = hashlib.sha1((title + "|" + disambig).encode()).hexdigest()[:6]
    fname = f"{slugify(title)}-{h}.jpg"
    cat_dir = COVERS_DIR / cat
    cat_dir.mkdir(parents=True, exist_ok=True)
    dest = cat_dir / fname
    rel = f"/assets/covers/{cat}/{fname}"

    if dest.exists() and dest.stat().st_size > 0:
        return rel

    src = overrides.get(title, "") or item.get("notion_cover_url", "")

    if not src:
        print(f"  ⚠️  no Poster in Notion: [{cat}] {title}")
        return ""

    # Retry up to 3 times for transient failures
    for attempt in range(3):
        try:
            data = _get(src)
            if data and len(data) > 1024:
                dest.write_bytes(data)
                print(f"  ✓  [{cat}] {title} ({len(data)//1024}KB)")
                return rel
            else:
                print(f"  ⚠️  tiny/empty response ({len(data) if data else 0}B): [{cat}] {title} — url: {src[:80]}")
                return ""
        except urllib.error.HTTPError as e:
            print(f"  ⚠️  HTTP {e.code} attempt {attempt+1}: [{cat}] {title} — {src[:80]}")
        except Exception as e:
            print(f"  ⚠️  {type(e).__name__} attempt {attempt+1}: [{cat}] {title} — {e}")
    print(f"  ✗  gave up after 3 attempts: [{cat}] {title}")
    return ""

# ── DATA SHAPING ──────────────────────────────────────────────────────────────

def shape(page: dict, cat: str) -> dict:
    r = page_rating(page)
    try:
        rating = float(r) if r else None
    except ValueError:
        rating = None
    # Resolve end date: try separate "End Date" property first, then the "end"
    # field of a Notion date-range on the "Date" property (enabled via "Include end date")
    end = ""
    end_prop = page.get("properties", {}).get("End Date")
    if end_prop and end_prop.get("type") == "date":
        d_obj = end_prop.get("date")
        if d_obj:
            end = d_obj.get("start", "") or ""
    if not end:
        date_prop = page.get("properties", {}).get("Date")
        if date_prop and date_prop.get("type") == "date":
            d_obj = date_prop.get("date")
            if d_obj:
                end = d_obj.get("end", "") or ""
    yr_src = end or page.get("properties", {}).get("Date", {}).get("date", {}).get("start", "")
    yr = None
    try:
        yr = datetime.fromisoformat(yr_src).year if yr_src else None
    except Exception:
        yr = None
    author = page_field(page, "Author")
    if isinstance(author, list):
        author = author[0] if author else ""
    return {
        "title": page_title(page),
        "cat": cat,
        "rating": rating,
        "rating_str": r,
        "start": page_start_date(page),
        "end": end,
        "type": page_type(page) if cat == "tv" else "",
        "where": page_field(page, "Where?") if cat == "tv" else "",
        "author": author if cat == "books" else "",
        "system": page_field(page, "System") if cat == "games" else "",
        "hours": page_field(page, "Hours Played") if cat == "games" else None,
        "days": page_field(page, "Days Spent"),
        "year": yr,
        "notion_cover_url": page_poster(page),
        "cover": "",
    }

# ── HTML BUILDING ─────────────────────────────────────────────────────────────

def esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def cover_or_fallback(it: dict) -> str:
    meta = CAT_META[it["cat"]]
    if it["cover"]:
        return (f'<img class="cov-img" loading="lazy" '
                f'src="{esc(it["cover"])}" alt="{esc(it["title"])}">')
    return (f'<div class="cov-fallback" data-cat="{it["cat"]}">'
            f'<span class="cov-fb-emoji">{meta["emoji"]}</span>'
            f'<span class="cov-fb-title">{esc(it["title"])}</span></div>')


def rating_badge(it: dict) -> str:
    if not it["rating_str"]:
        return ""
    top = it["rating"] is not None and it["rating"] >= 9
    return (f'<span class="cov-rating{" top" if top else ""}">'
            f'{esc(it["rating_str"])}</span>')


def media_card(it: dict) -> str:
    meta = CAT_META[it["cat"]]
    wip = it.get("status") == "wip"
    sub_bits = []
    if it["cat"] == "books" and it["author"]:
        sub_bits.append(esc(it["author"]))
    if it["cat"] == "games" and it["system"]:
        sub_bits.append(esc(it["system"]))
    if it["cat"] == "tv" and it["where"]:
        sub_bits.append(esc(it["where"]))
    s = fmt_date(it["start"], "%-d %b %Y") if it["start"] else ""
    e = fmt_date(it["end"], "%-d %b %Y") if it["end"] else ""
    if wip:
        sub_bits.append(f"Started {s}" if s else "In progress")
    else:
        if s and e:
            sub_bits.append(f"{s} → {e}")
        elif e:
            sub_bits.append(e)
        elif s:
            sub_bits.append(s)
    sub = " · ".join(b for b in sub_bits if b)
    wip_badge = ('<span class="cov-wip" style="color:'
                 f'{meta["accent"]}">&#9679; Now</span>') if wip else ""
    return f'''
    <article class="lib-card" data-cat="{it['cat']}"
             data-rating="{it['rating'] if it['rating'] is not None else 0}"
             data-date="{esc(it['end'] or it['start'])}">
      <div class="cov">
        {cover_or_fallback(it)}
        {rating_badge(it)}
        {wip_badge}
      </div>
      <div class="lib-card-title">{esc(it['title'])}</div>
      <div class="lib-card-sub">{sub}</div>
    </article>'''


def hero_card(it: dict) -> str:
    meta = CAT_META[it["cat"]]
    d = days_since(it["start"])
    day_txt = f"Day {d}" if d and d > 0 else "Just started"
    started = fmt_date(it["start"]) if it["start"] else ""

    facts = []
    if it["cat"] == "games":
        if it["system"]:
            facts.append(esc(str(it["system"])))
        if isinstance(it["hours"], (int, float)) and it["hours"]:
            facts.append(f'{int(it["hours"])}h in')
    elif it["cat"] == "tv":
        if it["type"]:
            facts.append(esc(str(it["type"])))
        if it["where"]:
            facts.append(esc(str(it["where"])))
    elif it["cat"] == "books":
        if it["author"]:
            facts.append(esc(str(it["author"])))
    facts_html = (f'<span class="hero-facts">{" · ".join(facts)}</span>'
                  if facts else "")

    prog = day_txt + (f" · since {started}" if started else "")
    rate_html = (f'<span class="hero-rate">★ {esc(it["rating_str"])}</span>'
                 if it["rating_str"] else "")

    if it["cover"]:
        cover_layer = (f'<img class="hero-cover" src="{esc(it["cover"])}" '
                       f'alt="{esc(it["title"])}" loading="lazy">')
        scrim = ('linear-gradient(180deg,rgba(11,10,9,.10),'
                 'rgba(11,10,9,.55) 45%,rgba(11,10,9,.96))')
    else:
        cover_layer = f'<span class="hero-fb">{meta["emoji"]}</span>'
        scrim = ('linear-gradient(160deg,rgba(255,255,255,.05),'
                 'rgba(255,255,255,.01))')
    label = (meta['label'].replace('Played', 'Playing')
             .replace('Watched', 'Watching').replace('Read', 'Reading'))
    return f'''
      <a class="hero-card" data-cat="{it['cat']}">
        {cover_layer}
        <div class="hero-scrim" style="background:{scrim}"></div>
        {rate_html}
        <div class="hero-card-body">
          <span class="hero-card-kicker" style="color:{meta['accent']}">
            {meta['emoji']} {label}</span>
          <span class="hero-card-title">{esc(it['title'])}</span>
          {facts_html}
          <span class="hero-card-meta">{prog}</span>
        </div>
      </a>'''


def stat_tile(value: str, label: str, accent: str = "var(--amber)") -> str:
    num = re.match(r"^[\d.]+", str(value))
    if num:
        return (f'<div class="stat-tile"><div class="stat-num" '
                f'data-count="{num.group(0)}" style="color:{accent}">'
                f'{esc(value)}</div><div class="stat-lab">{esc(label)}</div></div>')
    return (f'<div class="stat-tile"><div class="stat-num" '
            f'style="color:{accent}">{esc(value)}</div>'
            f'<div class="stat-lab">{esc(label)}</div></div>')


WINNER_LABELS = {
    "tv":    ("Watch", "Watches"),
    "books": ("Read",  "Reads"),
    "games": ("Game",  "Games"),
}


def winner_group(items: list, cat: str) -> str:
    if not items:
        return ""
    meta = CAT_META[cat]
    singular, plural = WINNER_LABELS[cat]
    label = singular if len(items) == 1 else plural
    cards = "".join(f'''
      <div class="winner">
        <div class="winner-cov">{cover_or_fallback(it)}</div>
        <div class="winner-info">
          <span class="winner-kicker" style="color:{meta['accent']}">Best {label} of {YEAR}</span>
          <span class="winner-title">{esc(it['title'])}</span>
          <span class="winner-rating">★ {esc(it['rating_str'] or '—')}</span>
        </div>
      </div>''' for it in items)
    return f'<div class="winner-group">{cards}</div>'


_WITTY = {
    "games": [
        "Day {d} of {title}. Still refusing to look up a guide.",
        "Currently {title}. The 'quick session' was three hours ago.",
        "{title} — because sleep is overrated and free time is for cowards.",
        "Stuck in {title}. The boss fight can wait. Probably.",
        "Playing {title} for research purposes. Definitely research.",
        "In {title} since day {d}. The respawns are educational.",
        "{title}: entered with a plan, exited with only screenshots.",
        "Day {d}. {title} has not finished me yet. Mutual respect.",
    ],
    "tv": [
        "Watching {title}. It was supposed to be background noise.",
        "{title} promised one episode. It lied beautifully.",
        "Currently emotionally compromised by {title}.",
        "Somewhere in {title}, refusing to check how many seasons are left.",
        "{title} — the show that turned 'five more minutes' into a lifestyle.",
        "Invested in {title} in ways that cannot be undone.",
        "Day {d} of {title}. The plot has thickened considerably.",
    ],
    "books": [
        "Somewhere in {title}. The bookmark has moved exactly twice this week.",
        "Reading {title}. Or thinking about reading it. Same thing.",
        "{title}: open on the nightstand, judging silently.",
        "Midway through {title}. The plot thickens. So does the coffee.",
        "{title} — not going to read itself, but we're negotiating.",
        "Day {d} with {title}. It's going well. I think.",
        "Currently: {title}. Sleep is optional, apparently.",
    ],
}


def witty_line(hero_items: list) -> str:
    if not hero_items:
        return "The queue is clear. The backlog is not."
    rng = random.Random(datetime.now(IST).toordinal())
    item = rng.choice(hero_items)
    templates = _WITTY.get(item["cat"], ["{title} — in progress."])
    tmpl = rng.choice(templates)
    d = days_since(item["start"]) or 1
    return tmpl.format(title=item["title"], d=d)


def build_page(in_prog: list, done: list) -> str:
    # In progress hero — every active item, ordered games → shows → books
    order = {"games": 0, "tv": 1, "books": 2}
    hero_items = sorted(in_prog, key=lambda x: (order.get(x["cat"], 9),
                                                -(days_since(x["start"]) or 0)))
    hero_html = "\n".join(hero_card(i) for i in hero_items) or \
        '<p class="empty">Nothing in progress right now. Touch grass achieved.</p>'

    n_games = sum(1 for x in hero_items if x["cat"] == "games")
    n_tv = sum(1 for x in hero_items if x["cat"] == "tv")
    n_books = sum(1 for x in hero_items if x["cat"] == "books")
    bits = []
    if n_games: bits.append(f"{n_games} game" + ("s" if n_games != 1 else ""))
    if n_tv:    bits.append(f"{n_tv} show" + ("s" if n_tv != 1 else ""))
    if n_books: bits.append(f"{n_books} book" + ("s" if n_books != 1 else ""))
    hero_sub = witty_line(hero_items) + (
        f" Scroll for {YEAR}'s stats and a {len(done)}-title archive."
        if hero_items else f" {len(done)} titles logged this year."
    )

    # Year stats
    yr_items = [x for x in done if x["year"] == YEAR]
    rated = [x["rating"] for x in yr_items if x["rating"] is not None]
    avg = round(sum(rated) / len(rated), 1) if rated else 0
    g_hours = sum(x["hours"] for x in yr_items
                  if x["cat"] == "games" and isinstance(x["hours"], (int, float)))
    cnt = {c: sum(1 for x in yr_items if x["cat"] == c)
           for c in ("tv", "books", "games")}

    def bests(cat):
        pool = [x for x in yr_items if x["cat"] == cat and x["rating"] is not None]
        if not pool:
            return []
        top_rating = max(x["rating"] for x in pool)
        tied = [x for x in pool if x["rating"] == top_rating]  # exact equality — 9.5 != 10
        tied.sort(key=lambda x: (x["end"] or x["start"] or ""), reverse=True)  # most recent first
        return tied

    stats_html = "".join([
        stat_tile(str(cnt["tv"]), "Movies & TV", "var(--teal)"),
        stat_tile(str(cnt["books"]), "Books", "var(--green)"),
        stat_tile(str(cnt["games"]), "Games", "var(--amber)"),
        stat_tile(f"{int(g_hours)}", "Hours Gamed", "var(--amber)"),
        stat_tile(str(avg), "Avg Rating", "var(--fg)"),
    ])
    winners_html = (
        winner_group(bests("tv"), "tv")
        + winner_group(bests("books"), "books")
        + winner_group(bests("games"), "games")
    )

    # This month — per-month panels (current year only), switchable via dropdown.
    # Use end date when present, else start date (Done items may lack an end).
    now = datetime.now(IST)
    def eff_date(x):
        return x["end"] or x["start"] or ""
    cur_month = f"{now.year}-{now.month:02d}"

    month_map = defaultdict(list)
    for x in done:
        key = eff_date(x)[:7]
        if key:
            month_map[key].append(x)
    for items in month_map.values():
        items.sort(key=eff_date, reverse=True)

    month_keys = sorted(month_map.keys(), reverse=True)  # omits empty months automatically

    def month_panel_items(key):
        # In-progress items only surface under the current month.
        return [*hero_items, *month_map[key]] if key == cur_month else month_map[key]

    def month_panel_html(key):
        cards = "".join(media_card(i) for i in month_panel_items(key))
        return f'<div class="month-panel" data-month="{esc(key)}">{cards}</div>'

    month_panels = "".join(month_panel_html(k) for k in month_keys)

    def month_label(key):
        return datetime.strptime(key, "%Y-%m").strftime("%B %Y")

    def month_note(key):
        items = month_panel_items(key)
        wip_ct = sum(1 for i in items if i.get("status") == "wip")
        done_ct = len(items) - wip_ct
        bits = []
        if wip_ct: bits.append(f"{wip_ct} in progress")
        if done_ct: bits.append(f"{done_ct} finished")
        return " · ".join(bits) if bits else "nothing logged"

    month_meta = {k: {"label": month_label(k), "note": month_note(k)} for k in month_keys}
    month_meta["_current"] = cur_month
    month_meta_json = json.dumps(month_meta)

    initial_key = cur_month if cur_month in month_map else (month_keys[0] if month_keys else None)
    initial_label = month_label(initial_key) if initial_key else ""
    initial_note = month_note(initial_key) if initial_key else "nothing logged"

    month_section = "" if not month_keys else f'''
  <section class="lib-sec reveal">
    <div class="sec-head"><span class="sec-num">02</span>
      <h2 id="month-heading">{esc(initial_label)}</h2>
      <span class="sec-note" id="month-note">{esc(initial_note)}</span>
      <select id="month-select" class="month-select">
        {"".join(f'<option value="{esc(k)}">{esc(month_meta[k]["label"])}</option>' for k in month_keys)}
      </select>
    </div>
    <div class="filter-wrap" id="month-wrap">
      <div class="lib-controls">
        <div class="filter-group">
          <input type="radio" name="mfilt" id="mf-all" checked>
          <label for="mf-all">All</label>
          <input type="radio" name="mfilt" id="mf-tv">
          <label for="mf-tv">📺 Watched</label>
          <input type="radio" name="mfilt" id="mf-books">
          <label for="mf-books">📚 Read</label>
          <input type="radio" name="mfilt" id="mf-games">
          <label for="mf-games">🎮 Played</label>
        </div>
      </div>
      {month_panels}
    </div>
    <script id="month-data" type="application/json">{month_meta_json}</script>
  </section>'''

    # All time
    all_sorted = sorted(done, key=eff_date, reverse=True)
    all_html = "".join(media_card(i) for i in all_sorted)

    updated = now.strftime("%-d %b %Y, %-I:%M %p IST")
    css = PAGE_CSS
    js = PAGE_JS

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>The Library — Avi AI</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>{css}</style>
</head>
<body>

<nav>
  <a href="/" class="nav-logo">&larr; AVI.AI</a>
  <div class="nav-mid">THE LIBRARY</div>
</nav>

<header class="lib-hero">
  <div class="lib-hero-eyebrow">Media Consumption · Live from Notion</div>
  <h1 class="lib-hero-title">The<br><span>Library.</span></h1>
  <p class="lib-hero-sub">{esc(hero_sub)} Auto-synced from Notion every night.</p>
  <div class="hero-row">
{hero_html}
  </div>
  <a class="scroll-cue" href="#sec-01">Explore the archive
    <span class="arrow">&darr;</span></a>
</header>

<section class="lib-sec reveal" id="sec-01">
  <div class="sec-head"><span class="sec-num">01</span>
    <h2>{YEAR} So Far</h2>
    <span class="sec-note">a year in media</span>
  </div>
  <div class="stat-grid">{stats_html}</div>
  <div class="winners">{winners_html}</div>
</section>
{month_section}
<section class="lib-sec reveal">
  <div class="sec-head"><span class="sec-num">{'03' if month_keys else '02'}</span>
    <h2>All Time</h2>
    <span class="sec-note">{len(done)} logged</span>
  </div>
  <div class="filter-wrap" id="all-wrap">
    <div class="lib-controls">
      <div class="filter-group">
        <input type="radio" name="catf" id="f-all" checked>
        <label for="f-all">All</label>
        <input type="radio" name="catf" id="f-tv">
        <label for="f-tv">📺 Watched</label>
        <input type="radio" name="catf" id="f-books">
        <label for="f-books">📚 Read</label>
        <input type="radio" name="catf" id="f-games">
        <label for="f-games">🎮 Played</label>
      </div>
      <div class="sort-group">
        <button class="sort-btn active" data-sort="date">Newest</button>
        <button class="sort-btn" data-sort="rating">Top Rated</button>
      </div>
    </div>
    <div class="card-grid" id="all-grid">{all_html}</div>
  </div>
</section>

<footer class="lib-foot">
  <a href="/">&larr; Back home</a>
  <span>Auto-updated nightly from Notion · {esc(updated)}</span>
</footer>

<script>{js}</script>
</body>
</html>
'''

# ── STATIC ASSETS (CSS / JS) ──────────────────────────────────────────────────

PAGE_CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0b0a09;--bg2:#111009;--fg:#f0ede6;--fg-dim:#8a8070;
--amber:oklch(68% 0.18 52);--amber-glow:oklch(68% 0.18 52 / 0.15);
--teal:oklch(68% 0.14 200);--green:oklch(68% 0.14 160);--red:oklch(58% 0.18 25)}
html{scroll-behavior:smooth}
body{background:var(--bg);color:var(--fg);font-family:'Space Grotesk',sans-serif;
overflow-x:hidden}
a{color:inherit}
body::before{content:'';position:fixed;inset:0;z-index:1000;pointer-events:none;
opacity:.035;background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
animation:grain .5s steps(1) infinite}
@keyframes grain{0%,100%{transform:translate(0,0)}20%{transform:translate(2%,1%)}
40%{transform:translate(3%,-1%)}60%{transform:translate(1%,-2%)}
80%{transform:translate(2%,3%)}}
nav{position:fixed;top:0;left:0;right:0;z-index:100;padding:24px 48px;
display:flex;align-items:center;justify-content:space-between;
background:linear-gradient(to bottom,var(--bg),transparent)}
.nav-logo{font-family:'Space Mono',monospace;font-size:13px;letter-spacing:.2em;
color:var(--amber);text-decoration:none;text-transform:uppercase}
.nav-mid{font-family:'Space Mono',monospace;font-size:11px;letter-spacing:.3em;
color:var(--fg-dim);text-transform:uppercase}
.lib-hero{min-height:auto;display:flex;flex-direction:column;justify-content:center;
padding:140px 48px 64px;position:relative}
.lib-hero-eyebrow{font-family:'Space Mono',monospace;font-size:12px;
letter-spacing:.2em;text-transform:uppercase;color:var(--amber);margin-bottom:24px}
.lib-hero-title{font-size:clamp(56px,11vw,150px);font-weight:700;line-height:.9;
letter-spacing:-.04em;margin-bottom:28px}
.lib-hero-title span{color:var(--amber)}
.lib-hero-sub{font-size:clamp(15px,1.6vw,18px);color:var(--fg-dim);max-width:560px;
line-height:1.6;margin-bottom:52px}
.hero-row{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));
gap:18px;max-width:1240px}
.hero-card{position:relative;aspect-ratio:16/9;border-radius:18px;overflow:hidden;
border:1px solid rgba(255,255,255,.08);background:var(--bg2);
display:flex;align-items:flex-end;text-decoration:none;
transition:transform .4s cubic-bezier(.16,1,.3,1),border-color .3s}
.hero-card:hover{transform:translateY(-8px);border-color:rgba(255,255,255,.2)}
.hero-cover{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.hero-scrim{position:absolute;inset:0}
.hero-fb{position:absolute;inset:0;display:flex;align-items:center;
justify-content:center;font-size:80px;opacity:.22}
.hero-rate{position:absolute;top:16px;right:16px;z-index:2;
font-family:'Space Mono',monospace;font-size:12px;font-weight:700;
padding:6px 12px;border-radius:100px;background:rgba(11,10,9,.7);
color:var(--amber);border:1px solid rgba(255,255,255,.12)}
.hero-card-body{position:relative;z-index:2;padding:26px;display:flex;
flex-direction:column;gap:7px}
.hero-card-kicker{font-family:'Space Mono',monospace;font-size:11px;
letter-spacing:.16em;text-transform:uppercase}
.hero-card-title{font-size:23px;font-weight:700;line-height:1.15;letter-spacing:-.02em}
.hero-facts{font-size:13px;color:var(--fg);opacity:.85}
.hero-card-meta{font-family:'Space Mono',monospace;font-size:12px;color:var(--fg-dim)}
.scroll-cue{margin-top:46px;display:inline-flex;align-items:center;gap:10px;
font-family:'Space Mono',monospace;font-size:11px;letter-spacing:.2em;
text-transform:uppercase;color:var(--fg-dim);text-decoration:none;width:max-content;
transition:color .25s}
.scroll-cue:hover{color:var(--amber)}
.scroll-cue .arrow{display:inline-block;animation:bob 1.6s ease-in-out infinite}
@keyframes bob{0%,100%{transform:translateY(0)}50%{transform:translateY(5px)}}
.lib-sec{padding:90px 48px;max-width:1400px;margin:0 auto}
.sec-head{display:flex;align-items:baseline;gap:18px;margin-bottom:48px;
border-bottom:1px solid rgba(255,255,255,.07);padding-bottom:20px}
.sec-num{font-family:'Space Mono',monospace;font-size:13px;color:var(--amber);
letter-spacing:.2em}
.sec-head h2{font-size:clamp(28px,4vw,46px);font-weight:700;letter-spacing:-.025em}
.sec-note{margin-left:auto;font-family:'Space Mono',monospace;font-size:11px;
letter-spacing:.16em;text-transform:uppercase;color:var(--fg-dim)}
.month-select{font-family:'Space Mono',monospace;font-size:11px;letter-spacing:.1em;
text-transform:uppercase;color:var(--fg-dim);background:var(--bg2);
border:1px solid rgba(255,255,255,.1);border-radius:100px;padding:9px 16px;
margin-left:12px}
.month-select:hover{color:var(--fg);border-color:rgba(255,255,255,.25)}
.stat-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:14px;margin-bottom:24px}
.stat-tile{padding:32px 22px;border:1px solid rgba(255,255,255,.07);
border-radius:14px;background:rgba(255,255,255,.02)}
.stat-num{font-size:clamp(28px,3.4vw,46px);font-weight:700;line-height:1;
letter-spacing:-.03em;margin-bottom:10px}
.stat-lab{font-family:'Space Mono',monospace;font-size:10px;letter-spacing:.14em;
text-transform:uppercase;color:var(--fg-dim)}
.winners{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
.winner-group{display:flex;flex-direction:column;gap:12px}
.winner{display:flex;gap:18px;padding:20px;border:1px solid rgba(255,255,255,.07);
border-radius:14px;background:rgba(255,255,255,.02)}
.winner-cov{width:132px;height:84px;border-radius:8px;overflow:hidden;flex-shrink:0}
.winner-cov img,.winner-cov .cov-fallback{width:100%;height:100%;object-fit:cover}
.winner-info{display:flex;flex-direction:column;justify-content:center;gap:8px}
.winner-kicker{font-family:'Space Mono',monospace;font-size:10px;letter-spacing:.14em;
text-transform:uppercase}
.winner-title{font-size:18px;font-weight:600;line-height:1.2}
.winner-rating{font-family:'Space Mono',monospace;font-size:13px;color:var(--amber)}
.lib-controls{display:flex;flex-wrap:wrap;gap:20px;justify-content:space-between;
align-items:center;margin-bottom:36px}
.filter-group input{position:absolute;opacity:0;pointer-events:none}
.filter-group label{display:inline-block;font-family:'Space Mono',monospace;
font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--fg-dim);
padding:9px 18px;border:1px solid rgba(255,255,255,.1);border-radius:100px;
margin-right:8px;transition:all .2s}
.filter-group label:hover{color:var(--fg);border-color:rgba(255,255,255,.25)}
#f-all:checked~label[for=f-all],#f-tv:checked~label[for=f-tv],
#f-books:checked~label[for=f-books],#f-games:checked~label[for=f-games],
#mf-all:checked~label[for=mf-all],#mf-tv:checked~label[for=mf-tv],
#mf-books:checked~label[for=mf-books],#mf-games:checked~label[for=mf-games]{
color:var(--bg);background:var(--amber);border-color:var(--amber)}
.sort-btn{font-family:'Space Mono',monospace;font-size:11px;letter-spacing:.12em;
text-transform:uppercase;color:var(--fg-dim);background:none;
border:1px solid rgba(255,255,255,.1);border-radius:100px;padding:9px 18px;
margin-left:8px;transition:all .2s}
.sort-btn:hover{color:var(--fg)}
.sort-btn.active{color:var(--amber);border-color:var(--amber)}
.card-grid,.month-panel.active{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));
gap:20px}
.month-panel{display:none}
.lib-card{display:flex;flex-direction:column;gap:10px}
.cov{position:relative;aspect-ratio:16/9;border-radius:12px;overflow:hidden;
border:1px solid rgba(255,255,255,.07);background:var(--bg2);
transition:transform .35s cubic-bezier(.16,1,.3,1),box-shadow .35s}
.lib-card:hover .cov{transform:scale(1.035);
box-shadow:0 18px 40px -16px rgba(0,0,0,.8),0 0 0 1px var(--amber-glow)}
.cov-img{width:100%;height:100%;object-fit:cover;display:block}
.cov-fallback{width:100%;height:100%;display:flex;flex-direction:column;
align-items:center;justify-content:center;gap:14px;padding:18px;text-align:center;
background:linear-gradient(160deg,rgba(255,255,255,.06),rgba(255,255,255,.01))}
.cov-fallback[data-cat=tv]{background:linear-gradient(160deg,oklch(68% 0.14 200 / .22),rgba(255,255,255,.01))}
.cov-fallback[data-cat=books]{background:linear-gradient(160deg,oklch(68% 0.14 160 / .22),rgba(255,255,255,.01))}
.cov-fallback[data-cat=games]{background:linear-gradient(160deg,oklch(68% 0.18 52 / .22),rgba(255,255,255,.01))}
.cov-fb-emoji{font-size:42px}
.cov-fb-title{font-size:15px;font-weight:600;line-height:1.3}
.cov-rating{position:absolute;top:10px;right:10px;font-family:'Space Mono',monospace;
font-size:12px;font-weight:700;padding:5px 9px;border-radius:8px;
background:rgba(11,10,9,.78);color:var(--fg-dim);backdrop-filter:blur(4px)}
.cov-rating.top{color:var(--bg);background:var(--amber)}
.lib-card[data-cat=tv] .cov{border-bottom:3px solid var(--teal)}
.lib-card[data-cat=books] .cov{border-bottom:3px solid var(--green)}
.lib-card[data-cat=games] .cov{border-bottom:3px solid var(--amber)}
.cov-wip{position:absolute;top:10px;left:10px;font-family:'Space Mono',monospace;
font-size:9px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;
padding:5px 9px;border-radius:7px;background:rgba(11,10,9,.82);
backdrop-filter:blur(4px);animation:wipPulse 2s ease-in-out infinite}
@keyframes wipPulse{0%,100%{opacity:1}50%{opacity:.55}}
.lib-card-title{font-size:14px;font-weight:600;line-height:1.3}
.lib-card-sub{font-family:'Space Mono',monospace;font-size:11px;color:var(--fg-dim)}
.empty{color:var(--fg-dim);font-size:16px}
.lib-foot{padding:50px 48px;border-top:1px solid rgba(255,255,255,.06);
display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:16px;
font-family:'Space Mono',monospace;font-size:12px;color:var(--fg-dim)}
.lib-foot a{text-decoration:none}.lib-foot a:hover{color:var(--amber)}
.reveal{transform:translateY(22px);
transition:transform .8s cubic-bezier(.16,1,.3,1)}
.reveal.vis{transform:translateY(0)}
#month-wrap:has(#mf-tv:checked) .lib-card:not([data-cat=tv]),
#month-wrap:has(#mf-books:checked) .lib-card:not([data-cat=books]),
#month-wrap:has(#mf-games:checked) .lib-card:not([data-cat=games]),
#all-wrap:has(#f-tv:checked) .lib-card:not([data-cat=tv]),
#all-wrap:has(#f-books:checked) .lib-card:not([data-cat=books]),
#all-wrap:has(#f-games:checked) .lib-card:not([data-cat=games]){display:none}
@media(max-width:1100px){.stat-grid{grid-template-columns:repeat(3,1fr)}
.winners,.hero-row{grid-template-columns:1fr}}
@media(max-width:760px){nav{padding:18px 18px}.lib-hero{padding:104px 18px 48px}
.lib-sec{padding:54px 18px}
.card-grid,.month-panel.active{grid-template-columns:repeat(2,1fr);gap:12px}
.stat-grid{grid-template-columns:repeat(2,1fr)}.nav-mid{display:none}
.sec-head{margin-bottom:30px;flex-wrap:wrap;gap:8px}
.sec-note{margin-left:0;flex-basis:100%}
.month-select{margin-left:0}
.lib-card-title{font-size:13px}.lib-card-sub{font-size:10px}
.lib-controls{margin-bottom:24px}
.filter-group label{padding:8px 13px;font-size:10px;margin-right:6px}
.hero-card-title{font-size:19px}}
@media(max-width:430px){.card-grid{gap:10px}
.cov-rating{font-size:10px;padding:4px 7px}
.cov-wip{font-size:8px;padding:4px 6px}}
@media(prefers-reduced-motion:reduce){.reveal{transform:none}
*{animation:none!important}}
"""

PAGE_JS = """
const io=new IntersectionObserver(es=>es.forEach(e=>{
if(e.isIntersecting)e.target.classList.add('vis')}),{threshold:.1});
document.querySelectorAll('.reveal').forEach(el=>io.observe(el));
const rm=matchMedia('(prefers-reduced-motion:reduce)').matches;
const cio=new IntersectionObserver(es=>es.forEach(e=>{
if(!e.isIntersecting)return;const el=e.target,t=parseFloat(el.dataset.count);
cio.unobserve(el);if(rm||isNaN(t)){return}
const dec=(el.dataset.count.indexOf('.')>-1);let s=0,st=performance.now();
(function tick(n){let p=Math.min((n-st)/900,1);
let v=t*(1-Math.pow(1-p,3));
el.textContent=dec?v.toFixed(1):Math.round(v);
if(p<1)requestAnimationFrame(tick)})(st)}),{threshold:.6});
document.querySelectorAll('.stat-num[data-count]').forEach(el=>cio.observe(el));
const grid=document.getElementById('all-grid');
document.querySelectorAll('.sort-btn').forEach(b=>b.addEventListener('click',()=>{
document.querySelectorAll('.sort-btn').forEach(x=>x.classList.remove('active'));
b.classList.add('active');const k=b.dataset.sort;
const cards=[...grid.children];cards.sort((a,z)=>{
if(k==='rating')return (+z.dataset.rating)-(+a.dataset.rating);
return (z.dataset.date||'').localeCompare(a.dataset.date||'')});
cards.forEach(c=>grid.appendChild(c))}));
(function(){
const dataEl=document.getElementById('month-data');
if(!dataEl)return;
const meta=JSON.parse(dataEl.textContent);
const cur=meta._current;
const keys=Object.keys(meta).filter(k=>k!=='_current');
const panels=document.querySelectorAll('.month-panel');
const sel=document.getElementById('month-select');
const heading=document.getElementById('month-heading');
const note=document.getElementById('month-note');
function show(key){
if(!meta[key]||key==='_current')key=keys[0];
panels.forEach(p=>p.classList.toggle('active',p.dataset.month===key));
heading.textContent=meta[key].label;
note.textContent=meta[key].note;
sel.value=key;
history.replaceState(null,'','#month='+key)}
sel.addEventListener('change',()=>show(sel.value));
const hashMatch=location.hash.match(/month=([\\d-]+)/);
const initial=(hashMatch&&meta[hashMatch[1]])?hashMatch[1]:(meta[cur]?cur:keys[0]);
show(initial)})();
"""

# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print("📚 Building media library...")
    overrides = load_overrides()

    in_prog, done = [], []
    for db, cat in ((DB_GAMES, "games"), (DB_MOVIES_TV, "tv"), (DB_BOOKS, "books")):
        for p in get_in_progress(db):
            it = shape(p, cat)
            it["status"] = "wip"
            in_prog.append(it)
        for p in get_done_all(db):
            it = shape(p, cat)
            it["status"] = "done"
            done.append(it)

    # Only this year's completions count anywhere on the page; in-progress
    # items are always kept regardless of when they started.
    before = len(done)
    done = [x for x in done if x["year"] == YEAR]
    print(f"  in-progress: {len(in_prog)} | done {YEAR}: {len(done)} "
          f"(dropped {before - len(done)} from earlier years)")

    targets = in_prog + done
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(lambda it: resolve_cover(it, overrides), targets))
    for it, rel in zip(targets, results):
        it["cover"] = rel

    hits = sum(1 for it in targets if it["cover"])
    print(f"  covers: {hits}/{len(targets)} resolved")

    OUT_FILE.write_text(build_page(in_prog, done), encoding="utf-8")
    print(f"✅ Wrote {OUT_FILE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
