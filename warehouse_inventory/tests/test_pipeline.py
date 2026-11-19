import json

import cv2
import numpy as np
import zxingcpp

from warehouse_inventory.config import DEFAULTS, normalize_lane_id
from warehouse_inventory.cv_count import count_tracks
from warehouse_inventory.labels import parse_gs1, read_labels
from warehouse_inventory.reconcile import load_wms, reconcile
from warehouse_inventory.vss import parse_json_block


def test_parse_gs1_hri_and_raw():
    hri = parse_gs1("(00)006141410000000017(02)10614141000415(37)40")
    assert hri == {"sscc": "006141410000000017", "content_gtin": "10614141000415", "count": "40"}
    raw = parse_gs1("]C100006141410000000017\x1d0210614141000415" + "3740")
    assert raw["sscc"] == "006141410000000017"
    assert raw["content_gtin"] == "10614141000415"
    assert raw["count"] == "40"


def test_normalize_lane_id():
    assert normalize_lane_id(" a03_l14 ") == "A03-L14"


def test_count_tracks_drops_short_tracks():
    frames = []
    for f in range(20):
        objs = [{"id": 1, "type": "pallet", "conf": 0.9}]
        if f >= 5:
            objs.append({"id": 2, "type": "pallet", "conf": 0.8})
        if f == 10:
            objs.append({"id": 3, "type": "pallet", "conf": 0.9})  # 1-frame ID switch
        if f < 10:
            objs.append({"id": 4, "type": "person", "conf": 0.9})
        frames.append({"frameNo": f, "timestamp": f, "objects": objs})
    r = count_tracks(frames, ["pallet"], min_track_frames=6)
    assert r["cv_pallet_count"] == 2
    assert r["tracks_dropped_short"] == 1
    assert r["track_ids_in_order"] == [1, 2]


def test_parse_json_block_tolerates_fences():
    assert parse_json_block('Here:\n```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_block("no json") is None


def test_read_labels_from_video(tmp_path):
    vid = tmp_path / "A01-L01.mp4"
    vw = cv2.VideoWriter(str(vid), cv2.VideoWriter_fourcc(*"mp4v"), 10, (1280, 720))
    for gtin in ["10614141000415", "10614141000422"]:
        bc = zxingcpp.create_barcode(f"(00)006141410000000017(02){gtin}", zxingcpp.BarcodeFormat.Code128)
        lab = cv2.cvtColor(np.pad(np.array(bc.to_image(scale=1)), 20, constant_values=255), cv2.COLOR_GRAY2BGR)
        for _ in range(10):
            fr = np.full((720, 1280, 3), 50, np.uint8)
            h, w = lab.shape[:2]
            fr[300 : 300 + h, 100 : 100 + w] = lab
            vw.write(fr)
    vw.release()
    r = read_labels(vid, sample_fps=5)
    assert r["gtins"] == ["10614141000415", "10614141000422"]
    assert r["labels"][0]["first_seen_s"] < r["labels"][1]["first_seen_s"]


def test_reconcile_statuses(tmp_path):
    csv_path = tmp_path / "wms.csv"
    csv_path.write_text(
        "Loc,Item,GTIN,Qty\n"
        "A01-L01-01,SKU1,10614141000415,10\n"
        "A01-L01-02,SKU1,10614141000415,9\n"
        "A01-L02-01,SKU2,10614141000422,5\n"
        "A01-L03-01,SKU3,10614141000439,4\n"
    )
    cfg = json.loads(json.dumps(DEFAULTS))
    cfg["wms"]["columns"].update(location="Loc", sku="Item", gtin="GTIN", pallets="Qty")
    cfg["wms"]["lane_regex"] = r"^(.*)-\d{2}$"
    wms = load_wms(csv_path, cfg["wms"])
    assert wms["A01-L01"]["pallets"] == 19

    def lane(cv, vlm, gtins, issues=()):
        return {"cv": {"cv_pallet_count": cv},
                "vss": {"parsed": {"vlm_pallet_estimate": vlm, "issues": list(issues)}},
                "labels": {"gtins": gtins, "labels": gtins, "ssccs": []}}

    results = {
        "A01-L01": lane(19, 19, ["10614141000415"]),
        "A01-L02": lane(4, 4, ["10614141000422"]),
        "A01-L04": lane(3, 3, []),
    }
    rows, _ = reconcile(wms, results, cfg)
    status = {r["lane_id"]: r["status"] for r in rows}
    assert status == {
        "A01-L01": "MATCH",
        "A01-L02": "COUNT_MISMATCH",
        "A01-L03": "NOT_FLOWN",
        "A01-L04": "NOT_IN_WMS",
    }

    results["A01-L01"] = lane(19, 23, ["10614141000415"])
    rows, _ = reconcile(wms, results, cfg)
    assert next(r for r in rows if r["lane_id"] == "A01-L01")["status"] == "NEEDS_REVIEW"

    results["A01-L01"] = lane(19, 19, ["10614141000415", "10614141000439"])
    rows, _ = reconcile(wms, results, cfg)
    assert next(r for r in rows if r["lane_id"] == "A01-L01")["status"] == "MIXED_SKU"
