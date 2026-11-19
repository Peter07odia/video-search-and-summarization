"""Minimal client for the VSS REST API (/files, /summarize, /models)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import requests

from . import prompts


class VSSClient:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.url = cfg["url"].rstrip("/")
        self._model = cfg.get("model")

    def model(self) -> str:
        if not self._model:
            r = requests.get(f"{self.url}/models", timeout=30)
            r.raise_for_status()
            self._model = r.json()["data"][0]["id"]
        return self._model

    def upload(self, video: Path) -> str:
        with open(video, "rb") as f:
            r = requests.post(
                f"{self.url}/files",
                data={"purpose": "vision", "media_type": "video", "filename": video.name},
                files={"file": (video.name, f, "video/mp4")},
                timeout=600,
            )
        r.raise_for_status()
        return r.json()["id"]

    def delete(self, file_id: str) -> None:
        requests.delete(f"{self.url}/files/{file_id}", timeout=60)

    def summarize(self, file_id: str) -> dict:
        c = self.cfg
        body = {
            "id": file_id,
            "model": self.model(),
            "prompt": prompts.CAPTION_PROMPT,
            "caption_summarization_prompt": prompts.CAPTION_SUMMARIZATION_PROMPT,
            "summary_aggregation_prompt": prompts.SUMMARY_AGGREGATION_PROMPT,
            "chunk_duration": c["chunk_duration"],
            "max_tokens": c["max_tokens"],
            "temperature": c["temperature"],
            "enable_chat": True,  # keeps the lane indexed for Q&A in the VSS UI
        }
        if c.get("num_frames_per_chunk"):
            body["num_frames_per_chunk"] = c["num_frames_per_chunk"]
        if c.get("enable_cv"):
            body["enable_cv_metadata"] = True
            body["cv_pipeline_prompt"] = c["cv_prompt"]
        r = requests.post(f"{self.url}/summarize", json=body, timeout=c["timeout_s"])
        r.raise_for_status()
        return r.json()


def parse_json_block(text: str) -> dict | None:
    """Pull the first JSON object out of an LLM response (tolerates ```json fences / prose)."""
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def analyze_lane(client: VSSClient, video: Path, keep_file: bool = True) -> dict:
    file_id = client.upload(video)
    resp = client.summarize(file_id)
    content = resp["choices"][0]["message"]["content"] if resp.get("choices") else ""
    parsed = parse_json_block(content)
    if not keep_file:
        client.delete(file_id)
    return {
        "file_id": file_id,
        "request_id": resp.get("id"),
        "raw": content,
        "parsed": parsed,
        "parse_ok": parsed is not None,
    }
