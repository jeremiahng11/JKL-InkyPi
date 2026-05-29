"""Rolling history of recently-displayed images.

Hooked into ``refresh_task`` so every time the panel actually redraws
(i.e. the new image differs from the previous one — refresh_task's
hash check has already filtered out duplicates) a PNG copy is saved
under ``static/images/history/`` with a JSON sidecar describing what
ran. The companion app pulls the list via /api/display/history and
shows a horizontally-scrolling strip of thumbnails under the live
preview.

Storage budget: keep the most-recent ``MAX_ENTRIES`` files. Older
ones are pruned eagerly so the directory size stays bounded on a Pi
Zero 2 W (~500KB per PNG × 20 entries ≈ 10MB).
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

MAX_ENTRIES = 20


def history_dir(device_config) -> str:
    """static/images/history/. Created on first call."""
    base = os.path.dirname(device_config.current_image_file)
    path = os.path.join(base, "history")
    os.makedirs(path, exist_ok=True)
    return path


def record(device_config, image, *, plugin_id: Optional[str],
           plugin_instance: Optional[str], playlist: Optional[str]) -> None:
    """Save ``image`` (PIL Image) under a fresh timestamped basename
    and write a sidecar with the producing plugin / instance /
    playlist. Failures are swallowed — history is opportunistic, not
    a load-bearing system."""
    try:
        directory = history_dir(device_config)
        ts = int(time.time() * 1000)  # ms — round-tripped to the app
        basename = f"{ts}"
        image.save(os.path.join(directory, f"{basename}.png"))
        meta = {
            "timestamp_ms":     ts,
            "plugin_id":        plugin_id,
            "plugin_instance":  plugin_instance,
            "playlist":         playlist,
        }
        with open(os.path.join(directory, f"{basename}.json"), "w") as f:
            json.dump(meta, f)
        _prune(directory)
    except Exception:
        logger.exception("display_history.record failed")


def list_entries(device_config) -> list[dict]:
    """Return the latest MAX_ENTRIES history records, newest first.
    Each dict has timestamp_ms / plugin_id / plugin_instance /
    playlist / image_path (filesystem path), suitable for /api
    serialisation after stripping image_path to a basename."""
    directory = history_dir(device_config)
    entries: list[dict] = []
    for name in os.listdir(directory):
        if not name.endswith(".json"):
            continue
        stem = name[:-5]  # drop .json
        png = os.path.join(directory, f"{stem}.png")
        if not os.path.isfile(png):
            continue
        try:
            with open(os.path.join(directory, name)) as f:
                meta = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        meta["image_path"] = png
        meta["id"] = stem
        entries.append(meta)
    entries.sort(key=lambda e: e.get("timestamp_ms", 0), reverse=True)
    return entries[:MAX_ENTRIES]


def find_entry(device_config, entry_id: str) -> Optional[dict]:
    """Return the metadata dict for one history entry, or None."""
    directory = history_dir(device_config)
    json_path = os.path.join(directory, f"{entry_id}.json")
    png_path = os.path.join(directory, f"{entry_id}.png")
    if not (os.path.isfile(json_path) and os.path.isfile(png_path)):
        return None
    try:
        with open(json_path) as f:
            meta = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    meta["image_path"] = png_path
    meta["id"] = entry_id
    return meta


def _prune(directory: str) -> None:
    """Keep the most-recent MAX_ENTRIES (png + sidecar pairs).
    Anything older is deleted. Run after every record() so the
    rolling window stays bounded."""
    pairs: list[tuple[int, str]] = []
    for name in os.listdir(directory):
        if not name.endswith(".png"):
            continue
        stem = name[:-4]
        try:
            ts = int(stem)
        except ValueError:
            continue
        pairs.append((ts, stem))
    if len(pairs) <= MAX_ENTRIES:
        return
    pairs.sort(reverse=True)
    for _, stem in pairs[MAX_ENTRIES:]:
        for ext in (".png", ".json"):
            path = os.path.join(directory, f"{stem}{ext}")
            try:
                os.remove(path)
            except OSError:
                pass
