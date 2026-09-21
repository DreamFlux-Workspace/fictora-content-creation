"""Human-readable harness artefacts (STORY.md, dialogue extraction)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence


def lines_from_spine(spine: Mapping[str, Any]) -> list[str]:
    """Flatten dialogue lines from a story spine JSON payload.

    Parameters
    ----------
    spine
        Serialized story spine from the drama API.

    Returns
    -------
    list[str]
        ``Speaker: text`` strings in beat order.
    """
    names = {c.get("cast_id"): c.get("name") for c in spine.get("cast") or [] if isinstance(c, dict)}
    out: list[str] = []

    def append_from_beats(beats: Any) -> None:
        if not isinstance(beats, list):
            return
        for beat in beats:
            if not isinstance(beat, dict):
                continue
            dialogue = beat.get("dialogue") or beat.get("lines") or beat.get("dialogue_lines") or []
            if isinstance(dialogue, str):
                dialogue = [dialogue]
            for line in dialogue:
                if isinstance(line, dict):
                    who = (
                        line.get("speaker")
                        or line.get("who")
                        or line.get("character")
                        or names.get(line.get("cast_id"))
                        or "?"
                    )
                    text = line.get("text") or line.get("line") or ""
                    if str(text).strip():
                        out.append(f"{who}: {text}")
                elif isinstance(line, str) and line.strip():
                    out.append(line)

    append_from_beats(spine.get("beats"))
    episodes = spine.get("episodes") or []
    if isinstance(episodes, list):
        for episode in episodes:
            if isinstance(episode, dict):
                append_from_beats(episode.get("beats"))
    return out


def video_urls_from_payloads(
    *,
    video: Mapping[str, Any] | None = None,
    delivery: Mapping[str, Any] | None = None,
) -> list[str]:
    """Collect episode video URLs from a terminal video job or delivery payload.

    Parameters
    ----------
    video
        Terminal ``/v1/video-generations/{id}`` JSON when available.
    delivery
        ``/v1/video-generations/{id}/delivery`` JSON when available.

    Returns
    -------
    list[str]
        De-duplicated URLs in encounter order.
    """
    seen: set[str] = set()
    urls: list[str] = []

    def add(url: Any) -> None:
        if not isinstance(url, str) or not url.strip():
            return
        if url in seen:
            return
        seen.add(url)
        urls.append(url)

    output = (video or {}).get("output") if isinstance(video, dict) else None
    if isinstance(output, dict):
        for episode in output.get("episodes") or []:
            if isinstance(episode, dict):
                for key in ("video_url", "final_video_url", "url"):
                    if episode.get(key):
                        add(episode[key])
                        break
    if isinstance(delivery, dict):
        for episode in delivery.get("episodes") or []:
            if isinstance(episode, dict):
                for key in ("video_url", "final_video_url", "url"):
                    if episode.get(key):
                        add(episode[key])
                        break
    return urls


def write_story_markdown(
    path: Path,
    *,
    title: str,
    meta_lines: Sequence[str],
    logline: str,
    cast_names: Sequence[str],
    dialogue_lines: Sequence[str],
    video_urls: Sequence[str],
    footer: str = "",
) -> None:
    """Write a harness-style STORY.md summary next to JSON artefacts.

    Parameters
    ----------
    path
        Output file path (typically ``run_dir / \"STORY.md\"``).
    title
        Story title heading.
    meta_lines
        Markdown lines after the title (genre, lane, spine id, etc.).
    logline
        Short story summary.
    cast_names
        Character display names.
    dialogue_lines
        Preformatted dialogue bullets.
    video_urls
        Final episode video URLs when known.
    footer
        Optional trailing note (e.g. pointers to raw JSON files).
    """
    md = [
        f"# {title}",
        "",
        *meta_lines,
        "",
        "## Logline",
        "",
        logline,
        "",
        "## Cast",
        "",
        *[f"- {name}" for name in cast_names],
        "",
        "## Dialogue",
        "",
        *[f"- {line}" for line in dialogue_lines],
        "",
        "## Episode video",
        "",
        *[f"- {url}" for url in video_urls],
        "",
    ]
    if footer.strip():
        md.extend([footer.strip(), ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(md), encoding="utf-8")
