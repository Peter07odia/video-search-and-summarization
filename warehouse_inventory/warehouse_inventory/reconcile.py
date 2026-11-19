"""Reconcile drone lane results against the WMS location export."""

from __future__ import annotations

import csv
import json
import math
import re
from collections import defaultdict
from pathlib import Path

from .config import normalize_lane_id

STATUS_PRIORITY = [
    "NOT_FLOWN",
    "LOCATION_MISMATCH",  # location label read in video != lane the clip is filed under
    "MISPLACED_PALLET",  # pallet ID read here, WMS has it in another location
    "COUNT_MISMATCH",
    "MIXED_SKU",
    "SKU_MISMATCH",
    "UNKNOWN_PALLET",  # pallet ID read here, not in WMS at all
    "NOT_IN_WMS",
    "NEEDS_REVIEW",
    "CONDITION",
    "MATCH",
]
SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3}


def _num(v) -> float | None:
    if v is None or str(v).strip() == "":
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def load_wms(csv_path: str | Path, wms_cfg: dict) -> dict[str, dict]:
    cols = wms_cfg["columns"]
    lane_re = re.compile(wms_cfg["lane_regex"]) if wms_cfg.get("lane_regex") else None
    lanes: dict[str, dict] = defaultdict(
        lambda: {"locations": [], "skus": set(), "gtins": set(), "lpns": set(), "pallets": 0}
    )
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = [c for c in cols.values() if c and c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(
                f"WMS CSV is missing columns {missing}. Found: {reader.fieldnames}. "
                "Fix wms.columns in config.yaml."
            )
        for row in reader:
            loc = row[cols["location"]].strip()
            if not loc:
                continue
            lane_raw = loc
            if lane_re:
                m = lane_re.match(loc)
                lane_raw = m.group(1) if m else loc
            lane = lanes[normalize_lane_id(lane_raw)]
            lane["locations"].append(loc)
            if cols.get("sku") and row.get(cols["sku"], "").strip():
                lane["skus"].add(row[cols["sku"]].strip())
            if cols.get("gtin") and row.get(cols["gtin"], "").strip():
                lane["gtins"].add(row[cols["gtin"]].strip().zfill(14))
            if cols.get("lpn") and row.get(cols["lpn"], "").strip():
                lane["lpns"].add(row[cols["lpn"]].strip())

            pallets = _num(row.get(cols["pallets"])) if cols.get("pallets") else None
            if pallets is None and cols.get("units") and cols.get("units_per_pallet"):
                units = _num(row.get(cols["units"]))
                upp = _num(row.get(cols["units_per_pallet"]))
                if units is not None and upp:
                    pallets = math.ceil(units / upp)
            if pallets is None and cols.get("lpn") and row.get(cols["lpn"], "").strip():
                pallets = 1  # one row per pallet
            lane["pallets"] += int(pallets or 0)
    return dict(lanes)


def load_lane_results(lanes_dir: str | Path) -> dict[str, dict]:
    out = {}
    for p in sorted(Path(lanes_dir).glob("*.json")):
        with open(p) as f:
            rec = json.load(f)
        out[rec["lane_id"]] = rec
    return out


def build_lpn_index(wms: dict[str, dict]) -> dict[str, str]:
    return {lpn: lane for lane, rec in wms.items() for lpn in rec["lpns"]}


def _reconcile_lane(
    lane_id: str, wms: dict | None, res: dict | None, cfg: dict, lpn_index: dict[str, str]
) -> dict:
    rc = cfg["reconcile"]
    flags: list[str] = []
    row = {
        "lane_id": lane_id,
        "in_wms": wms is not None,
        "wms_pallets": wms["pallets"] if wms else 0,
        "wms_skus": "|".join(sorted(wms["skus"])) if wms else "",
        "counted_pallets": None,
        "count_source": "",
        "cv_count": None,
        "vlm_estimate": None,
        "variance": None,
        "pallet_ids_read": 0,
        "pallet_ids_confirmed": 0,
        "misplaced": "",
        "label_gtins": "",
        "labels_read": 0,
        "issues_count": 0,
        "max_severity": "",
        "video": "",
        "summary": "",
    }

    if res is None:
        row["status"] = "NOT_FLOWN"
        row["flags"] = "NOT_FLOWN"
        return row

    row["video"] = res.get("video", "")
    cv = (res.get("cv") or {}).get("cv_pallet_count")
    vss_parsed = (res.get("vss") or {}).get("parsed") or {}
    vlm = vss_parsed.get("vlm_pallet_estimate")
    row["cv_count"], row["vlm_estimate"] = cv, vlm
    row["summary"] = vss_parsed.get("summary", "")

    if cv is not None:
        row["counted_pallets"], row["count_source"] = cv, "cv_tracker"
    elif isinstance(vlm, int):
        row["counted_pallets"], row["count_source"] = vlm, "vlm_only"
        flags.append("NEEDS_REVIEW")  # VLM-only counts are never trusted on their own
    else:
        flags.append("NEEDS_REVIEW")

    if cv is not None and isinstance(vlm, int) and abs(cv - vlm) > rc["cross_check_tolerance"]:
        flags.append("NEEDS_REVIEW")

    counted = row["counted_pallets"]
    if wms is None:
        if counted:
            flags.append("NOT_IN_WMS")
    elif counted is not None:
        row["variance"] = counted - wms["pallets"]
        if abs(row["variance"]) > rc["count_tolerance"]:
            flags.append("COUNT_MISMATCH")

    labels = res.get("labels") or {}

    # Location check: barcode on the upright, else the location text the VLM read.
    seen_locs = {normalize_lane_id(l) for l in labels.get("locations") or []}
    vlm_loc = vss_parsed.get("location_label_text")
    if vlm_loc:
        seen_locs.add(normalize_lane_id(vlm_loc))
    if seen_locs and lane_id not in seen_locs:
        flags.append("LOCATION_MISMATCH")
        row["location_seen"] = "|".join(sorted(seen_locs))

    # Pallet IDs (LPN / SSCC): each one read is a confirmed, identified pallet.
    lpns = list(dict.fromkeys(labels.get("lpns") or []))
    row["pallet_ids_read"] = len(lpns)
    if lpn_index:
        confirmed = [x for x in lpns if lpn_index.get(x) == lane_id]
        misplaced = {x: lpn_index[x] for x in lpns if x in lpn_index and lpn_index[x] != lane_id}
        unknown = [x for x in lpns if x not in lpn_index]
        row["pallet_ids_confirmed"] = len(confirmed)
        if misplaced:
            flags.append("MISPLACED_PALLET")
            row["misplaced"] = "|".join(f"{k}->WMS {v}" for k, v in misplaced.items())
        if unknown:
            flags.append("UNKNOWN_PALLET")
            row["unknown_pallet_ids"] = "|".join(unknown)
    if counted is not None and len(lpns) > counted:
        flags.append("NEEDS_REVIEW")  # can't count fewer pallets than IDs we read

    label_gtins = set(labels.get("gtins") or [])
    row["label_gtins"] = "|".join(sorted(label_gtins))
    row["labels_read"] = len(labels.get("labels") or [])
    if len(label_gtins) > 1 or vss_parsed.get("mixed_product_suspected"):
        flags.append("MIXED_SKU")

    if wms and label_gtins and rc["compare_sku_via"] != "none":
        ref = wms["gtins"] if rc["compare_sku_via"] == "gtin" else {s.zfill(14) for s in wms["skus"]}
        if ref and not label_gtins <= ref:
            flags.append("SKU_MISMATCH")

    issues = vss_parsed.get("issues") or []
    row["issues_count"] = len(issues)
    if issues:
        flags.append("CONDITION")
        row["max_severity"] = max(
            (i.get("severity", "low") for i in issues), key=lambda s: SEVERITY_RANK.get(s, 0)
        )

    flags = list(dict.fromkeys(flags)) or ["MATCH"]
    row["flags"] = "|".join(flags)
    row["status"] = next(s for s in STATUS_PRIORITY if s in flags)
    return row


def reconcile(wms: dict[str, dict], results: dict[str, dict], cfg: dict) -> tuple[list, list]:
    lpn_index = build_lpn_index(wms)
    rows = [
        _reconcile_lane(lane, wms.get(lane), results.get(lane), cfg, lpn_index)
        for lane in sorted(set(wms) | set(results))
    ]
    issues = []
    for lane, res in results.items():
        for i in ((res.get("vss") or {}).get("parsed") or {}).get("issues") or []:
            issues.append({"lane_id": lane, "video": res.get("video", ""), **i})
    issues.sort(key=lambda i: (-SEVERITY_RANK.get(i.get("severity"), 0), i["lane_id"]))
    return rows, issues


def write_csv(rows: list[dict], path: Path) -> None:
    if not rows:
        path.write_text("")
        return
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def summarize(rows: list[dict], issues: list[dict]) -> dict:
    flown = [r for r in rows if r["status"] != "NOT_FLOWN"]
    in_wms = [r for r in rows if r["in_wms"]]
    return {
        "lanes_in_wms": len(in_wms),
        "lanes_flown": len(flown),
        "lanes_match": sum(r["status"] == "MATCH" for r in rows),
        "count_mismatches": sum("COUNT_MISMATCH" in r["flags"] for r in rows),
        "sku_exceptions": sum(
            any(f in r["flags"] for f in ("SKU_MISMATCH", "MIXED_SKU", "UNKNOWN_PALLET"))
            for r in rows
        ),
        "misplaced_pallets": sum(len(r["misplaced"].split("|")) for r in rows if r["misplaced"]),
        "location_mismatches": sum("LOCATION_MISMATCH" in r["flags"] for r in rows),
        "pallet_ids_read": sum(r["pallet_ids_read"] for r in rows),
        "pallet_ids_confirmed": sum(r["pallet_ids_confirmed"] for r in rows),
        "needs_review": sum("NEEDS_REVIEW" in r["flags"] for r in rows),
        "not_flown": sum(r["status"] == "NOT_FLOWN" for r in rows),
        "pallets_expected": sum(r["wms_pallets"] for r in flown),
        "pallets_counted": sum(r["counted_pallets"] or 0 for r in flown),
        "issues_high": sum(i.get("severity") == "high" for i in issues),
        "issues_medium": sum(i.get("severity") == "medium" for i in issues),
        "issues_low": sum(i.get("severity") == "low" for i in issues),
    }
