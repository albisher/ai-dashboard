#!/usr/bin/env python3
"""
AI Radar generator.

Reads scripts/portfolio.json, checks each repo's GitHub metadata, asks Claude
(via the Anthropic API's built-in web_search tool) for this week's AI news,
asks Claude to map that news onto the portfolio, and renders docs/index.html.

Env vars:
  ANTHROPIC_API_KEY  - required
  RADAR_GH_TOKEN     - optional; a read-only PAT for checking private repos
                       beyond the one this workflow runs in. Falls back to
                       the default GITHUB_TOKEN (public repos only) if unset.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from anthropic import Anthropic

ROOT = Path(__file__).resolve().parent.parent
PORTFOLIO_FILE = ROOT / "scripts" / "portfolio.json"
OUTPUT_FILE = ROOT / "docs" / "index.html"
ARCHIVE_DIR = ROOT / "docs" / "archive"

GH_TOKEN = os.environ.get("RADAR_GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]  # hard fail if missing — nothing works without it
MODEL = "claude-sonnet-4-6"


def gh_headers():
    h = {"Accept": "application/vnd.github+json"}
    if GH_TOKEN:
        h["Authorization"] = f"Bearer {GH_TOKEN}"
    return h


def fetch_repo_metadata(owner: str, repo: str) -> dict:
    url = f"https://api.github.com/repos/{owner}/{repo}"
    r = requests.get(url, headers=gh_headers(), timeout=15)
    if r.status_code != 200:
        return {"name": repo, "error": f"HTTP {r.status_code}"}
    d = r.json()
    pushed = datetime.fromisoformat(d["pushed_at"].replace("Z", "+00:00"))
    days = (datetime.now(timezone.utc) - pushed).days
    status = "active" if days <= 30 else ("quiet" if days <= 120 else "dormant")
    return {
        "name": d["full_name"],
        "description": d.get("description") or "",
        "language": d.get("language") or "",
        "private": d.get("private", False),
        "days_since_push": days,
        "status": status,
    }


def build_portfolio() -> list:
    cfg = json.loads(PORTFOLIO_FILE.read_text())
    owner = cfg["owner"]
    return [fetch_repo_metadata(owner, name) for name in cfg["repos"]]


NEWS_SCHEMA_PROMPT = """
Search the web for the most significant AI industry developments from the past 7 days
(new model releases, pricing changes, notable open-source repos, regulation, agent/tooling trends).

Respond with ONLY a JSON object, no prose, no markdown fences, matching this schema exactly:

{
  "week_label": "string, e.g. 'Week of Jul 6-16, 2026'",
  "stats": [
    {"value": "short string like '45%'", "label": "one sentence, what it means"}
  ],  // exactly 4 items
  "category_signal": {
    "Frontier Models": 0, "AI Agents": 0, "Image / Video Gen": 0,
    "Open-Weight & Local": 0, "Regulation & Safety": 0, "Dev Tooling / MCP": 0
  },  // each a 0-10 integer, relative coverage this week
  "trending_repos": [
    {"repo": "org/name", "stars": "string", "why": "one sentence"}
  ],  // 4-6 items, real GitHub repos trending this week
  "top_stories": [
    {"title": "one sentence headline", "detail": "1-2 sentence explanation"}
  ]  // exactly 5 items, ranked most important first
}

Paraphrase everything in your own words. Do not quote sources verbatim beyond a few words.
"""

RELEVANCE_SCHEMA_PROMPT = """
Here is a portfolio of GitHub repos (JSON below) and a set of this week's AI news stories (JSON below).

PORTFOLIO:
{portfolio_json}

NEWS:
{news_json}

