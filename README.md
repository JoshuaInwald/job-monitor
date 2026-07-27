# Job Monitor

Automated Python pipeline that polls 35 company career boards (Greenhouse, Lever, Ashby, and Workday APIs) plus USAJobs, Adzuna, and the 80,000 Hours job board, scores postings by weighted keyword relevance, and delivers a daily digest by email via GitHub Actions.

**Stack:** Python (stdlib `requests`/`urllib` only, no scraping framework), GitHub Actions (cron scheduling + state persistence), REST APIs, YAML-driven configuration.

**How it works:** a scheduled GitHub Actions workflow runs `monitor.py` daily. Each source has its own fetcher function (`fetch_greenhouse`, `fetch_lever`, `fetch_ashby`, `fetch_workday`, plus `fetch_usajobs`/`fetch_adzuna`/`fetch_80k`); a source failing (e.g. a company changes its board) logs an error without breaking the others. Postings are scored against a weighted keyword config (title-based term weights, location boosts, hard excludes), diffed against persisted state (`seen_jobs.json`, `open_matches.json`, `history.jsonl`) to find new/closed matches, and — if there's anything new — opened as a GitHub Issue, which GitHub emails automatically. No server, no scraping infrastructure, no cost.

**How to run:** fork/clone, edit `config.yml` (companies + keyword weights), add repo secrets for `USAJOBS_KEY`/`ADZUNA_APP_KEY` (optional), then either let the scheduled workflow run or trigger it manually from the Actions tab. See the setup guide below for a no-code walkthrough.

---

## Setup — click by click (~10 minutes)

### Step 1: Create the repo
1. Log in at github.com (make a free account if needed).
2. Click the **green "New"** button (left side of homepage), or go to
   github.com/new.
3. Repository name: `job-monitor`. Select **Private**. Click
   **Create repository**.

### Step 2: Upload the files
1. On your new empty repo page, click the link **"uploading an existing
   file"**.
2. Drag in: `monitor.py`, `config.yml`, `claude_scoring_prompt.md`,
   `README.md`. Click **Commit changes**.
3. The workflow file must live in a folder, which drag-and-drop can't create.
   So: click **Add file → Create new file**. In the filename box type
   exactly: `.github/workflows/monitor.yml` (typing the slashes creates the
   folders). Paste in the full contents of `monitor.yml`. Click
   **Commit changes**.

### Step 3: Give the script permission to post
1. In your repo, click **Settings** (top tab) → **Actions** (left sidebar) →
   **General**.
2. Scroll to **Workflow permissions**. Select **"Read and write
   permissions"**. Click **Save**.

### Step 4: First run
1. Click the **Actions** tab (top of repo). If it asks you to enable
   workflows, click enable.
2. Click **"Job monitor"** in the left list → click the **"Run workflow"**
   dropdown (right side) → green **Run workflow** button.
3. Wait ~1–2 minutes. Refresh. Click the run to see logs if curious.
4. Check the **Issues** tab: you should see a "baseline snapshot" issue
   listing all currently-open matches — that's your apply-now list. You'll
   also get it by email.

That's it. It now runs daily on its own. No new matches = no issue = no email.

### Step 5 (optional, ~5 min each): free API keys for two extra sources
These sources are skipped harmlessly if you don't do this.

**USAJobs (federal roles):**
1. Request a free key: https://developer.usajobs.gov/apirequest (instant).
2. In your repo: **Settings → Secrets and variables → Actions → New
   repository secret**.
3. Create secret named `USAJOBS_EMAIL` with your email; another named
   `USAJOBS_KEY` with the key they sent.

**Adzuna (aggregator across many job boards):**
1. Free key: https://developer.adzuna.com (sign up → create app).
2. Add secrets `ADZUNA_APP_ID` and `ADZUNA_APP_KEY` the same way.

---

## Customizing (all in `config.yml` — edit on GitHub by clicking the file →
pencil icon → Commit changes)

- **Companies**: add/remove lines. Slug = the ID in the careers-page URL
  (`boards.greenhouse.io/<slug>` etc. — instructions in the file).
- **Keywords & weights**: the `scoring:` section. Higher weight = more
  important. Negative = demote. `hard_exclude` = never show.
- **Thresholds**: `core_threshold` / `adjacent_threshold` control how picky
  the two digest sections are. Too much noise → raise them; too quiet →
  lower them.
- **Schedule**: the `cron:` line in `.github/workflows/monitor.yml` (UTC).

## Reading the digest

- `[12]` before each job = its keyword score. **Core** = strong matches;
  **Adjacent** = broader "wouldn't suck" net.
- "Likely closed/filled" = previously-matched postings that vanished.
- Sundays include a weekly roll-up of everything found that week.
- For a smarter pass: open `claude_scoring_prompt.md`, paste it + the digest
  into claude.ai (uses your existing Pro plan, costs nothing extra).

## When something breaks

Each source is independent — if Workday or 80k Hours changes their site, that
source logs an error and the rest keep working. Errors appear in a collapsed
"Source errors" section at the bottom of digest issues. Some starter slugs in
config.yml are guesses; after the first run, delete or fix the ones that
error. If a fetcher stays broken, paste the error into Claude and ask for a
fix.
