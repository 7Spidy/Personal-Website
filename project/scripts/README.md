# Auto-Update Setup

The `update_now.py` script fetches your Notion Media Consumption databases every night at **1 AM IST** and patches the **Now** section of `index.html` with fresh In Progress and recently completed items.

This mirrors your existing **Notion Daily Quote** project — same GitHub Actions + Python + Notion API pattern.

---

## Setup (5 minutes)

### 1. Push this project to GitHub
```bash
git init
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git add .
git commit -m "Initial commit"
git push -u origin main
```

### 2. Add your Notion token as a GitHub Secret
1. Go to your repo → **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret**
3. Name: `NOTION_TOKEN`
4. Value: your Notion integration token (the one from your Personal AI Agent page)

> Your token starts with `ntn_...` — find it in your Notion integrations settings.

### 3. Make sure your Notion integration has access
Share the **Life OS 2026** root page with your integration (same as your Notion Daily Quote project).

### 4. Verify the markers are in index.html
The script patches HTML between these two comment markers:
```html
<!-- NOW-SECTION-START -->
  ...now section content...
<!-- NOW-SECTION-END -->
```
These are already in your `index.html`.

---

## Manual trigger
Go to **GitHub Actions** → **Daily Site Update** → **Run workflow**

---

## Schedule
- Runs at **19:30 UTC** = **1:00 AM IST** every day
- Only commits if content actually changed
- Commit message includes IST timestamp

---

## How it works
1. Queries your 3 Notion databases (Movies/TV, Books, Games)
2. Filters for **In progress** items → shown as active cards
3. Fetches recent **Done** items → shown in the smaller "Recently Completed" list
4. Regenerates the Now section HTML with fresh data
5. Commits the updated `index.html` back to the repo

If you're hosting via **GitHub Pages**, the site updates automatically after each commit.