For each news story that plausibly matters to one or more of these repos (based on their name,
description, and language — you don't have their source code), map it. Skip stories that don't
apply to anything in the portfolio. Respond with ONLY a JSON array, no prose:

[
  {{"story": "short restatement of the news item", "repos": ["repo-name", "..."], "why": "one sentence"}}
]
"""


def call_claude_json(client: Anthropic, prompt: str, use_web_search: bool) -> object:
    kwargs = dict(
        model=MODEL,
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    if use_web_search:
        kwargs["tools"] = [{"type": "web_search_20250305", "name": "web_search"}]
    resp = client.messages.create(**kwargs)
    text_parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    raw = "\n".join(text_parts).strip()
    # Strip accidental markdown fences
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        if raw.lower().startswith("json"):
            raw = raw[4:]
    return json.loads(raw)


def render_html(news: dict, portfolio: list, relevance: list) -> str:
    portfolio_rows = "\n".join(
        f"""<tr>
              <td class="repo">{r['name'].split('/')[-1]}</td>
              <td class="days">{r.get('days_since_push', '?')}d</td>
              <td><span class="badge {r.get('status','dormant')}">{r.get('status','?')}</span></td>
              <td class="note">{r.get('description','') or r.get('language','')}</td>
            </tr>"""
        for r in portfolio if "error" not in r
    )
    stat_cards = "\n".join(
        f"""<div class="stat c{i%4+1}"><div class="num">{s['value']}</div><div class="label">{s['label']}</div></div>"""
        for i, s in enumerate(news["stats"])
    )
    repo_rows = "\n".join(
        f"""<tr><td class="repo">{t['repo']}</td><td class="stars">{t['stars']}</td><td class="note">{t['why']}</td></tr>"""
        for t in news["trending_repos"]
    )
    relevance_rows = "\n".join(
        f"""<tr><td class="note">{item['story']}</td><td class="repo">{', '.join(item['repos'])}</td><td class="note">{item['why']}</td></tr>"""
        for item in relevance
    )
    top_stories = "\n".join(
        f"""<div class="arow"><div class="arank">{i+1:02d}</div>
              <div><b>{s['title']}</b><p>{s['detail']}</p></div></div>"""
        for i, s in enumerate(news["top_stories"])
    )
    labels = list(news["category_signal"].keys())
    values = list(news["category_signal"].values())

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>AI Radar — {news['week_label']}</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.4/chart.umd.min.js"></script>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@600;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap');
  :root{{--bg:#0C1019;--panel:#141A28;--line:#232B3D;--text:#E8EBF3;--muted:#818AA3;--cyan:#5EEAD4;--amber:#FFB86B;--red:#FF7A6E;--violet:#9C8CFF;}}
  *{{box-sizing:border-box;}}
  body{{margin:0;background:var(--bg);color:var(--text);font-family:'Inter',sans-serif;padding:28px 32px 60px;}}
  h1{{font-family:'Space Grotesk',sans-serif;font-size:26px;margin:0;}}
  .sub{{color:var(--muted);font-size:13px;margin-top:4px;}}
  .stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:24px 0;}}
  .stat{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px 20px;}}
  .stat .num{{font-family:'Space Grotesk',sans-serif;font-size:28px;font-weight:700;}}
  .stat .label{{color:var(--muted);font-size:12.5px;margin-top:8px;}}
  .c1 .num{{color:var(--cyan);}} .c2 .num{{color:var(--amber);}} .c3 .num{{color:var(--violet);}} .c4 .num{{color:var(--red);}}
  .panel{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:20px 22px;margin-bottom:16px;}}
  .panel h2{{font-family:'Space Grotesk',sans-serif;font-size:15px;margin:0 0 12px;}}
  table{{width:100%;border-collapse:collapse;font-size:13px;}}
  th{{text-align:left;color:var(--muted);font-size:11px;text-transform:uppercase;padding-bottom:8px;border-bottom:1px solid var(--line);}}
  td{{padding:9px 8px 9px 0;border-bottom:1px solid var(--line);vertical-align:top;}}
  td.repo{{font-family:'JetBrains Mono',monospace;color:var(--cyan);white-space:nowrap;}}
  td.note{{color:var(--muted);}}
  td.stars,td.days{{font-family:'JetBrains Mono',monospace;color:var(--amber);white-space:nowrap;}}
  .badge{{font-size:10.5px;font-family:'JetBrains Mono',monospace;padding:2px 7px;border-radius:10px;border:1px solid;}}
  .badge.active{{color:#8CE99A;border-color:#8CE99A66;}} .badge.quiet{{color:var(--amber);border-color:#FFB86B66;}} .badge.dormant{{color:var(--muted);border-color:var(--line);}}
  .attention{{background:#101521;border:1px solid var(--line);border-radius:14px;padding:22px 24px;}}
  .arow{{display:flex;gap:16px;padding:12px 0;border-top:1px solid var(--line);}}
  .arow:first-of-type{{border-top:none;padding-top:0;}}
  .arank{{font-family:'JetBrains Mono',monospace;color:var(--muted);font-size:20px;font-weight:600;width:26px;}}
  footer{{color:var(--muted);font-size:11.5px;text-align:center;margin-top:16px;}}
</style>
</head>
<body>
<h1>AI Radar</h1>
<div class="sub">{news['week_label']} — auto-generated, portfolio: albisher</div>

<div class="stats">{stat_cards}</div>

<div class="panel">
  <h2>Signal by category (0–10)</h2>
  <canvas id="radarChart" style="max-height:240px;"></canvas>
</div>

<div class="panel">
  <h2>Your repo portfolio</h2>
  <table><tr><th>Repo</th><th>Last push</th><th>Status</th><th>What it is</th></tr>{portfolio_rows}</table>
</div>

<div class="panel">
  <h2>Trending on GitHub this week</h2>
  <table><tr><th>Repo</th><th>Stars</th><th>Why</th></tr>{repo_rows}</table>
</div>

<div class="panel">
  <h2>This week's news → your repos</h2>
  <table><tr><th>Development</th><th>Repo(s)</th><th>Why</th></tr>{relevance_rows}</table>
</div>

<div class="attention">
  <h2 style="color:var(--amber);font-family:'Space Grotesk',sans-serif;">⚡ Top 5 this week</h2>
  {top_stories}
</div>

<footer>Generated automatically every Monday via GitHub Actions. Not reviewed by a human before publishing.</footer>

<script>
new Chart(document.getElementById('radarChart'), {{
  type: 'radar',
  data: {{ labels: {json.dumps(labels)}, datasets: [{{ label:'This week', data: {json.dumps(values)},
    backgroundColor:'rgba(94,234,212,0.15)', borderColor:'#5EEAD4', pointBackgroundColor:'#5EEAD4', borderWidth:2 }}] }},
  options: {{ responsive:true, plugins:{{legend:{{display:false}}}},
    scales:{{ r:{{ min:0,max:10, angleLines:{{color:'rgba(255,255,255,0.06)'}}, grid:{{color:'rgba(255,255,255,0.06)'}},
      pointLabels:{{color:'#E8EBF3',font:{{size:11}}}}, ticks:{{display:false}} }} }} }}
}});
</script>
</body>
</html>"""


def main():
    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    print("Fetching portfolio metadata from GitHub...", file=sys.stderr)
    portfolio = build_portfolio()

    print("Asking Claude for this week's AI news (with web search)...", file=sys.stderr)
    news = call_claude_json(client, NEWS_SCHEMA_PROMPT, use_web_search=True)

    print("Asking Claude to map news onto the portfolio...", file=sys.stderr)
    relevance_prompt = RELEVANCE_SCHEMA_PROMPT.format(
        portfolio_json=json.dumps(portfolio, indent=2),
        news_json=json.dumps(news, indent=2),
    )
    relevance = call_claude_json(client, relevance_prompt, use_web_search=False)

    html = render_html(news, portfolio, relevance)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(html)

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    dated = ARCHIVE_DIR / f"{datetime.now(timezone.utc).date().isoformat()}.html"
    dated.write_text(html)

    print(f"Wrote {OUTPUT_FILE} and {dated}", file=sys.stderr)


if __name__ == "__main__":
    main()
