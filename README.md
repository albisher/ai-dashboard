# AI Radar — automated weekly pipeline

Generates a weekly AI-news dashboard, cross-referenced against your GitHub
portfolio, and publishes it via GitHub Pages. Runs every Monday 06:00 UTC
via GitHub Actions — no manual step once set up.

## Setup (one-time)

1. **Create the repo.**
   `git init`, add these files, `git remote add origin <your ai-dashboard repo>`, push.

2. **Add secrets** — repo Settings → Secrets and variables → Actions:
   - `ANTHROPIC_API_KEY` — your Anthropic API key from console.anthropic.com.
     Billed to you at API rates (a weekly run is a few cents).
   - `RADAR_GH_TOKEN` — a fine-grained GitHub PAT, **read-only**, scoped to
     "Contents: Read-only" + "Metadata: Read-only" on the repos listed in
     `scripts/portfolio.json`. Give it a real expiration (30–90 days) and
     rotate it when it expires — GitHub Actions will just start failing
     with a clear auth error, which is your cue to refresh it.

3. **Enable GitHub Pages** — repo Settings → Pages → Source: "Deploy from a
   branch" → branch `main`, folder `/docs`. Save.

4. **Edit `scripts/portfolio.json`** to match whichever repos you actually
   want checked — add or remove freely.

5. **First run:** go to the Actions tab → "AI Radar Weekly" → "Run workflow"
   to trigger it manually and confirm everything works before waiting for
   the Monday schedule.

## What it does each run

1. Reads `scripts/portfolio.json`, pulls metadata (last push, description,
   language) for each listed repo via the GitHub API.
2. Asks Claude (Anthropic API, with the API's own web-search tool enabled)
   for a structured summary of the week's AI news.
3. Asks Claude to map those news items onto your specific portfolio.
4. Renders `docs/index.html` (plus a dated copy under `docs/archive/`) and
   commits it back to the repo.

## Notes and limits

- This is **read-only against your other repos** — it only calls GitHub's
  read endpoints, never pushes to any repo except this one (to publish the
  dashboard itself).
- Nothing here is reviewed by a human before publishing — it's an
  unattended weekly job. Treat the output as a starting point, not a final
  verdict, especially the repo-relevance mapping.
- If a Monday run fails (bad API key, expired PAT, JSON parsing hiccup),
  check the Actions tab logs — the script fails loudly rather than
  publishing a broken page.
- Cost scales with how much you ask it to do. Two Claude calls per week is
  cheap; don't lower the cron interval to daily without checking your API
  budget.
