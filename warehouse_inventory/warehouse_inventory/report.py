"""Self-contained HTML exception report."""

from __future__ import annotations

import html
from datetime import datetime

from .reconcile import STATUS_PRIORITY

STATUS_STYLE = {
    "MATCH": "ok",
    "CONDITION": "warn",
    "NEEDS_REVIEW": "warn",
    "NOT_IN_WMS": "bad",
    "UNKNOWN_PALLET": "bad",
    "MISPLACED_PALLET": "bad",
    "LOCATION_MISMATCH": "bad",
    "MIXED_SKU": "bad",
    "SKU_MISMATCH": "bad",
    "COUNT_MISMATCH": "bad",
    "NOT_FLOWN": "muted",
}

CSS = """
:root{--bg:#f7f7f5;--card:#fff;--fg:#1b1b1b;--muted:#6b6b6b;--line:#e3e3df;
--ok:#1f7a4d;--ok-bg:#e6f4ec;--warn:#8a5a00;--warn-bg:#fdf2dc;--bad:#a8261b;--bad-bg:#fbe6e3}
@media (prefers-color-scheme:dark){:root{--bg:#141414;--card:#1e1e1e;--fg:#ededed;--muted:#9a9a9a;
--line:#333;--ok:#6fd39f;--ok-bg:#16301f;--warn:#f0c46b;--warn-bg:#352a12;--bad:#ff8a7d;--bad-bg:#3a1a17}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;padding:24px 16px}
main{max-width:1200px;margin:0 auto}h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 10px}
.sub{color:var(--muted);margin-bottom:20px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.kpi b{display:block;font-size:24px;font-variant-numeric:tabular-nums}.kpi span{color:var(--muted);font-size:12px}
.wrap{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;min-width:760px}th,td{padding:8px 10px;text-align:left;
border-bottom:1px solid var(--line);vertical-align:top}th{font-size:12px;color:var(--muted);font-weight:600}
td.n{text-align:right;font-variant-numeric:tabular-nums}tr:last-child td{border-bottom:0}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600;white-space:nowrap}
.ok{color:var(--ok);background:var(--ok-bg)}.warn{color:var(--warn);background:var(--warn-bg)}
.bad{color:var(--bad);background:var(--bad-bg)}.muted{color:var(--muted);background:var(--line)}
.small{color:var(--muted);font-size:12px}
td.lane b{white-space:nowrap}
"""


def _e(v) -> str:
    return html.escape("" if v is None else str(v))


def _pill(status: str) -> str:
    return f'<span class="pill {STATUS_STYLE.get(status, "muted")}">{_e(status.replace("_", " "))}</span>'


def render(rows: list[dict], issues: list[dict], summary: dict, title: str = "Lane Inventory Report") -> str:
    order = {s: i for i, s in enumerate(STATUS_PRIORITY)}
    rows = sorted(rows, key=lambda r: (order.get(r["status"], 0), r["lane_id"]))
    acc = (
        f'{100 * summary["lanes_match"] / summary["lanes_flown"]:.0f}%' if summary["lanes_flown"] else "–"
    )
    kpis = [
        (summary["lanes_flown"], f'lanes flown of {summary["lanes_in_wms"]} in WMS'),
        (acc, "lanes clean (no exception)"),
        (summary["count_mismatches"], "count mismatches"),
        (summary["misplaced_pallets"], "pallets found in the wrong location"),
        (summary["sku_exceptions"], "SKU / mixed-lane / unknown-pallet exceptions"),
        (f'{summary["pallet_ids_confirmed"]} / {summary["pallet_ids_read"]}', "pallet IDs confirmed / read"),
        (f'{summary["pallets_counted"]} / {summary["pallets_expected"]}', "pallets counted / expected"),
        (summary["issues_high"], "high-severity condition issues"),
        (summary["needs_review"], "lanes needing human review"),
    ]
    kpi_html = "".join(f'<div class="kpi"><b>{_e(v)}</b><span>{_e(l)}</span></div>' for v, l in kpis)

    lane_rows = "".join(
        "<tr>"
        f'<td class="lane"><b>{_e(r["lane_id"])}</b><div class="small">{_e(r.get("video"))}</div></td>'
        f'<td>{_pill(r["status"])}<div class="small">{_e(r["flags"].replace("|", ", "))}</div></td>'
        f'<td class="n">{_e(r["wms_pallets"])}</td>'
        f'<td class="n">{_e(r["counted_pallets"])}<div class="small">{_e(r["count_source"])}</div></td>'
        f'<td class="n">{_e(r["variance"])}</td>'
        f'<td>{_e(r["wms_skus"])}<div class="small">IDs read {_e(r["pallet_ids_read"])}, confirmed {_e(r["pallet_ids_confirmed"])}</div>'
        f'<div class="small">{_e(r["misplaced"].replace("|", ", "))}</div></td>'
        f'<td class="n">{_e(r["issues_count"])}</td>'
        f'<td class="small">{_e(r.get("summary"))}</td>'
        "</tr>"
        for r in rows
    )
    issue_rows = "".join(
        "<tr>"
        f'<td><span class="pill {"bad" if i.get("severity") == "high" else "warn" if i.get("severity") == "medium" else "muted"}">{_e(i.get("severity"))}</span></td>'
        f'<td><b>{_e(i["lane_id"])}</b></td>'
        f'<td>{_e(str(i.get("type", "")).replace("_", " "))}</td>'
        f'<td class="n">{_e(i.get("pallet_position_from_front"))}</td>'
        f'<td class="n">{_e(i.get("approx_time_s"))}</td>'
        f'<td>{_e(i.get("description"))}</td>'
        "</tr>"
        for i in issues
    ) or '<tr><td colspan="6" class="small">No condition issues reported.</td></tr>'

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(title)}</title>
<style>{CSS}</style></head><body><main>
<h1>{_e(title)}</h1><div class="sub">Generated {datetime.now():%Y-%m-%d %H:%M} · drone flight vs WMS export</div>
<div class="kpis">{kpi_html}</div>
<h2>Lanes (exceptions first)</h2><div class="wrap"><table>
<tr><th>Lane</th><th>Status</th><th>WMS</th><th>Counted</th><th>Var</th><th>SKU / pallet IDs</th><th>Issues</th><th>Notes</th></tr>
{lane_rows}</table></div>
<h2>Condition &amp; compliance issues</h2><div class="wrap"><table>
<tr><th>Severity</th><th>Lane</th><th>Type</th><th>Pallet #</th><th>Time (s)</th><th>Description</th></tr>
{issue_rows}</table></div>
</main></body></html>"""
