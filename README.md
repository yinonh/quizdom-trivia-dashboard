# Trivia Dashboard — free hosting via GitHub Actions + Pages

A static dashboard of your Quizdom trivia DB progress (question counts per
language, refiner progress, recent activity, suggestions). It regenerates itself
from live Supabase data on a schedule and publishes to GitHub Pages — 100% free,
always-on, no server to keep alive.

## How it works

- `update_trivia_dashboard.py` queries your Quizdom Supabase (project
  `uhfsfedwteeoxsvixvtr`) and writes a self-contained `index.html`.
- `.github/workflows/refresh-dashboard.yml` runs that script on a schedule
  (and on demand), then deploys `index.html` to GitHub Pages.
- Your Supabase key lives as an encrypted **GitHub Secret** — never in the page,
  never in the repo.

## One-time setup

### 1. Test locally first (optional but recommended)
So you catch any issue before pushing. In this folder:
```bash
export SUPABASE_URL="https://uhfsfedwteeoxsvixvtr.supabase.co"
export SUPABASE_KEY="<your alfred secret key>"
python3 update_trivia_dashboard.py
open index.html   # should show the dashboard with real numbers
```
(Unset the vars after: `unset SUPABASE_KEY`.)

### 2. Create a GitHub repo and push this folder
```bash
cd ~/Documents/grok-agents/trivia-dashboard
git init && git add . && git commit -m "Trivia dashboard"
# create a repo on github.com (e.g. quizdom-trivia-dashboard), then:
git remote add origin https://github.com/<you>/quizdom-trivia-dashboard.git
git branch -M main && git push -u origin main
```
⚠️ Do NOT commit any file containing the real key. This folder has none — keep it
that way. The key goes in as a Secret (next step), not in the repo.

### 3. Add the Supabase secrets
Repo → **Settings → Secrets and variables → Actions → New repository secret**:
- `SUPABASE_URL` = `https://uhfsfedwteeoxsvixvtr.supabase.co`
- `SUPABASE_KEY` = your `alfred` secret key from Supabase

### 4. Enable Pages
Repo → **Settings → Pages → Source: GitHub Actions**.

### 5. Run it
Repo → **Actions → "Refresh trivia dashboard" → Run workflow**. When it finishes,
your dashboard is live at `https://<you>.github.io/<repo>/`.

## Schedule
The workflow refreshes daily at 05:00 UTC (~07:00–08:00 Israel). Change the
`cron:` line in the workflow to adjust. You can also hit **Run workflow** anytime.

## Notes
- The old `server.py` (live local server with a /refresh button) is not needed
  for this hosting model — Pages serves the generated static file. Kept out of
  this folder on purpose.
- Tables queried: `questions_he`, `questions_en`, `raw_questions_he`,
  `questions_raw_en`, `question_suggestions`, `trivia_categories`,
  `trivia_sessions`.
