"""CLI: python -m warehouse_inventory <labels|vss|cv|reconcile|run> -c config.yaml"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load_config, normalize_lane_id


def _videos(cfg: dict) -> list[Path]:
    exts = {e.lower() for e in cfg["videos"]["extensions"]}
    vids = sorted(p for p in Path(cfg["videos"]["dir"]).iterdir() if p.suffix.lower() in exts)
    if not vids:
        sys.exit(f"no videos found in {cfg['videos']['dir']}")
    return vids


def _lane_path(cfg: dict, lane_id: str) -> Path:
    d = Path(cfg["output_dir"]) / "lanes"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{lane_id}.json"


def _update_lane(cfg: dict, video: Path, key: str, value: dict) -> None:
    lane_id = normalize_lane_id(video.stem)
    p = _lane_path(cfg, lane_id)
    rec = json.loads(p.read_text()) if p.exists() else {"lane_id": lane_id, "video": video.name}
    rec[key] = value
    p.write_text(json.dumps(rec, indent=2))


def cmd_labels(cfg: dict) -> None:
    from .labels import read_labels

    for v in _videos(cfg):
        lc = cfg["labels"]
        res = read_labels(v, lc["sample_fps"], lc["max_width"], lc["patterns"])
        _update_lane(cfg, v, "labels", res)
        print(
            f"{v.name}: location={res['locations'] or '-'} pallet IDs={len(res['lpns'])} "
            f"GTINs={res['gtins'] or '-'}"
        )


def cmd_vss(cfg: dict) -> None:
    from .vss import VSSClient, analyze_lane

    client = VSSClient(cfg["vss"])
    for v in _videos(cfg):
        print(f"{v.name}: uploading + summarizing ...", flush=True)
        res = analyze_lane(client, v)
        _update_lane(cfg, v, "vss", res)
        p = res["parsed"] or {}
        print(
            f"  request_id={res['request_id']} parse_ok={res['parse_ok']} "
            f"vlm_estimate={p.get('vlm_pallet_estimate')} issues={len(p.get('issues') or [])}"
        )


def cmd_cv(cfg: dict) -> None:
    from .cv_count import count_from_file

    cv_dir = Path(cfg["vss"]["cv_json_dir"])
    for p in sorted((Path(cfg["output_dir"]) / "lanes").glob("*.json")):
        rec = json.loads(p.read_text())
        rid = (rec.get("vss") or {}).get("request_id")
        f = cv_dir / f"{rid}_fused.json" if rid else None
        if not f or not f.exists():
            print(f"{rec['lane_id']}: no CV file ({f}) - copy it out of the VSS container")
            continue
        rec["cv"] = count_from_file(f, cfg["counting"])
        p.write_text(json.dumps(rec, indent=2))
        print(f"{rec['lane_id']}: {rec['cv']['cv_pallet_count']} pallets (tracker)")


def cmd_reconcile(cfg: dict) -> None:
    from . import reconcile as rc
    from .report import render

    out = Path(cfg["output_dir"])
    wms = rc.load_wms(cfg["wms"]["csv"], cfg["wms"])
    results = rc.load_lane_results(out / "lanes")
    rows, issues = rc.reconcile(wms, results, cfg)
    summary = rc.summarize(rows, issues)
    rc.write_csv(rows, out / "reconciliation.csv")
    rc.write_csv(issues, out / "issues.csv")
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    (out / "report.html").write_text(render(rows, issues, summary, cfg.get("report_title", "Lane Inventory Report")))
    print(json.dumps(summary, indent=2))
    print(f"wrote {out}/reconciliation.csv, issues.csv, report.html")


def main() -> None:
    ap = argparse.ArgumentParser(prog="warehouse_inventory")
    ap.add_argument("command", choices=["labels", "vss", "cv", "reconcile", "run"])
    ap.add_argument("-c", "--config", default="config.yaml")
    a = ap.parse_args()
    cfg = load_config(a.config if Path(a.config).exists() else None)
    if a.command == "run":
        cmd_labels(cfg)
        cmd_vss(cfg)
        cmd_cv(cfg)
        cmd_reconcile(cfg)
    else:
        {"labels": cmd_labels, "vss": cmd_vss, "cv": cmd_cv, "reconcile": cmd_reconcile}[a.command](cfg)


if __name__ == "__main__":
    main()
