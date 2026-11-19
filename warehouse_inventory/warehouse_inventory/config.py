"""Config loading with defaults. Everything a site needs to change lives in config.yaml."""

from __future__ import annotations

import copy
import re
from pathlib import Path

import yaml

DEFAULTS: dict = {
    "wms": {
        "csv": "wms_export.csv",
        # Map our field names -> your WMS CSV header names. null = column not present.
        "columns": {
            "location": "Location",
            "sku": "SKU",
            "pallets": "Pallets",  # pallet quantity per location
            "units": None,  # unit quantity (used with units_per_pallet if pallets is null)
            "units_per_pallet": None,
            "lpn": None,  # pallet license plate / SSCC, if WMS tracks per pallet
            "gtin": None,  # GTIN on the label, if WMS export has it
        },
        # If WMS locations are per position (A03-L14-07) and videos are per lane (A03-L14),
        # capture the lane part with group 1. null = location already equals lane id.
        "lane_regex": None,
    },
    "videos": {"dir": "videos", "extensions": [".mp4", ".mov", ".mkv", ".avi"]},
    "output_dir": "out",
    "labels": {
        "sample_fps": 4.0,  # frames per second scanned for barcodes
        "max_width": 3840,  # downscale frames wider than this before decoding
        # Full-match regexes that tell barcode types apart.
        "patterns": {
            "location": r"[A-Z]-\d{3}-\d{4}-\d{2}",  # rack location label, e.g. H-033-0054-10
            "lpn": r"\d{10}",  # pallet license plate, e.g. 7700593756
        },
    },
    "vss": {
        "url": "http://localhost:8100",
        "model": None,  # null = first model returned by GET /models
        "chunk_duration": 10,
        "num_frames_per_chunk": 0,  # 0 = VSS default
        "enable_cv": True,
        "cv_prompt": "pallet;0.4",
        "max_tokens": 1024,
        "temperature": 0.2,
        "timeout_s": 1800,
        # Directory where <request_id>_fused.json CV files are copied from the VSS container.
        "cv_json_dir": "out/cv",
    },
    "counting": {
        "object_types": ["pallet"],
        "min_track_frames": 6,  # tracks shorter than this are treated as noise / ID switches
        "min_confidence": 0.35,
    },
    "reconcile": {
        "count_tolerance": 0,  # pallets; 0 = exact match required
        "cross_check_tolerance": 1,  # CV vs VLM count disagreement that triggers NEEDS_REVIEW
        "compare_sku_via": "none",  # gtin | sku | none (case GTIN vs WMS, if WMS has GTIN)
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | Path | None) -> dict:
    cfg = DEFAULTS
    if path:
        with open(path) as f:
            cfg = _merge(DEFAULTS, yaml.safe_load(f) or {})
    return cfg


def normalize_lane_id(raw: str) -> str:
    """Canonical lane key: uppercase, no whitespace, '_' and '.' treated as '-'."""
    s = re.sub(r"\s+", "", str(raw)).upper()
    return re.sub(r"[_.]", "-", s)
