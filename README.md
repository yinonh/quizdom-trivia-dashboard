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
- **Refresh now** on the page calls the Supabase Edge Function
  `refresh-dashboard`, which dispatches that same workflow. The GitHub token
  stays in a Supabase secret. See [Refresh now](#refresh-now).
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
`cron:` line in the workflow to adjust. You can also hit **Run workflow** anytime,
or use **Refresh now** on the dashboard after the function below is deployed.

## Refresh now

The header button POSTs to:

`https://uhfsfedwteeoxsvixvtr.supabase.co/functions/v1/refresh-dashboard`

That Edge Function (`supabase/functions/refresh-dashboard`) does one thing: it
dispatches `.github/workflows/refresh-dashboard.yml` on the `main` branch. The
repo, workflow, and ref are hardcoded. The request body is ignored. GitHub Pages
then serves the HTML the workflow generates, usually within 1–2 minutes. The
button polls the page and reloads when the snapshot timestamp moves.

Nothing here is deployed by this repo. Run the steps below yourself after review.
Do not commit the token.

### Why JWT verification is off

The dashboard is a public static page and has no signed-in user. It does not
embed a Supabase key. Turning JWT verification on would only check the public
anon or publishable key, which anyone can copy out of a page, and a service-role
key must never be put in the client. Deploy with `--no-verify-jwt`.
`supabase/config.toml` sets `verify_jwt = false` for this function as well.

What actually limits the button:

- The function reads `DASHBOARD_GH_TOKEN` from Supabase secrets and never returns it.
- It can dispatch only `yinonh/quizdom-trivia-dashboard` workflow `refresh-dashboard.yml` on `main`.
- Browsers: CORS allows only `https://yinonh.github.io`, and only `POST` plus the `OPTIONS` preflight. The `Origin` header must be that exact origin.
- If the latest runs of that workflow include one that is queued, in progress, or otherwise unfinished, or one created in the last 3 minutes, the function returns `429` and does not dispatch.
- Errors are `{ "ok": false, "message": "..." }`. GitHub response bodies and headers are not forwarded.

CORS is not authentication. A non-browser client can still call the URL. The
cooldown and the single hardcoded workflow are what keep that from becoming an
open "run anything" button. Anyone who can open the public dashboard can start
a refresh. That is the same audience as the button.

### 1. Create a fine-grained personal access token

GitHub → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.

- Resource owner: the account that owns `yinonh/quizdom-trivia-dashboard`
- Repository access: **Only select repositories** → `quizdom-trivia-dashboard`
- Repository permissions: **Actions: Read and write**. Leave every other repository permission, and every account permission, at **No access**
- Expiration: pick a date you will rotate. When you rotate, set the Supabase secret again. Do not commit the new token

Copy the token once (`github_pat_...`). It needs Actions read (to check the latest run) and Actions write (to dispatch).

### 2. Set the Supabase secret

Install the [Supabase CLI](https://supabase.com/docs/guides/local-development/cli/getting-started), then from this repo:

```bash
supabase login
supabase link --project-ref uhfsfedwteeoxsvixvtr
```

`link` asks for the project database password. Then put the token in a file that is not committed (`*.env` is gitignored here) and push only that secret:

```bash
umask 077
printf 'DASHBOARD_GH_TOKEN=%s\n' 'github_pat_paste_the_token_here' > /tmp/dashboard-gh-token.env
supabase secrets set --env-file /tmp/dashboard-gh-token.env
rm -f /tmp/dashboard-gh-token.env
```

`DASHBOARD_GH_TOKEN` must not start with `SUPABASE_` (that prefix is reserved). The function reads it with `Deno.env.get("DASHBOARD_GH_TOKEN")`. A new secret is visible to deployed functions without a redeploy. Confirm the name is stored, not the value:

```bash
supabase secrets list
```

### 3. Deploy the function

```bash
supabase functions deploy refresh-dashboard --no-verify-jwt --project-ref uhfsfedwteeoxsvixvtr
```

Pass `--no-verify-jwt` even though `supabase/config.toml` already has `verify_jwt = false`, so a deploy that is not reading this config cannot turn verification back on. Do not deploy any other function from this folder.

The page calls:

`https://uhfsfedwteeoxsvixvtr.supabase.co/functions/v1/refresh-dashboard`

### 4. Check it, then publish the new page

```bash
curl -i -X POST 'https://uhfsfedwteeoxsvixvtr.supabase.co/functions/v1/refresh-dashboard' \
  -H 'Origin: https://yinonh.github.io' \
  -H 'Content-Type: application/json' \
  --data '{}'
```

A working call returns HTTP 200 and `{"ok":true,"message":"Refresh started. The page will update in about 1–2 minutes."}`. Actions → **Refresh trivia dashboard** shows a new `workflow_dispatch` run. A second call within 3 minutes returns HTTP 429 with `{"ok":false,"message":"..."}`.

GitHub Pages serves the HTML that workflow generates, not the file committed on the branch. After this change is on `main`, run the workflow once from the Actions tab so the live button points at the function. Until that run finishes, the site still has the old button.

## Notes
- Pages serves a static file. The old `server.py` `/refresh` route is gone; **Refresh now** uses the Edge Function above instead.
- Tables queried: `questions_he`, `questions_en`, `raw_questions_he`,
  `questions_raw_en`, `question_suggestions`, `trivia_categories`,
  `trivia_sessions`.
