"""Count pallets from the VSS CV pipeline output (Grounding DINO detections + NvDCF tracker).

VSS writes `<request_id>_fused.json` inside its container: a list of frames, each with
`objects: [{id, type, conf, bbox, ...}]`. A pallet = one tracker ID that persists for at least
`min_track_frames` frames. Short tracks are dropped: they are usually ID switches when the
drone moves fast or a load is half out of frame.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path


def count_tracks(
    frames: list[dict],
    object_types: list[str],
    min_track_frames: int = 6,
    min_confidence: float = 0.35,
) -> dict:
    wanted = {t.lower() for t in object_types}
    hits: dict[int, int] = defaultdict(int)
    first_ts: dict[int, float] = {}
    for fr in frames:
        ts = fr.get("timestamp", fr.get("frameNo", 0))
        for obj in fr.get("objects", []):
            otype = str(obj.get("type", obj.get("label", ""))).lower()
            if wanted and otype not in wanted:
                continue
            if float(obj.get("conf", 1.0)) < min_confidence:
                continue
            oid = int(obj["id"])
            hits[oid] += 1
            first_ts.setdefault(oid, ts)

    kept = sorted((oid for oid, n in hits.items() if n >= min_track_frames), key=first_ts.get)
    return {
        "cv_pallet_count": len(kept),
        "tracks_total": len(hits),
        "tracks_dropped_short": len(hits) - len(kept),
        "track_ids_in_order": kept,
    }


def count_from_file(path: str | Path, counting_cfg: dict) -> dict:
    with open(path) as f:
        frames = json.load(f)
    return count_tracks(
        frames,
        counting_cfg["object_types"],
        counting_cfg["min_track_frames"],
        counting_cfg["min_confidence"],
    )
