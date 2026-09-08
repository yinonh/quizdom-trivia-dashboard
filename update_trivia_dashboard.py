#!/usr/bin/env python3
"""Generate Alfred's trivia DB dashboard from live Supabase counts."""
from __future__ import annotations

import collections
import datetime as dt
import html
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CREDS_PATH = ROOT / "memory" / "supabase-creds.json"
OUT_PATH = Path(__file__).resolve().parent / "index.html"

TABLES = [
    "question_suggestions",
    "questions_en",
    "questions_he",
    "questions_raw_en",
    "raw_questions_he",
    "trivia_categories",
    "trivia_sessions",
]

SESSION_WINDOW_DAYS = 3


def supabase_client():
    # Read credentials from environment variables (GitHub Actions secrets),
    # falling back to the local creds file for local runs if it exists.
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY")
    if not (url and key) and CREDS_PATH.exists():
        creds = json.loads(CREDS_PATH.read_text())
        url = url or creds.get("url")
        key = key or creds.get("service_key") or creds.get("key")
    if not (url and key):
        raise RuntimeError(
            "Missing Supabase credentials. Set SUPABASE_URL and SUPABASE_KEY "
            "environment variables (or provide memory/supabase-creds.json)."
        )
    return url, {"apikey": key, "Authorization": f"Bearer {key}"}


