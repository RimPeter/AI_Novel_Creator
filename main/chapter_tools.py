from __future__ import annotations

import json
from typing import Any


def structurize_scene(*, title: str, summary: str, pov: str = "", location: str = "") -> str:
    structure = {
        "schema_version": 1,
        "title": (title or "").strip(),
        "summary": (summary or "").strip(),
        "pov": (pov or "").strip(),
        "location": (location or "").strip(),
        "beats": [
            "Goal: what does the viewpoint character want in this scene?",
            "Conflict: what blocks them (person/clock/secret)?",
            "Turn: what new information forces a choice?",
            "Outcome: what changes by the end of the scene?",
        ],
    }
    return json.dumps(structure, indent=2, ensure_ascii=False)


def parse_scene_structure_json(raw: str) -> dict[str, Any]:
    if not (raw or "").strip():
        raise ValueError("Structure JSON is empty.")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Structure JSON must be an object.")
    if "beats" in data and not isinstance(data["beats"], list):
        raise ValueError("'beats' must be a list.")
    return data


def render_scene_from_structure(structure: dict[str, Any]) -> str:
    title = (structure.get("title") or "").strip() or "Scene"
    summary = (structure.get("summary") or "").strip()
    pov = (structure.get("pov") or "").strip()
    location = (structure.get("location") or "").strip()
    beats = [str(b).strip() for b in (structure.get("beats") or []) if str(b).strip()]

    parts: list[str] = []
    meta = " — ".join(part for part in [location, f"POV: {pov}" if pov else ""] if part)
    parts.append(f"{title}{(' — ' + meta) if meta else ''}")
    if summary:
        parts.append(summary)
    if beats:
        parts.append("Beats: " + " / ".join(beats))
    parts.append("Replace this placeholder with full prose (character voice, concrete action, sensory detail).")
    return ("\n\n".join(parts)).strip() + "\n"
