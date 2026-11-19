"""Read pallet labels (GS1-128 / SSCC, QR, DataMatrix) from lane flight videos.

Scans frames at `sample_fps`, decodes every barcode, dedupes, and parses GS1 application
identifiers. Order of first sighting ~= position along the lane (front -> back).
"""

from __future__ import annotations

import re
from pathlib import Path

import cv2
import zxingcpp

# GS1 AIs that matter on pallet labels. value = fixed length or None (variable, GS-terminated).
GS1_AIS = {
    "00": ("sscc", 18),
    "01": ("gtin", 14),
    "02": ("content_gtin", 14),
    "10": ("batch", None),
    "11": ("prod_date", 6),
    "13": ("pack_date", 6),
    "15": ("best_before", 6),
    "17": ("expiry", 6),
    "21": ("serial", None),
    "37": ("count", None),
    "400": ("po_number", None),
}

_PAREN_RE = re.compile(r"\((\d{2,4})\)([^(]*)")
GS = "\x1d"


def parse_gs1(text: str) -> dict:
    """Parse GS1 element strings in HRI '(00)...(02)...' or raw FNC1/GS-separated form."""
    fields: dict = {}
    if text.startswith("("):
        for ai, val in _PAREN_RE.findall(text):
            if ai in GS1_AIS:
                fields[GS1_AIS[ai][0]] = val.strip()
        return fields

    raw = text.replace("<GS>", GS)
    for prefix in ("]C1", "]d2", "]Q3", "]e0"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
    raw = raw.lstrip(GS)
    i = 0
    while i < len(raw):
        for ai_len in (2, 3):
            ai = raw[i : i + ai_len]
            if ai in GS1_AIS:
                name, length = GS1_AIS[ai]
                start = i + ai_len
                if length:
                    end = start + length
                else:
                    end = raw.find(GS, start)
                    end = len(raw) if end == -1 else end
                fields[name] = raw[start:end]
                i = end + (1 if end < len(raw) and raw[end] == GS else 0)
                break
        else:
            break  # not GS1 / unknown AI: stop, keep what we have
    return fields


def _decode_frame(gray, upscale_below: int = 2600):
    """Decode a frame, escalating only when needed (each pass is slower than the last)."""
    found = {b.text: b for b in zxingcpp.read_barcodes(gray)}
    # Dark lanes: local contrast boost often rescues a read.
    enhanced = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(gray)
    for b in zxingcpp.read_barcodes(enhanced):
        found.setdefault(b.text, b)
    # Small case labels need ~2px per bar; upscale sub-4K frames once.
    if gray.shape[1] < upscale_below:
        big = cv2.resize(enhanced, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        for b in zxingcpp.read_barcodes(big):
            found.setdefault(b.text, b)
    return list(found.values())


def classify(text: str, patterns: dict) -> str:
    """location | lpn | gs1 | other. Patterns come from config labels.patterns."""
    if patterns.get("location") and re.fullmatch(patterns["location"], text):
        return "location"
    if patterns.get("lpn") and re.fullmatch(patterns["lpn"], text):
        return "lpn"
    if parse_gs1(text):
        return "gs1"
    return "other"


def read_labels(
    video_path: str | Path,
    sample_fps: float = 4.0,
    max_width: int = 3840,
    patterns: dict | None = None,
) -> dict:
    patterns = patterns or {}
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, round(fps / sample_fps))
    seen: dict[str, dict] = {}
    frame_idx = scanned = 0

    while True:
        ok = cap.grab()
        if not ok:
            break
        if frame_idx % step == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            scanned += 1
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if gray.shape[1] > max_width:
                scale = max_width / gray.shape[1]
                gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            t = frame_idx / fps
            for bc in _decode_frame(gray):
                key = bc.text
                rec = seen.setdefault(
                    key,
                    {
                        "text": bc.text,
                        "format": str(bc.format).split(".")[-1],
                        "kind": classify(bc.text, patterns),
                        "gs1": parse_gs1(bc.text),
                        "first_seen_s": round(t, 2),
                        "last_seen_s": round(t, 2),
                        "hits": 0,
                    },
                )
                rec["last_seen_s"] = round(t, 2)
                rec["hits"] += 1
        frame_idx += 1
    cap.release()

    labels = sorted(seen.values(), key=lambda r: r["first_seen_s"])
    gtins = sorted({l["gs1"].get("gtin") or l["gs1"].get("content_gtin") for l in labels} - {None})
    ssccs = [l["gs1"]["sscc"] for l in labels if "sscc" in l["gs1"]]
    return {
        "frames_scanned": scanned,
        "duration_s": round(frame_idx / fps, 2),
        "labels": labels,
        "locations": [l["text"] for l in labels if l["kind"] == "location"],
        # Pallet IDs in order of first sighting ~= front-to-back position in the lane.
        "lpns": [l["text"] for l in labels if l["kind"] == "lpn"] + ssccs,
        "gtins": gtins,
        "ssccs": ssccs,
    }