def count_table(base: str, headers: dict[str, str], table: str) -> int:
    req = urllib.request.Request(
        f"{base}/rest/v1/{table}?select=*&limit=1",
        headers={**headers, "Prefer": "count=exact"},
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        return int(res.headers["content-range"].split("/")[-1])


def fetch_all(base: str, headers: dict[str, str], table: str, select: str) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    page_size = 1000
    encoded = urllib.parse.quote(select, safe=",:*()")
    while True:
        req = urllib.request.Request(
            f"{base}/rest/v1/{table}?select={encoded}&offset={offset}&limit={page_size}",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=30) as res:
            chunk = json.loads(res.read() or b"[]")
        rows.extend(chunk)
        if len(chunk) < page_size:
            break
        offset += page_size
    return rows


def pct(done: int, total: int) -> float:
    return 0.0 if not total else done / total * 100


def fmt(n: int) -> str:
    return f"{n:,}"


def parse_ts(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    except ValueError:
        return None


def day_label(day: dt.date) -> str:
    return day.strftime("%a %d")


def age(value: dt.datetime | None, now: dt.datetime) -> str:
    if value is None:
        return "not tracked"
    delta = max(dt.timedelta(), now - value)
    if delta.days:
        return f"{delta.days}d ago"
    hours = delta.seconds // 3600
    if hours:
        return f"{hours}h ago"
    minutes = delta.seconds // 60
    return f"{minutes}m ago"


def difficulty_pills(items: dict[str, int]) -> str:
    order = ["easy", "medium", "hard", "unknown"]
    labels = {"easy": "Easy", "medium": "Medium", "hard": "Hard", "unknown": "Unknown"}
    return "".join(
        f'<span class="tag">{html.escape(labels.get(k, k.title()))}: <b>{fmt(v)}</b></span>'
        for k, v in sorted(items.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else 99)
    )


def category_rows(rows: list[dict], total: int, lang: str, limit: int = 10) -> str:
    max_count = max((r["count"] for r in rows), default=1)
    rendered = []
    visible = rows[:limit]
    hidden = rows[limit:]
    other_count = sum(r["count"] for r in hidden)

    def render_row(r: dict, *, muted: bool = False) -> str:
        width = max(2, r["count"] / max_count * 100) if max_count else 2
        label = r["name"] if lang == "he" else r["name_en"]
        subtitle = r["name_en"] if lang == "he" else r["name"]
        fill_class = "barfill mutedfill" if muted else "barfill"
        return f"""
            <div class="barrow">
              <div class="barlabel">
                <span class="catname">{html.escape(label or f'Category {r["category_id"]}')}</span>
                <span class="catmeta">#{r['category_id']} · {html.escape(subtitle or '')}</span>
              </div>
              <div class="bartrack"><div class="{fill_class}" style="width:{width:.2f}%"></div></div>
              <div class="barvalue">{fmt(r['count'])}<small>{pct(r['count'], total):.1f}%</small></div>
            </div>
            """

    for r in visible:
        rendered.append(render_row(r))
    if other_count:
        hidden_rows = "\n".join(render_row(r, muted=True) for r in hidden)
        other_width = min(100, max(2, other_count / max_count * 100))
        rendered.append(
            f"""
            <details class="categorydetails">
              <summary>
                <span class="barlabel">
                  <span class="catname">Other categories</span>
                  <span class="catmeta">{len(hidden)} lower-volume categories · tap to expand</span>
                </span>
                <span class="bartrack"><span class="barfill mutedfill" style="width:{other_width:.2f}%"></span></span>
                <span class="barvalue">{fmt(other_count)}<small>{pct(other_count, total):.1f}%</small></span>
              </summary>
              <div class="detailrows">
                {hidden_rows}
              </div>
            </details>
            """
        )
    return "\n".join(rendered)


def session_column_chart(days: list[dt.date], new_counts: collections.Counter, active_counts: collections.Counter) -> str:
    max_count = max([1, *new_counts.values(), *active_counts.values()])
    rendered = []
    for day in days:
        new_count = new_counts.get(day, 0)
        active_count = active_counts.get(day, 0)
        new_height = max(3, new_count / max_count * 100) if new_count else 0
        active_height = max(3, active_count / max_count * 100) if active_count else 0
        rendered.append(
            f"""
            <div class="colgroup">
              <div class="columns" aria-label="{html.escape(day_label(day))}: {fmt(new_count)} new, {fmt(active_count)} active">
                <div class="col new" style="height:{new_height:.2f}%"><span>{fmt(new_count)}</span></div>
                <div class="col active" style="height:{active_height:.2f}%"><span>{fmt(active_count)}</span></div>
              </div>
              <div class="axislabel">{html.escape(day.strftime("%a"))}<small>{day.strftime("%d")}</small></div>
            </div>
            """
        )
    return f'<div class="columnchart">{"".join(rendered)}</div>'


def answer_comparison_chart(he_stats: dict[str, int], en_stats: dict[str, int]) -> str:
    he_correct = he_stats["correct_answers"]
    he_wrong = he_stats["wrong_answers"]
    en_correct = en_stats["correct_answers"]
    en_wrong = en_stats["wrong_answers"]
    max_count = max(1, he_correct, he_wrong, en_correct, en_wrong)
    items = [
        ("Hebrew", "Correct", he_correct, "correct"),
        ("Hebrew", "Wrong", he_wrong, "wrong"),
        ("English", "Correct", en_correct, "correct"),
        ("English", "Wrong", en_wrong, "wrong"),
    ]
    rows = []
    for lang, label, value, kind in items:
        rows.append(
            f"""
            <div class="comparebar">
              <span class="comparelabel">{html.escape(lang)} <small>{html.escape(label)}</small></span>
              <div class="bartrack"><div class="barfill {kind}" style="width:{max(2, value / max_count * 100):.2f}%"></div></div>
              <span class="barvalue">{fmt(value)}</span>
            </div>
            """
        )
    return "\n".join(rows)


def difficulty_mix_bars(diff_counts: dict[str, collections.Counter], totals: dict[str, int]) -> str:
    labels = {"easy": "Easy", "medium": "Medium", "hard": "Hard", "unknown": "Unknown"}
    classes = {"easy": "easyseg", "medium": "mediumseg", "hard": "hardseg", "unknown": "unknownseg"}
    rendered = []
    for table, label in [("questions_he", "Hebrew"), ("questions_en", "English")]:
        total = totals[table]
        counter = diff_counts[table]
        segments = []
        legend = []
        for key in ["easy", "medium", "hard", "unknown"]:
            count = counter.get(key, 0)
            if not count:
                continue
            segments.append(f'<span class="{classes[key]}" style="width:{pct(count, total):.3f}%"></span>')
            legend.append(f'<span class="tag">{labels[key]}: <b>{fmt(count)}</b></span>')
        rendered.append(
            f"""
            <div class="mixrow">
              <div class="split"><span>{html.escape(label)}</span><span class="value">{fmt(total)} questions</span></div>
              <div class="mixbar">{"".join(segments)}</div>
              <div class="pillrow compact">{"".join(legend)}</div>
            </div>
            """
        )
    return "\n".join(rendered)


def report_rows(rows: list[dict], lang_label: str) -> str:
    reported = [r for r in rows if (r.get("reports") or 0) > 0]
    reported.sort(key=lambda r: (-(r.get("reports") or 0), r.get("id") or 0))
    if not reported:
        return f'<p class="muted">No {html.escape(lang_label)} questions currently have report counters above zero.</p>'
    rendered = []
    for r in reported[:8]:
        rendered.append(
            f"""
            <div class="datarow">
              <span><b>#{r.get('id')}</b> · {html.escape((r.get('Question') or '')[:92])}</span>
              <span class="value">{fmt(r.get('reports') or 0)}</span>
            </div>
            """
        )
    return "\n".join(rendered)


def suggestion_rows(rows: list[dict], now: dt.datetime) -> str:
    if not rows:
        return '<p class="muted">No user suggestions have been submitted yet.</p>'
    rendered = []
    for r in rows[:6]:
        submitted = parse_ts(r.get("submitted_at"))
        rendered.append(
            f"""
            <div class="datarow">
              <span><b>#{r.get('id')}</b> · {html.escape((r.get('language') or 'unknown').upper())} · {html.escape(r.get('status') or 'unknown')}</span>
              <span class="value">{html.escape(age(submitted, now))}</span>
            </div>
            """
        )
    return "\n".join(rendered)


def donut(easy: int, medium: int, hard: int, total: int) -> str:
    # SVG donut using stroke-dasharray segments.
    if total <= 0:
        return ""
    vals = [("easy", easy, "#6ee7a8"), ("medium", medium, "#f7c76b"), ("hard", hard, "#ff8d8d")]
    circumference = 100
    offset = 25
    segs = []
    for name, val, color in vals:
        part = val / total * circumference
        segs.append(f'<circle class="seg" stroke="{color}" stroke-dasharray="{part:.3f} {circumference-part:.3f}" stroke-dashoffset="{-offset:.3f}" />')
        offset += part
    return "".join(segs)


def answer_performance_card(title: str, table_name: str, lang_stats: dict[str, int]) -> str:
    correct = lang_stats["correct_answers"]
    wrong = lang_stats["wrong_answers"]
    answered = correct + wrong
    return f"""
      <div class="card">
        <h2>{html.escape(title)} answer performance</h2>
        <div class="split"><span>Answered questions</span><span class="value">{fmt(answered)} total</span></div>
        <div class="stack"><div class="correct" style="width:{pct(correct, answered):.3f}%"></div><div class="wrong" style="width:{pct(wrong, answered):.3f}%"></div></div>
        <div class="statpair">
          <div class="mini"><span class="label">Correct answers</span><b>{fmt(correct)}</b><span class="muted">{pct(correct, answered):.1f}% correct</span></div>
          <div class="mini"><span class="label">Wrong answers</span><b>{fmt(wrong)}</b><span class="muted">{pct(wrong, answered):.1f}% wrong</span></div>
        </div>
        <div class="diffmap" style="margin-top:12px"><span class="diffitem"><span class="swatch easy"></span>Correct</span><span class="diffitem"><span class="swatch hard"></span>Wrong</span></div>
        <p class="muted">Source: <code>{html.escape(table_name)}</code> counters.</p>
      </div>
    """


def main() -> None:
    base, headers = supabase_client()
    counts = {table: count_table(base, headers, table) for table in TABLES}
    cats = fetch_all(base, headers, "trivia_categories", "id,name,name_en")
    catmap = {c["id"]: c for c in cats}
    generated_at = dt.datetime.now(dt.timezone.utc)
    today = generated_at.date()
    days = [today - dt.timedelta(days=offset) for offset in range(SESSION_WINDOW_DAYS - 1, -1, -1)]
    session_window_start = dt.datetime.combine(days[0], dt.time.min, tzinfo=dt.timezone.utc)
    previous_session_window_start = session_window_start - dt.timedelta(days=SESSION_WINDOW_DAYS)
    suggestions_window_start = generated_at - dt.timedelta(days=7)

    sessions = fetch_all(base, headers, "trivia_sessions", "token,created_at,updated_at,seen_ids_he,seen_ids_en")
    suggestions = fetch_all(base, headers, "question_suggestions", "id,language,status,submitted_at,user_id")

    cat_counts: dict[str, list[dict]] = {}
    diff_counts: dict[str, collections.Counter] = {}
    stats: dict[str, dict[str, int]] = {}
    question_rows: dict[str, list[dict]] = {}
    for table in ["questions_he", "questions_en"]:
        rows = fetch_all(base, headers, table, "id,Question,category_id,difficulty,reports,correct_count,wrong_count")
        question_rows[table] = rows
        cc = collections.Counter(r.get("category_id") for r in rows)
        dc = collections.Counter(r.get("difficulty") or "unknown" for r in rows)
        diff_counts[table] = dc
        all_category_rows = [
            {
                "category_id": c["id"],
                "name": c.get("name") or f"Category {c['id']}",
                "name_en": c.get("name_en") or f"Category {c['id']}",
                "count": cc.get(c["id"], 0),
            }
            for c in cats
        ]
        # Show every category, including zero-count categories. Non-zero categories
        # are sorted by volume; empty categories stay visible at the bottom by ID.
        cat_counts[table] = sorted(
            all_category_rows,
            key=lambda r: (r["count"] == 0, -r["count"], r["category_id"]),
        )
        stats[table] = {
            "reported_questions": sum(1 for r in rows if (r.get("reports") or 0) > 0),
            "total_reports": sum(r.get("reports") or 0 for r in rows),
            "correct_answers": sum(r.get("correct_count") or 0 for r in rows),
            "wrong_answers": sum(r.get("wrong_count") or 0 for r in rows),
        }

    he_done = counts["questions_he"]
    he_raw = counts["raw_questions_he"]
    en_done = counts["questions_en"]
    en_raw = counts["questions_raw_en"]
    total_done = he_done + en_done
    total_raw = he_raw + en_raw
    he_stats = stats["questions_he"]
    en_stats = stats["questions_en"]
    total_correct = he_stats["correct_answers"] + en_stats["correct_answers"]
    total_wrong = he_stats["wrong_answers"] + en_stats["wrong_answers"]
    total_answers = total_correct + total_wrong
    total_reports = he_stats["total_reports"] + en_stats["total_reports"]
    tracked_question_events = total_correct + total_wrong + total_reports

    created_sessions = [(s, parse_ts(s.get("created_at"))) for s in sessions]
    updated_sessions = [(s, parse_ts(s.get("updated_at"))) for s in sessions]
    new_sessions_window = [s for s, created in created_sessions if created and created >= session_window_start]
    previous_new_sessions_window = [
        s for s, created in created_sessions
        if created and previous_session_window_start <= created < session_window_start
    ]
    active_sessions_24h = [
        s for s, updated in updated_sessions
        if updated and updated >= generated_at - dt.timedelta(hours=24)
    ]
    new_by_day = collections.Counter(created.date() for _, created in created_sessions if created and created.date() in days)
    active_by_day = collections.Counter(updated.date() for _, updated in updated_sessions if updated and updated.date() in days)
    latest_session_update = max((updated for _, updated in updated_sessions if updated), default=None)
    seen_he = sum(len(s.get("seen_ids_he") or []) for s in sessions)
    seen_en = sum(len(s.get("seen_ids_en") or []) for s in sessions)
    session_delta = len(new_sessions_window) - len(previous_new_sessions_window)
    session_delta_label = (
        f"+{fmt(session_delta)}"
        if session_delta > 0
        else fmt(session_delta)
    )

    suggestions_by_status = collections.Counter((s.get("status") or "unknown") for s in suggestions)
    suggestions_by_lang = collections.Counter((s.get("language") or "unknown") for s in suggestions)
    suggestions_7d = [
        s for s in suggestions
        if (submitted := parse_ts(s.get("submitted_at"))) and submitted >= suggestions_window_start
    ]
    suggestions.sort(key=lambda s: parse_ts(s.get("submitted_at")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)

    ts = generated_at.strftime("%Y-%m-%d %H:%M UTC")
    ts_iso = generated_at.isoformat()

    html_doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Alfred Dashboard · Trivia DB</title>
  <style>
    :root {{
      color-scheme: dark;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --bg:#08111f; --card:rgba(255,255,255,.08); --line:rgba(255,255,255,.14);
      --text:#f5f8ff; --muted:#aec0d8; --blue:#8fc7ff; --green:#6ee7a8; --amber:#f7c76b; --red:#ff8d8d; --pink:#f0a3ff;
    }}
    * {{ box-sizing:border-box; }} body {{ margin:0; min-height:100vh; background:radial-gradient(circle at 12% 8%, rgba(69,139,255,.34), transparent 32%), radial-gradient(circle at 88% 0%, rgba(126,87,255,.28), transparent 30%), radial-gradient(circle at 50% 100%, rgba(47,213,153,.14), transparent 36%), var(--bg); color:var(--text); }}
    .wrap {{ width:min(1220px, calc(100vw - 28px)); margin:0 auto; padding:34px 0 46px; }}
    header,.card {{ border:1px solid var(--line); border-radius:28px; background:linear-gradient(135deg, rgba(255,255,255,.12), rgba(255,255,255,.055)); box-shadow:0 24px 90px rgba(0,0,0,.32); backdrop-filter:blur(16px); }}
    header {{ padding:clamp(24px,4vw,42px); }} .card {{ padding:20px; border-radius:22px; }}
    .eyebrow {{ color:var(--blue); font-size:13px; letter-spacing:.14em; text-transform:uppercase; font-weight:800; }}
    h1 {{ margin:12px 0 10px; font-size:clamp(38px,7vw,76px); line-height:.92; letter-spacing:-.055em; }} h2 {{ margin:0 0 14px; font-size:22px; letter-spacing:-.02em; }}
    .lead {{ max-width:850px; color:#d8e5f7; font-size:clamp(17px,2vw,21px); line-height:1.55; margin:0; }}
    .pillrow {{ display:flex; flex-wrap:wrap; gap:10px; margin-top:22px; align-items:center; }} .pill,.tag {{ border:1px solid rgba(255,255,255,.13); background:rgba(0,0,0,.18); color:#dce9fb; padding:9px 12px; border-radius:999px; font-size:14px; display:inline-block; margin:3px 6px 3px 0; }}
    .refreshbar {{ display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin-top:18px; }} .button {{ border:1px solid rgba(143,199,255,.45); background:linear-gradient(135deg, rgba(143,199,255,.22), rgba(240,163,255,.15)); color:#f5f8ff; padding:11px 15px; border-radius:999px; font-weight:800; cursor:pointer; box-shadow:0 12px 32px rgba(0,0,0,.22); }} .button:disabled {{ opacity:.58; cursor:wait; }} .statusline {{ color:var(--muted); font-size:14px; }}
    .diffmap {{ display:flex; flex-wrap:wrap; gap:8px; margin:8px 0 14px; }} .diffitem {{ display:inline-flex; align-items:center; gap:7px; border:1px solid rgba(255,255,255,.12); background:rgba(0,0,0,.16); border-radius:999px; padding:7px 10px; color:#dce9fb; font-size:13px; }} .swatch {{ width:11px; height:11px; border-radius:999px; box-shadow:0 0 14px currentColor; }} .easy {{ color:var(--green); background:var(--green); }} .medium {{ color:var(--amber); background:var(--amber); }} .hard {{ color:var(--red); background:var(--red); }}
    .grid {{ display:grid; gap:16px; margin-top:16px; }} .metrics {{ grid-template-columns:repeat(4,minmax(0,1fr)); }} .two {{ grid-template-columns:1fr 1fr; }} .wide {{ grid-template-columns:1fr; }}
    .sectionhead {{ margin-top:28px; display:flex; align-items:end; justify-content:space-between; gap:16px; }} .sectionhead h2 {{ margin:0; font-size:26px; }} .sectionhead p {{ margin:0; max-width:640px; text-align:right; }}
    .label {{ color:var(--muted); font-size:13px; text-transform:uppercase; letter-spacing:.08em; }} .num {{ font-size:clamp(34px,4vw,52px); font-weight:850; letter-spacing:-.05em; margin:5px 0 4px; }} .note,.muted {{ color:var(--muted); font-size:14px; line-height:1.5; }}
    .progressline {{ margin:14px 0 0; }} .split {{ display:grid; grid-template-columns:1fr auto; gap:12px; align-items:end; margin-bottom:8px; }} .value {{ font-weight:800; }}
    .track {{ height:13px; border-radius:999px; background:rgba(255,255,255,.12); overflow:hidden; border:1px solid rgba(255,255,255,.08); }} .fill {{ height:100%; border-radius:inherit; background:linear-gradient(90deg,var(--green),var(--blue)); }}
    .stack {{ height:18px; border-radius:999px; overflow:hidden; background:rgba(255,255,255,.10); display:flex; border:1px solid rgba(255,255,255,.08); }} .stack .correct {{ background:linear-gradient(90deg,var(--green),#9ef5c2); }} .stack .wrong {{ background:linear-gradient(90deg,var(--red),#ffb0b0); }} .statpair {{ display:grid; grid-template-columns:repeat(2, minmax(0,1fr)); gap:10px; margin-top:12px; }} .mini {{ border:1px solid rgba(255,255,255,.1); background:rgba(0,0,0,.14); border-radius:16px; padding:12px; }} .mini b {{ font-size:22px; display:block; }}
    .barrow {{ display:grid; grid-template-columns:minmax(145px, 280px) 1fr 74px; gap:12px; align-items:center; padding:9px 0; border-bottom:1px solid rgba(255,255,255,.07); }} .barrow:last-child {{ border-bottom:0; }}
    .catname {{ display:block; font-weight:750; }} .catmeta {{ display:block; color:var(--muted); font-size:12px; margin-top:2px; }} .bartrack {{ height:15px; border-radius:999px; background:rgba(255,255,255,.10); overflow:hidden; }} .barfill {{ height:100%; border-radius:999px; background:linear-gradient(90deg,var(--pink),var(--blue)); }} .barfill.correct {{ background:linear-gradient(90deg,var(--green),#9ef5c2); }} .barfill.wrong {{ background:linear-gradient(90deg,var(--red),#ffb0b0); }} .mutedfill {{ opacity:.45; }} .barvalue {{ text-align:right; font-weight:850; }} .barvalue small {{ display:block; color:var(--muted); font-weight:600; font-size:11px; }}
    .categorydetails {{ border-bottom:1px solid rgba(255,255,255,.07); }} .categorydetails summary {{ display:grid; grid-template-columns:minmax(145px, 280px) 1fr 74px; gap:12px; align-items:center; padding:10px 0; cursor:pointer; list-style:none; }} .categorydetails summary::-webkit-details-marker {{ display:none; }} .categorydetails summary .catname::before {{ content:"▸"; display:inline-block; width:16px; color:var(--blue); transition:transform .16s ease; }} .categorydetails[open] summary .catname::before {{ transform:rotate(90deg); }} .categorydetails summary .barfill {{ display:block; }} .detailrows {{ margin:2px 0 8px 16px; padding-left:14px; border-left:1px solid rgba(255,255,255,.12); }}
    .columnchart {{ height:275px; display:grid; grid-template-columns:repeat(auto-fit, minmax(0,1fr)); gap:12px; align-items:end; padding:18px 8px 4px; border:1px solid rgba(255,255,255,.08); border-radius:18px; background:rgba(0,0,0,.12); }} .colgroup {{ min-width:0; }} .columns {{ height:210px; display:flex; align-items:end; justify-content:center; gap:7px; border-bottom:1px solid rgba(255,255,255,.16); }} .col {{ width:min(30px, 38%); min-height:0; border-radius:9px 9px 0 0; position:relative; box-shadow:0 12px 24px rgba(0,0,0,.18); }} .col.new {{ background:linear-gradient(180deg,var(--green),var(--blue)); }} .col.active {{ background:linear-gradient(180deg,var(--pink),var(--amber)); }} .col span {{ position:absolute; top:-22px; left:50%; transform:translateX(-50%); font-size:12px; font-weight:800; color:#f5f8ff; }} .axislabel {{ margin-top:8px; text-align:center; color:#dce9fb; font-size:13px; font-weight:760; }} .axislabel small {{ display:block; color:var(--muted); font-weight:650; }}
    .comparebar {{ display:grid; grid-template-columns:118px 1fr 58px; gap:12px; align-items:center; padding:10px 0; border-bottom:1px solid rgba(255,255,255,.07); }} .comparebar:last-child {{ border-bottom:0; }} .comparelabel {{ font-weight:800; }} .comparelabel small {{ display:block; color:var(--muted); font-weight:650; margin-top:2px; }}
    .mixrow {{ margin-top:14px; }} .mixbar {{ height:22px; border-radius:999px; overflow:hidden; background:rgba(255,255,255,.10); display:flex; border:1px solid rgba(255,255,255,.08); }} .mixbar span {{ display:block; height:100%; }} .easyseg {{ background:linear-gradient(90deg,var(--green),#9ef5c2); }} .mediumseg {{ background:linear-gradient(90deg,var(--amber),#ffe1a0); }} .hardseg {{ background:linear-gradient(90deg,var(--red),#ffb0b0); }} .unknownseg {{ background:linear-gradient(90deg,#8a98ab,#c4cfde); }} .compact {{ margin-top:8px; gap:6px; }}
    .datarow {{ display:grid; grid-template-columns:1fr auto; gap:12px; align-items:center; padding:10px 0; border-bottom:1px solid rgba(255,255,255,.07); }} .datarow:last-child {{ border-bottom:0; }} .datarow .value {{ color:#f5f8ff; white-space:nowrap; }}
    .warning {{ border-color:rgba(247,199,107,.32); background:linear-gradient(135deg, rgba(247,199,107,.12), rgba(255,255,255,.055)); }}
    ul {{ margin:10px 0 0; padding-left:20px; color:#d7e3f4; line-height:1.62; }} code {{ color:#d6e8ff; }} footer {{ margin-top:16px; color:var(--muted); font-size:13px; text-align:center; }}
    @media (max-width:900px) {{ .metrics,.two {{ grid-template-columns:1fr; }} .sectionhead {{ display:block; }} .sectionhead p {{ text-align:left; margin-top:6px; }} .barrow,.datarow,.comparebar,.categorydetails summary {{ grid-template-columns:1fr; gap:7px; }} .barvalue {{ text-align:left; }} .barvalue small {{ display:inline; margin-left:6px; }} .detailrows {{ margin-left:4px; padding-left:10px; }} .columnchart {{ gap:7px; padding-left:4px; padding-right:4px; }} .col span {{ font-size:11px; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <div class="eyebrow">Alfred Dashboard · Live Supabase snapshot</div>
      <h1>Trivia DB Progress</h1>
      <p class="lead">This view now uses the real Quiz Supabase tables as the source of truth: raw gathered questions versus production-ready Hebrew and English questions.</p>
      <div class="pillrow"><span class="pill">🔒 Private Tailscale route</span><span class="pill">🧮 {fmt(counts['trivia_categories'])} categories</span><span class="pill">🌅 Daily morning refresh</span><span class="pill">🕒 Last updated: <b id="lastUpdatedAgo" data-generated-at="{ts_iso}">00:00 ago</b> <span class="muted">({ts})</span></span></div>
      <div class="refreshbar"><button id="refreshButton" class="button" type="button">↻ Refresh now</button><span id="refreshStatus" class="statusline">Manual refresh pulls fresh Supabase counts and reloads this page. Cron refreshes it every morning.</span></div>
    </header>

    <section class="grid metrics">
      <div class="card"><div class="label">Ready Hebrew</div><div class="num">{fmt(he_done)}</div><div class="note">{pct(he_done, he_raw):.1f}% of raw Hebrew pool</div></div>
      <div class="card"><div class="label">Ready English</div><div class="num">{fmt(en_done)}</div><div class="note">{pct(en_done, en_raw):.1f}% of raw English pool</div></div>
      <div class="card"><div class="label">New sessions · {SESSION_WINDOW_DAYS} days</div><div class="num">{fmt(len(new_sessions_window))}</div><div class="note">Created since {html.escape(days[0].isoformat())} UTC</div></div>
      <div class="card"><div class="label">Active sessions · 24h</div><div class="num">{fmt(len(active_sessions_24h))}</div><div class="note">Latest update: {html.escape(age(latest_session_update, generated_at))}</div></div>
    </section>

    <div class="sectionhead">
      <h2>Usage</h2>
      <p class="muted">Session activity is shown for the last {SESSION_WINDOW_DAYS} UTC days, matching the current session-retention window.</p>
    </div>
    <section class="grid two">
      <div class="card">
        <h2>Sessions by day</h2>
        <div class="diffmap"><span class="diffitem"><span class="swatch easy"></span>New sessions</span><span class="diffitem"><span class="swatch medium"></span>Updated sessions</span></div>
        {session_column_chart(days, new_by_day, active_by_day)}
        <p class="muted">Source: <code>trivia_sessions.created_at</code> and <code>updated_at</code>. This measures opened/active session tokens, not unique humans.</p>
      </div>
      <div class="card">
        <h2>Session summary</h2>
        <div class="statpair">
          <div class="mini"><span class="label">Total sessions</span><b>{fmt(counts['trivia_sessions'])}</b><span class="muted"><code>trivia_sessions</code></span></div>
          <div class="mini"><span class="label">{SESSION_WINDOW_DAYS}-day change</span><b>{html.escape(session_delta_label)}</b><span class="muted">vs previous {SESSION_WINDOW_DAYS} days</span></div>
          <div class="mini"><span class="label">Seen Hebrew IDs</span><b>{fmt(seen_he)}</b><span class="muted">Across stored sessions</span></div>
          <div class="mini"><span class="label">Seen English IDs</span><b>{fmt(seen_en)}</b><span class="muted">Across stored sessions</span></div>
        </div>
        <p class="muted">This is the best currently available usage signal. Exact API/database request counts need explicit event logging.</p>
      </div>
    </section>

    <div class="sectionhead">
      <h2>Quality</h2>
      <p class="muted">Answer counters and reports are grouped by language so Hebrew and English problems do not hide inside one combined number.</p>
    </div>
    <section class="grid two">
      <div class="card">
        <h2>Answer performance</h2>
        <div class="statpair">
          <div class="mini"><span class="label">Hebrew correct rate</span><b>{pct(he_stats['correct_answers'], he_stats['correct_answers'] + he_stats['wrong_answers']):.1f}%</b><span class="muted">{fmt(he_stats['correct_answers'] + he_stats['wrong_answers'])} answers</span></div>
          <div class="mini"><span class="label">English correct rate</span><b>{pct(en_stats['correct_answers'], en_stats['correct_answers'] + en_stats['wrong_answers']):.1f}%</b><span class="muted">{fmt(en_stats['correct_answers'] + en_stats['wrong_answers'])} answers</span></div>
        </div>
        <div style="margin-top:12px">{answer_comparison_chart(he_stats, en_stats)}</div>
        <p class="muted">Bars share one scale, making the Hebrew/English split easier to compare than separate stacked panels.</p>
      </div>
      <div class="card">
        <h2>Report status</h2>
        <div class="statpair">
          <div class="mini"><span class="label">Hebrew reported questions</span><b>{fmt(he_stats['reported_questions'])}</b><span class="muted">{fmt(he_stats['total_reports'])} total reports</span></div>
          <div class="mini"><span class="label">English reported questions</span><b>{fmt(en_stats['reported_questions'])}</b><span class="muted">{fmt(en_stats['total_reports'])} total reports</span></div>
        </div>
        <h2 style="margin-top:18px">Top reported rows</h2>
        {report_rows(question_rows['questions_he'], 'Hebrew')}
        {report_rows(question_rows['questions_en'], 'English')}
        <p class="muted">“Reported questions” counts rows where <code>reports &gt; 0</code>. The current schema stores counters only, so exact report timestamps are not available yet.</p>
      </div>
    </section>

    <div class="sectionhead">
      <h2>Content</h2>
      <p class="muted">These charts describe the question inventory: raw-to-ready progress, difficulty balance, and strongest categories.</p>
    </div>
    <section class="grid two">
      <div class="card">
        <h2>Completion from raw pool</h2>
        <div class="statpair">
          <div class="mini"><span class="label">Raw Hebrew pool</span><b>{fmt(he_raw)}</b><span class="muted"><code>raw_questions_he</code></span></div>
          <div class="mini"><span class="label">Raw English pool</span><b>{fmt(en_raw)}</b><span class="muted"><code>questions_raw_en</code></span></div>
        </div>
        <div class="progressline"><div class="split"><span>Hebrew production-ready</span><span class="value">{fmt(he_done)} / {fmt(he_raw)} · {pct(he_done, he_raw):.1f}%</span></div><div class="track"><div class="fill" style="width:{pct(he_done, he_raw):.3f}%"></div></div></div>
        <div class="progressline"><div class="split"><span>English production-ready</span><span class="value">{fmt(en_done)} / {fmt(en_raw)} · {pct(en_done, en_raw):.1f}%</span></div><div class="track"><div class="fill" style="width:{pct(en_done, en_raw):.3f}%"></div></div></div>
        <div class="progressline"><div class="split"><span>Total ready across HE + EN</span><span class="value">{fmt(total_done)} / {fmt(total_raw)} · {pct(total_done, total_raw):.1f}%</span></div><div class="track"><div class="fill" style="width:{pct(total_done, total_raw):.3f}%"></div></div></div>
        <p class="muted">Raw tables are treated as 100% gathered inventory. Ready tables are the actual playable/refined question pools.</p>
      </div>
      <div class="card">
        <h2>Difficulty mix</h2>
        <div class="diffmap" aria-label="Difficulty color map">
          <span class="diffitem"><span class="swatch easy"></span>Easy</span>
          <span class="diffitem"><span class="swatch medium"></span>Medium</span>
          <span class="diffitem"><span class="swatch hard"></span>Hard</span>
        </div>
        {difficulty_mix_bars(diff_counts, {'questions_he': he_done, 'questions_en': en_done})}
        <p class="muted">A shared 100% scale makes the language mix easier to compare than separate donut charts.</p>
      </div>
    </section>

    <section class="grid two">
      <div class="card"><h2>Top Hebrew categories</h2>{category_rows(cat_counts['questions_he'], he_done, 'he')}</div>
      <div class="card"><h2>Top English categories</h2>{category_rows(cat_counts['questions_en'], en_done, 'en')}</div>
    </section>

    <div class="sectionhead">
      <h2>Feedback</h2>
      <p class="muted">Suggestions are kept compact because the volume is low today; reports can become a trend chart once timestamps are tracked.</p>
    </div>
    <section class="grid two">
      <div class="card">
        <h2>User suggestions</h2>
        <div class="statpair">
          <div class="mini"><span class="label">Suggestions · 7 days</span><b>{fmt(len(suggestions_7d))}</b><span class="muted">New submissions</span></div>
          <div class="mini"><span class="label">All suggestions</span><b>{fmt(counts['question_suggestions'])}</b><span class="muted">{html.escape(', '.join(f'{k}: {v}' for k, v in sorted(suggestions_by_status.items())) or 'no statuses')}</span></div>
        </div>
        <div class="pillrow"><span class="tag">HE: <b>{fmt(suggestions_by_lang.get('he', 0))}</b></span><span class="tag">EN: <b>{fmt(suggestions_by_lang.get('en', 0))}</b></span><span class="tag">Unknown: <b>{fmt(suggestions_by_lang.get('unknown', 0))}</b></span></div>
        {suggestion_rows(suggestions, generated_at)}
      </div>
      <div class="card warning">
        <h2>Telemetry gaps</h2>
        <ul>
          <li>DB/API request counts are not currently stored in a public table.</li>
          <li>Report timestamps are not currently stored; only per-question report counters exist.</li>
          <li>For exact usage analytics, add an append-only event table for session starts, answers, reports, and API calls.</li>
        </ul>
      </div>
    </section>

    <section class="card" style="margin-top:16px">
      <h2>What this means</h2>
      <ul>
        <li>The real ready tables currently contain <b>{fmt(he_done)}</b> Hebrew questions and <b>{fmt(en_done)}</b> English questions.</li>
        <li>Usage: <b>{fmt(len(new_sessions_window))}</b> new sessions opened in the last {SESSION_WINDOW_DAYS} UTC days, with <b>{fmt(len(active_sessions_24h))}</b> sessions active in the last 24 hours.</li>
        <li>Session inventory has seen <b>{fmt(seen_he)}</b> Hebrew question IDs and <b>{fmt(seen_en)}</b> English question IDs across all stored sessions.</li>
        <li>Reports: <b>{fmt(he_stats['reported_questions'])}</b> Hebrew questions and <b>{fmt(en_stats['reported_questions'])}</b> English questions currently have at least one report.</li>
        <li>Answer counters show <b>{fmt(total_correct)}</b> correct answers and <b>{fmt(total_wrong)}</b> wrong answers across both ready tables, with Hebrew and English performance split into separate panels.</li>
        <li>The dashboard can show session starts and interaction counters today; exact DB/API call counts and report timestamps need explicit event logging in the app/backend.</li>
        <li>Hebrew is strongest in History, Science & Nature, General Knowledge, and Animals.</li>
        <li>English is currently strongest in Geography, Music, Science & Nature, and General Knowledge.</li>
        <li>This page refreshes automatically every morning and still supports manual refresh from the header.</li>
      </ul>
    </section>
    <footer>Built by Alfred 🎩 · source of truth: Supabase project uhfsfedwteeoxsvixvtr</footer>
  </div>
  <script>
    const button = document.getElementById('refreshButton');
    const status = document.getElementById('refreshStatus');
    const lastUpdatedAgo = document.getElementById('lastUpdatedAgo');
    function updateLastUpdatedAgo() {{
      if (!lastUpdatedAgo) return;
      const generatedAt = new Date(lastUpdatedAgo.dataset.generatedAt);
      const diffMs = Math.max(0, Date.now() - generatedAt.getTime());
      const totalMinutes = Math.floor(diffMs / 60000);
      const hours = Math.floor(totalMinutes / 60);
      const minutes = totalMinutes % 60;
      lastUpdatedAgo.textContent = String(hours).padStart(2, '0') + ':' + String(minutes).padStart(2, '0') + ' ago';
    }}
    updateLastUpdatedAgo();
    setInterval(updateLastUpdatedAgo, 60000);
    button?.addEventListener('click', async () => {{
      const base = window.location.pathname.replace(/\\/?$/, '');
      const refreshUrl = base + '/refresh';
      button.disabled = true;
      const oldText = button.textContent;
      button.textContent = 'Refreshing…';
      status.textContent = 'Pulling live Supabase counts. This can take a few seconds.';
      try {{
        const res = await fetch(refreshUrl, {{ method: 'POST' }});
        const data = await res.json().catch(() => ({{}}));
        if (!res.ok || !data.ok) throw new Error(data.error || data.stderr || ('HTTP ' + res.status));
        status.textContent = 'Updated successfully in ' + data.durationSeconds + 's. Reloading…';
        window.location.reload();
      }} catch (err) {{
        status.textContent = 'Refresh failed: ' + (err.message || err);
        button.disabled = false;
        button.textContent = oldText;
      }}
    }});
  </script>
</body>
</html>
"""
    OUT_PATH.write_text(html_doc)
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
