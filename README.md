# avi.ai — Personal Website

My corner of the internet. Dark, cinematic, and updated automatically so I don't have to.

## What's here

**`index.html`** — The whole site. One file. No frameworks, no build steps, no excuses.

**`scripts/update_now.py`** — Fetches my Notion databases every night and updates the *Right Now* section with whatever I'm currently playing, watching, and reading. Ratings included. Japanese learning card not included (I gave up).

**`.github/workflows/daily-update.yml`** — GitHub Actions cron job that runs at 1 AM IST. While I sleep, the robots work.

## Stack

- Pure HTML/CSS/JS
- Notion API for the live media section
- GitHub Actions for the nightly update
- Vercel for hosting
- Claude for building all of this

## Sections

- **About** — who I am, in case you forgot
- **Right Now** — auto-updated daily from Notion
- **Interests** — things I care about more than I should
- **Projects** — side quests, some shipped, some still in my head
- **Writing** — Substack essays, published when something won't leave me alone
- **Favorite AI** — Claude (obviously)
- **Places** — where I've been, where I'm going
- **Contact** — if you want to say hi

## Setup (if you're forking this)

Add a `NOTION_TOKEN` secret to your GitHub repo and update the database IDs in `scripts/update_now.py` with your own Notion databases. Everything else just works.

---

*Built with Claude. Hosted on Vercel. Fuelled by chai and Sekiro deaths.*
