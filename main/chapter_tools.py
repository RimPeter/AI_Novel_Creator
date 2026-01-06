from __future__ import annotations

import json
import re
from typing import Any


def _split_sentences(text: str) -> list[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    parts = re.split(r"(?<=[.!?])\s+|\n{2,}", raw)
    return [p.strip() for p in parts if p.strip()]


def structurize_chapter(*, chapter_title: str, chapter_summary: str, scenes: int = 3) -> str:
    sentences = _split_sentences(chapter_summary)
    scene_count = max(1, min(12, int(scenes)))

    groups: list[list[str]] = [[] for _ in range(scene_count)]
    for idx, sentence in enumerate(sentences):
        groups[idx % scene_count].append(sentence)

    default_beats = [
        "Goal: what does the protagonist want right now?",
        "Conflict: what blocks them (person/clock/secret)?",
        "Turn: what new information forces a choice?",
        "Outcome: what changes going into the next scene?",
    ]

    scene_items: list[dict[str, Any]] = []
    for i in range(scene_count):
        scene_summary = " ".join(groups[i]).strip() or (chapter_summary or "").strip()
        scene_items.append(
            {
                "index": i + 1,
                "title": f"Scene {i + 1}",
                "summary": scene_summary,
                "pov": "",
                "location": "",
                "beats": default_beats,
            }
        )

    structure = {
        "schema_version": 1,
        "chapter_title": (chapter_title or "").strip(),
        "chapter_summary": (chapter_summary or "").strip(),
        "scenes": scene_items,
    }

    return json.dumps(structure, indent=2, ensure_ascii=False)


def parse_structure_json(raw: str) -> dict[str, Any]:
    if not (raw or "").strip():
        raise ValueError("Structure JSON is empty.")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Structure JSON must be an object.")
    scenes = data.get("scenes")
    if scenes is None:
        raise ValueError("Structure JSON must include a 'scenes' key.")
    if not isinstance(scenes, list):
        raise ValueError("'scenes' must be a list.")
    return data


def render_from_structure(structure: dict[str, Any]) -> str:
    chapter_title = (structure.get("chapter_title") or "").strip()
    scenes = structure.get("scenes") or []

    chunks: list[str] = []
    if chapter_title:
        chunks.append(chapter_title.strip())

    for idx, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict):
            continue

        title = (scene.get("title") or "").strip() or f"Scene {idx}"
        summary = (scene.get("summary") or "").strip()
        pov = (scene.get("pov") or "").strip()
        location = (scene.get("location") or "").strip()
        beats = [str(b).strip() for b in (scene.get("beats") or []) if str(b).strip()]

        meta = " — ".join(part for part in [location, f"POV: {pov}" if pov else ""] if part)
        scene_header = f"{title}{(' — ' + meta) if meta else ''}"

        paragraphs: list[str] = [scene_header]
        if summary:
            paragraphs.append(summary)
        if beats:
            paragraphs.append(
                " ".join(
                    [
                        "It begins with a clear want and a pressure point.",
                        "The situation tightens through action and reaction.",
                        "A turn forces a choice, and the choice leaves a consequence that points into the next beat.",
                    ]
                )
            )

        paragraphs.append(
            "Replace this placeholder with full prose (sensory detail, character voice, and concrete action)."
        )

        chunks.append("\n\n".join(paragraphs))

    return ("\n\n***\n\n".join(chunks)).strip() + "\n"
