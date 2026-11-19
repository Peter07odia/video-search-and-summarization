"""Build a self-contained demo modelled on a pallet-shuttle cold store: a synthetic per-pallet WMS
export (location + LPN), rendered lane videos with real barcode labels, and *simulated* VSS/CV
outputs. Then run labels + reconcile.

Run: python -m warehouse_inventory.demo   ->  demo/out/report.html

Label reads are real (barcodes are rendered into the videos and decoded by labels.py).
VSS/CV outputs are simulated (no GPU needed) and marked "synthetic": true.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import cv2
import numpy as np
import zxingcpp

from . import __main__ as cli
from .config import load_config

ROOT = Path("demo")


def lpn(lane_no: int, pos: int) -> str:
    return f"7700{lane_no:03d}{pos:03d}"


def loc(n: int) -> str:
    return f"H-033-{n:04d}-10"


# lane_no: dict(item, wms (pallet count in WMS), cv, vlm, issues, extra)
SCENARIOS = {
    51: dict(item="46058", desc="12x473mL Choc Milk", wms=19, cv=19, vlm=19),
    52: dict(item="46058", desc="12x473mL Choc Milk", wms=19, cv=17, vlm=17),
    53: dict(item="47743", desc="12x240g Med Cheddar Slices", wms=14, cv=14, vlm=14,
             issues=[("torn_wrap", "medium", 31.5, 6, "Stretch wrap torn on top corner of pallet 6, cartons exposed.")]),
    # One pallet that WMS says is in 0055 was put away into 0054.
    54: dict(item="47743", desc="12x240g Med Cheddar Slices", wms=16, cv=17, vlm=17, intruder=(55, 14),
             issues=[("mixed_product", "medium", 44.0, 17, "Last pallet has different carton print (milk, not cheese) and is taller than the rest.")]),
    55: dict(item="46061", desc="12x473mL 2% Milk", wms=14, cv=13, vlm=13, missing_pos=14),
    56: dict(item="46061", desc="12x473mL 2% Milk", wms=10, cv=10, vlm=13),
    57: dict(item="47750", desc="12x200g Mozzarella", wms=18, cv=None),  # not flown
    58: dict(item=None, desc=None, wms=0, cv=6, vlm=6),  # WMS says empty
    59: dict(item="47750", desc="12x200g Mozzarella", wms=19, cv=19, vlm=19,
             issues=[("leaning_load", "high", 58.0, 15, "Load on pallet 15 leaning toward the left rail, top cases shifted."),
                     ("broken_pallet", "medium", 22.0, 4, "Bottom deck board cracked on pallet 4.")]),
    60: dict(item="46058", desc="12x473mL Choc Milk", wms=12, cv=12, vlm=12, label_loc=61),  # flew wrong lane
}
VIDEO_LANES = [51, 54, 60]  # these get rendered videos with real barcodes


def _barcode(text: str, scale: int = 3) -> np.ndarray:
    img = np.array(zxingcpp.create_barcode(text, zxingcpp.BarcodeFormat.Code128).to_image(scale=scale))
    img = cv2.resize(img, None, fx=1.0, fy=0.5, interpolation=cv2.INTER_NEAREST)
    return cv2.cvtColor(np.pad(img, 24, constant_values=255), cv2.COLOR_GRAY2BGR)


def _render_video(path: Path, location_text: str, pallet_ids: list[str], fps: int = 15) -> None:
    w, h = 1920, 1080
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))

    def blank():
        return np.full((h, w, 3), 35, np.uint8)

    def paste(frame, img, cx, cy):
        ih, iw = img.shape[:2]
        x, y = cx - iw // 2, cy - ih // 2
        frame[y : y + ih, x : x + iw] = img

    # Hover at lane entrance: orange location plate with barcode.
    plate = _barcode(location_text)
    for _ in range(fps * 2):
        f = blank()
        cv2.rectangle(f, (560, 340), (1360, 740), (30, 80, 230), -1)
        paste(f, plate, w // 2, h // 2)
        vw.write(f)
    # Fly-in: each pallet face with its LPN label passes by.
    for pid in pallet_ids:
        lab = _barcode(pid, scale=4)
        for k in range(fps):
            f = blank()
            s = 0.8 + 0.2 * k / fps
            pw, ph = int(1400 * s), int(780 * s)
            cv2.rectangle(f, ((w - pw) // 2, (h - ph) // 2), ((w + pw) // 2, (h + ph) // 2), (70, 120, 165), -1)
            paste(f, lab, w // 2, h // 2)
            vw.write(f)
    vw.release()


def build() -> dict:
    (ROOT / "videos").mkdir(parents=True, exist_ok=True)
    out = ROOT / "out"
    (out / "lanes").mkdir(parents=True, exist_ok=True)
    for p in list((out / "lanes").glob("*.json")) + list((ROOT / "videos").glob("*.mp4")):
        p.unlink()

    # WMS: one row per pallet (LPN), like an LPN-tracked WMS location export.
    with open(ROOT / "wms_export.csv", "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["Location", "LPN", "Item", "Description", "Cases"])
        for n, sc in SCENARIOS.items():
            for pos in range(1, sc["wms"] + 1):
                wr.writerow([loc(n), lpn(n, pos), sc["item"], sc["desc"], 80])

    # What is physically in each lane (pallet IDs front -> back).
    physical: dict[int, list[str]] = {}
    for n, sc in SCENARIOS.items():
        if sc.get("cv") is None:
            continue
        ids = [lpn(n, p) for p in range(1, (sc["wms"] or sc["cv"]) + 1)]
        if sc.get("missing_pos"):
            ids.remove(lpn(n, sc["missing_pos"]))
        if sc.get("intruder"):
            ids.append(lpn(*sc["intruder"]))
        if sc["wms"] == 0:
            ids = [f"7700999{p:03d}" for p in range(1, sc["cv"] + 1)]  # pallets WMS doesn't know
        physical[n] = ids[: sc["cv"]] if len(ids) > sc["cv"] else ids

    for n in VIDEO_LANES:
        sc = SCENARIOS[n]
        # Drone reads a subset of pallet IDs in real life; render first 3 + last 2.
        ids = physical[n]
        shown = ids[:3] + ids[-2:] if len(ids) > 5 else ids
        _render_video(ROOT / "videos" / f"{loc(n)}.mp4", loc(sc.get("label_loc", n)), shown)

    cfg_dict = {
        "wms": {
            "csv": str(ROOT / "wms_export.csv"),
            "columns": {"location": "Location", "sku": "Item", "pallets": None, "lpn": "LPN", "gtin": None},
        },
        "videos": {"dir": str(ROOT / "videos")},
        "output_dir": str(out),
        "report_title": "Lane Inventory Report (DEMO DATA)",
    }
    (ROOT / "config.yaml").write_text(json.dumps(cfg_dict, indent=2))  # JSON is valid YAML
    cfg = load_config(ROOT / "config.yaml")

    cli.cmd_labels(cfg)  # real barcode decoding on the rendered videos

    for n, sc in SCENARIOS.items():
        if sc.get("cv") is None:
            continue
        lane = loc(n)
        lp = out / "lanes" / f"{lane}.json"
        rec = json.loads(lp.read_text()) if lp.exists() else {"lane_id": lane, "video": f"{lane}.mp4"}
        if "labels" not in rec:  # simulated partial label reads for lanes without a rendered video
            ids = physical[n]
            rec["labels"] = {"synthetic": True, "labels": [], "locations": [loc(n)],
                             "lpns": ids[:2] + ids[-1:], "gtins": [], "ssccs": []}
        issues = sc.get("issues", [])
        rec["cv"] = {"cv_pallet_count": sc["cv"], "synthetic": True}
        rec["vss"] = {
            "synthetic": True,
            "parsed": {
                "location_label_text": None,
                "vlm_pallet_estimate": sc["vlm"],
                "lane_full_to_back": True,
                "mixed_product_suspected": any(t == "mixed_product" for t, *_ in issues),
                "issues": [
                    {"type": t, "severity": sev, "approx_time_s": ts,
                     "pallet_position_from_front": pos, "description": d}
                    for t, sev, ts, pos, d in issues
                ],
                "summary": issues[0][4] if issues else "No condition issues observed (simulated).",
            },
        }
        lp.write_text(json.dumps(rec, indent=2))

    cli.cmd_reconcile(cfg)
    return cfg


if __name__ == "__main__":
    build()
