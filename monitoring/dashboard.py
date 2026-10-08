"""Static dashboard for the monitored failure mode, standing in for a Langfuse dashboard.

    python -m monitoring.dashboard

Writes ``monitoring/dashboard.html``, one self-contained page with the three views the
handout asks for:

  1. the random-sample verdict score over time (with the corrected rate and threshold),
  2. the risk verdict score over time,
  3. the traces flagged by either score, with the judge's critique.

Score values come from the score store (``monitoring/output/scores.jsonl``), the way a
Langfuse dashboard reads scores. A score carries only a trace id, so period membership
comes from each period's ``verdicts.json``. A store value that disagrees with the saved
verdict stops the build instead of drawing a wrong number.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

MONITORING = Path(__file__).resolve().parent
OUTPUT = MONITORING / "output"
CONFIG = MONITORING / "config.json"
HISTORY = MONITORING / "history.jsonl"
SCHEDULED_HISTORY = OUTPUT / "scheduled-history.jsonl"
DASHBOARD = MONITORING / "dashboard.html"

_W, _H = 560, 280
_LEFT, _RIGHT, _TOP, _BOTTOM = 48, 96, 16, 40


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_periods(mode: str) -> list[dict[str, Any]]:
    """One dict per judged period, oldest first, with score-store values attached."""
    scores = {(s["name"], s["trace_id"]): s["value"] for s in _jsonl(OUTPUT / "scores.jsonl")}
    periods = []
    for entry in sorted(_jsonl(HISTORY), key=lambda e: e["from"]):
        work = OUTPUT / entry["label"]
        if "corrected" not in entry or not (work / "verdicts.json").exists():
            continue
        verdicts = json.loads((work / "verdicts.json").read_text())
        conversations = {c["trace_id"]: c for c in json.loads((work / "conversations.json").read_text())}
        plan = json.loads((work / "plan.json").read_text())
        critiques = {r["trace_id"]: r["critique"] for r in _jsonl(work / "judge" / "results.jsonl")}

        def from_store(kind: str, name: str) -> dict[str, float]:
            values = {}
            for trace_id, saved in verdicts[kind].items():
                value = scores.get((name, trace_id))
                if value is None or int(value) != saved:
                    raise SystemExit(f"{entry['label']}: score store has {value!r} for "
                                     f"{name} on {trace_id}, verdicts.json has {saved}")
                values[trace_id] = value
            return values

        random_scores = from_store("random", f"{mode}_verdict")
        risk_scores = from_store("risk", f"{mode}_risk_verdict")
        flagged = []
        for trace_id in dict.fromkeys([*random_scores, *risk_scores]):
            if random_scores.get(trace_id) != 1 and risk_scores.get(trace_id) != 1:
                continue
            conv = conversations[trace_id]
            flagged.append({
                "scenario": conv["key"], "trace_id": trace_id,
                "random": trace_id in random_scores, "risk": trace_id in risk_scores,
                "groups": [g for g, keys in plan["risk_groups"].items() if conv["key"] in keys],
                "turns": conv["turn_count"], "tools": conv["tools"],
                "critique": critiques.get(trace_id, ""),
            })
        periods.append({
            **entry,
            "random_mean": sum(random_scores.values()) / len(random_scores),
            "risk_mean": sum(risk_scores.values()) / len(risk_scores),
            "flagged": flagged,
        })
    return periods


def _x(i: int, n: int) -> float:
    span = _W - _LEFT - _RIGHT
    return _LEFT + span * (i + 0.5) / n


def _y(value: float) -> float:
    return _TOP + (_H - _TOP - _BOTTOM) * (1 - value)


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%".replace(".0%", "%")


def _frame(periods: list[dict[str, Any]], label: str) -> list[str]:
    """Gridlines, y ticks at 0-100%, and one x label per period."""
    parts = [f'<svg viewBox="0 0 {_W} {_H}" role="img" aria-label="{html.escape(label)}">']
    for tick in (0, 0.25, 0.5, 0.75, 1):
        y = _y(tick)
        cls = "axis" if tick == 0 else "grid"
        parts.append(f'<line class="{cls}" x1="{_LEFT}" x2="{_W - _RIGHT}" y1="{y:.1f}" y2="{y:.1f}"/>')
        parts.append(f'<text class="tick" x="{_LEFT - 8}" y="{y + 4:.1f}" text-anchor="end">{_pct(tick)}</text>')
    for i, p in enumerate(periods):
        x = _x(i, len(periods))
        parts.append(f'<text class="tick" x="{x:.1f}" y="{_H - _BOTTOM + 18}" text-anchor="middle">'
                     f'{html.escape(p["label"])} · {p["from"][:10]}</text>')
    return parts


def _point(x: float, y: float, series: str, tip: str) -> str:
    return (f'<g class="pt" data-tip="{html.escape(tip)}">'
            f'<circle class="hit" cx="{x:.1f}" cy="{y:.1f}" r="14"/>'
            f'<circle class="dot {series}" cx="{x:.1f}" cy="{y:.1f}" r="5"/></g>')


def _line(points: list[tuple[float, float]], series: str) -> str:
    coords = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    return f'<polyline class="line {series}" points="{coords}"/>'


def random_chart(periods: list[dict[str, Any]], threshold: float) -> str:
    n = len(periods)
    parts = _frame(periods, "Random-sample verdict score and corrected failure rate by period")
    ty = _y(threshold)
    parts.append(f'<line class="threshold" x1="{_LEFT}" x2="{_W - _RIGHT}" y1="{ty:.1f}" y2="{ty:.1f}"/>')
    parts.append(f'<text class="ref" x="{_W - _RIGHT + 6}" y="{ty + 4:.1f}">threshold {_pct(threshold)}</text>')
    for i, p in enumerate(periods):
        x = _x(i, n)
        parts.append(f'<line class="ci" x1="{x + 7:.1f}" x2="{x + 7:.1f}" '
                     f'y1="{_y(p["ci_low"]):.1f}" y2="{_y(p["ci_high"]):.1f}"/>')
    raw = [(_x(i, n) - 7, _y(p["random_mean"])) for i, p in enumerate(periods)]
    corrected = [(_x(i, n) + 7, _y(p["corrected"])) for i, p in enumerate(periods)]
    parts += [_line(raw, "s2"), _line(corrected, "s1")]
    for (x, y), p in zip(raw, periods):
        parts.append(_point(x, y, "s2", f'{p["label"]}: random verdict score {_pct(p["random_mean"])} '
                                        f'({p["random_flagged"]} of {p["random_count"]} flagged)'))
    for (x, y), p in zip(corrected, periods):
        parts.append(_point(x, y, "s1", f'{p["label"]}: corrected {_pct(p["corrected"])}, '
                                        f'95% CI {_pct(p["ci_low"])} to {_pct(p["ci_high"])}'))
    last = periods[-1]
    parts.append(f'<text class="end" x="{corrected[-1][0] + 10:.1f}" y="{corrected[-1][1] + 4:.1f}">'
                 f'{_pct(last["corrected"])}</text>')
    parts.append("</svg>")
    return "".join(parts)


def risk_chart(periods: list[dict[str, Any]]) -> str:
    n = len(periods)
    parts = _frame(periods, "Risk verdict score by period")
    pts = [(_x(i, n), _y(p["risk_mean"])) for i, p in enumerate(periods)]
    parts.append(_line(pts, "s1"))
    for (x, y), p in zip(pts, periods):
        groups = ", ".join(f"{g} {c}" for g, c in p["risk_group_counts"].items())
        parts.append(_point(x, y, "s1", f'{p["label"]}: risk verdict score {_pct(p["risk_mean"])} '
                                        f'({p["risk_flagged"]} of {p["risk_count"]} flagged; {groups})'))
        parts.append(f'<text class="end" x="{x + 10:.1f}" y="{y - 8:.1f}">'
                     f'{p["risk_flagged"]} of {p["risk_count"]}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _table(head: list[str], rows: list[list[str]]) -> str:
    th = "".join(f"<th>{h}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>"


def render(config: dict[str, Any], periods: list[dict[str, Any]],
           scheduled: list[dict[str, Any]]) -> str:
    mode, threshold = config["judge_mode"], config["threshold"]
    last = periods[-1]
    crossed = last["corrected"] >= threshold
    status = ("crossed: start error analysis on the flagged traces" if crossed
              else "below threshold")

    trend_rows = [[html.escape(p["label"]), f'{p["from"]} to {p["to"]}',
                   f'{p["random_flagged"]} of {p["random_count"]}', _pct(p["random_mean"]),
                   _pct(p["corrected"]), f'{_pct(p["ci_low"])} to {_pct(p["ci_high"])}',
                   f'{p["risk_flagged"]} of {p["risk_count"]}', _pct(p["risk_mean"])]
                  for p in periods]
    flagged_rows = []
    for p in periods:
        for f in p["flagged"]:
            which = " + ".join(w for w, on in (("random", f["random"]), ("risk", f["risk"])) if on)
            flagged_rows.append([
                html.escape(p["label"]), html.escape(f["scenario"]),
                f'<code>{f["trace_id"][:12]}</code>', which,
                html.escape(", ".join(f["groups"]) or "—"), str(f["turns"]),
                f'<details><summary>critique</summary><p>{html.escape(f["critique"])}</p></details>',
            ])
    run_rows = [[html.escape(e["label"]), "period" if "corrected" in e else "scheduled",
                 f'{e["from"]} to {e["to"]}', str(e.get("conversation_count", 0)),
                 str(e.get("judge_calls", 0)), e.get("run_at", "")]
                for e in sorted([*_jsonl(HISTORY), *scheduled], key=lambda e: e["run_at"])]

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{mode} monitor</title>
<style>
.viz-root {{
  color-scheme: light;
  --surface-1: #fcfcfb; --page: #f9f9f7; --text-primary: #0b0b0b; --text-secondary: #52514e;
  --muted: #898781; --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
  --series-1: #2a78d6; --series-1-wash: #b7d3f6; --series-2: #eb6834; --critical: #d03b3b;
}}
@media (prefers-color-scheme: dark) {{
  .viz-root {{
    color-scheme: dark;
    --surface-1: #1a1a19; --page: #0d0d0d; --text-primary: #ffffff; --text-secondary: #c3c2b7;
    --muted: #898781; --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
    --series-1: #3987e5; --series-1-wash: #184f95; --series-2: #d95926;
  }}
}}
body {{ margin: 0; }}
.viz-root {{ background: var(--page); color: var(--text-primary); min-height: 100vh;
  font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; padding: 24px 32px; }}
h1 {{ font-size: 20px; margin: 0 0 4px; }} h2 {{ font-size: 15px; margin: 0 0 2px; }}
.sub {{ color: var(--text-secondary); margin: 0 0 16px; }}
.tiles {{ display: flex; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }}
.tile, .card {{ background: var(--surface-1); border: 1px solid var(--border); border-radius: 8px; }}
.tile {{ padding: 12px 16px; min-width: 150px; }}
.tile .label {{ color: var(--text-secondary); font-size: 12px; }}
.tile .value {{ font-size: 28px; font-weight: 600; }}
.tile .note {{ color: var(--muted); font-size: 12px; }}
.grid2 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 16px; }}
.card {{ padding: 16px; margin-bottom: 16px; }}
.legend {{ display: flex; gap: 16px; color: var(--text-secondary); font-size: 12px; margin: 6px 0; }}
.key {{ display: inline-block; width: 14px; height: 2px; vertical-align: middle; margin-right: 6px; }}
.key.ci {{ width: 3px; height: 12px; }}
.key.th {{ border-top: 1px dashed var(--muted); height: 0; }}
svg {{ width: 100%; height: auto; display: block; }}
svg text {{ font: 11px system-ui, -apple-system, sans-serif; fill: var(--muted); }}
svg text.end {{ fill: var(--text-secondary); font-size: 12px; }}
.grid {{ stroke: var(--grid); stroke-width: 1; }} .axis {{ stroke: var(--axis); stroke-width: 1; }}
.threshold {{ stroke: var(--muted); stroke-width: 1; stroke-dasharray: 4 3; }}
.ci {{ stroke: var(--series-1-wash); stroke-width: 4; stroke-linecap: round; }}
.line {{ fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }}
.line.s1 {{ stroke: var(--series-1); }} .line.s2 {{ stroke: var(--series-2); }}
.dot {{ stroke: var(--surface-1); stroke-width: 2; }}
.dot.s1 {{ fill: var(--series-1); }} .dot.s2 {{ fill: var(--series-2); }}
.key.s1 {{ background: var(--series-1); }} .key.s2 {{ background: var(--series-2); }}
.key.ci {{ background: var(--series-1-wash); }}
.hit {{ fill: transparent; cursor: default; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--grid); vertical-align: top; }}
th {{ color: var(--text-secondary); font-weight: 600; }}
td {{ font-variant-numeric: tabular-nums; }}
details p {{ max-width: 720px; color: var(--text-secondary); margin: 6px 0; }}
summary {{ cursor: pointer; color: var(--text-secondary); }}
#tip {{ position: fixed; pointer-events: none; background: var(--surface-1); color: var(--text-primary);
  border: 1px solid var(--border); border-radius: 6px; padding: 6px 10px; font-size: 12px;
  box-shadow: 0 2px 8px rgba(0,0,0,.15); display: none; max-width: 320px; }}
</style></head>
<body><div class="viz-root">
<h1>{mode}</h1>
<p class="sub">Frozen judge <code>{config["judge_id"]}</code> · Cartwheel model <code>{config["model"]}</code> ·
scores read from <code>monitoring/output/scores.jsonl</code> (local stand-in for Langfuse scores).
1 = failure flagged, 0 = not flagged.</p>

<div class="tiles">
  <div class="tile"><div class="label">Corrected failure rate, {html.escape(last["label"])}</div>
    <div class="value">{_pct(last["corrected"])}</div>
    <div class="note">95% CI {_pct(last["ci_low"])} to {_pct(last["ci_high"])}</div></div>
  <div class="tile"><div class="label">Threshold</div><div class="value">{_pct(threshold)}</div>
    <div class="note">{status}</div></div>
  <div class="tile"><div class="label">Flagged traces, {html.escape(last["label"])}</div>
    <div class="value">{len(last["flagged"])}</div>
    <div class="note">by either score</div></div>
</div>

<div class="grid2">
<div class="card"><h2>Random-sample verdict score over time</h2>
<p class="sub">Share of the 10 random conversations the judge flagged, and the corrected rate it implies.
Only this sample estimates the failure rate.</p>
<div class="legend"><span><i class="key s2"></i>random verdict score (raw)</span>
<span><i class="key s1"></i>corrected failure rate</span><span><i class="key ci"></i>95% interval</span>
<span><i class="key th"></i>threshold</span></div>
{random_chart(periods, threshold)}</div>
<div class="card"><h2>Risk verdict score over time</h2>
<p class="sub">Share of risk-group conversations flagged ({", ".join(config["risk_groups"])}).
Not a random sample, so it is not a rate estimate.</p>
{risk_chart(periods)}</div>
</div>

<div class="card"><h2>Traces flagged by either score</h2>
{_table(["period", "scenario", "trace", "flagged by", "risk groups", "turns", "judge"], flagged_rows)}</div>

<div class="card"><h2>Chart data</h2>
{_table(["period", "window (UTC)", "random flagged", "random score", "corrected", "95% CI",
         "risk flagged", "risk score"], trend_rows)}</div>

<div class="card"><h2>Monitor runs</h2>
{_table(["label", "kind", "window (UTC)", "conversations", "judge calls", "run at (UTC)"], run_rows)}</div>
</div>
<div id="tip"></div>
<script>
const tip = document.getElementById("tip");
document.querySelectorAll(".pt").forEach(g => {{
  g.addEventListener("mousemove", e => {{
    tip.textContent = g.dataset.tip; tip.style.display = "block";
    tip.style.left = (e.clientX + 14) + "px"; tip.style.top = (e.clientY + 14) + "px";
  }});
  g.addEventListener("mouseleave", () => {{ tip.style.display = "none"; }});
}});
</script>
</body></html>
"""


def write_dashboard() -> Path:
    config = json.loads(CONFIG.read_text())
    periods = load_periods(config["judge_mode"])
    if not periods:
        raise SystemExit("no judged periods in monitoring/history.jsonl")
    DASHBOARD.write_text(render(config, periods, _jsonl(SCHEDULED_HISTORY)))
    return DASHBOARD


if __name__ == "__main__":
    print(f"dashboard -> {write_dashboard()}")
