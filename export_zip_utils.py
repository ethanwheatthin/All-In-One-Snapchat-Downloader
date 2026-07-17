"""Helpers for processing Snapchat data-export ZIPs directly.

Snapchat delivers large exports as several mydata~*.zip files whose
contents share one layout (memories/, chat_media/, json/, html/ at the
top level). These helpers let the app be pointed at the folder of ZIPs
as downloaded: the ZIPs are merge-extracted into a single
``<folder>/extracted`` root that looks exactly like one extracted
export, which the existing local-files and chat-media pipelines already
understand.

Extraction restores each file's modification time from the ZIP entry
(zipfile does not do this by itself) because the chat-media pipeline
uses the export's file times as a timestamp source. Already-extracted
files with a matching size are skipped, so an interrupted extraction
resumes where it left off.
"""

import logging
import os
import time
import zipfile
from datetime import datetime

# Top-level names that identify a Snapchat export ZIP
EXPORT_TOP_LEVEL_HINTS = ("memories", "chat_media", "json", "html", "index.html")

EXTRACT_DIRNAME = "extracted"

MEMORIES_JSON_SUFFIX = "json/memories_history.json"


def default_extract_root(zip_dir):
    """Where a folder of export ZIPs gets extracted to."""
    return os.path.join(zip_dir, EXTRACT_DIRNAME)


def looks_like_export_zip(zip_path):
    """True if the ZIP's top level matches the Snapchat export layout."""
    try:
        with zipfile.ZipFile(zip_path) as z:
            for name in z.namelist():
                top = name.split("/", 1)[0]
                if top in EXPORT_TOP_LEVEL_HINTS:
                    return True
    except Exception as exc:
        logging.debug(f"Could not inspect ZIP {zip_path}: {exc}")
    return False


def find_export_zips(directory):
    """Return Snapchat export ZIPs directly inside directory (sorted by name)."""
    try:
        entries = sorted(os.listdir(directory))
    except Exception:
        return []
    zips = []
    for entry in entries:
        path = os.path.join(directory, entry)
        if entry.lower().endswith(".zip") and os.path.isfile(path) \
                and looks_like_export_zip(path):
            zips.append(path)
    return zips


def total_zip_size(zip_paths):
    total = 0
    for path in zip_paths:
        try:
            total += os.path.getsize(path)
        except Exception:
            pass
    return total


def format_size(num_bytes):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num_bytes < 1024 or unit == "TB":
            return f"{num_bytes:.1f} {unit}" if unit != "B" else f"{num_bytes} B"
        num_bytes /= 1024


def _safe_target(dest_root_abs, member_name):
    """Resolve a ZIP member to a path inside dest_root, or None (zip-slip)."""
    target = os.path.abspath(os.path.join(dest_root_abs, member_name))
    if target == dest_root_abs or target.startswith(dest_root_abs + os.sep):
        return target
    return None


def _restore_mtime(target, info):
    """Set the extracted file's mtime from the ZIP entry's DOS timestamp."""
    try:
        ts = time.mktime(datetime(*info.date_time).timetuple())
        os.utime(target, (ts, ts))
    except Exception:
        pass  # invalid/pre-1980 timestamp — keep extraction time


def extract_memories_json(zip_paths, dest_root):
    """Extract the export's json/ folder (memories/chat history) if present.

    Cheap enough to run at browse time: the json/ folder is a handful of
    files. Returns the path to the extracted memories_history.json, or
    None if no ZIP contains one.
    """
    json_path = None
    for zip_path in zip_paths:
        try:
            with zipfile.ZipFile(zip_path) as z:
                infos = [i for i in z.infolist()
                         if not i.is_dir() and i.filename.startswith("json/")]
                if not any(i.filename.endswith(MEMORIES_JSON_SUFFIX) for i in infos):
                    continue
                dest_root_abs = os.path.abspath(dest_root)
                for info in infos:
                    target = _safe_target(dest_root_abs, info.filename)
                    if target is None:
                        continue
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with z.open(info) as src, open(target, "wb") as dst:
                        while True:
                            chunk = src.read(1024 * 1024)
                            if not chunk:
                                break
                            dst.write(chunk)
                    _restore_mtime(target, info)
                    if info.filename.endswith(MEMORIES_JSON_SUFFIX):
                        json_path = target
                return json_path
        except Exception as exc:
            logging.warning(f"Could not read json/ from {zip_path}: {exc}")
    return None


def extract_export_zips(zip_paths, dest_root, log=None, progress=None, stop_check=None):
    """Merge-extract every export ZIP into dest_root.

    All ZIPs share one top-level layout, so extracting them into the same
    root yields a single export folder (memories/ and chat_media/ merged
    across ZIPs; file names never collide between ZIPs).

    Args:
        zip_paths: ZIPs to extract.
        dest_root: extraction root (created if missing).
        log: optional callable(str) for user-facing progress lines.
        progress: optional callable(done, total) counting files.
        stop_check: optional callable() -> bool; True aborts cleanly.

    Returns dict with counts: total, extracted, skipped, errors, aborted.
    """
    stats = {"total": 0, "extracted": 0, "skipped": 0, "errors": 0, "aborted": False}
    os.makedirs(dest_root, exist_ok=True)
    dest_root_abs = os.path.abspath(dest_root)

    per_zip_counts = {}
    for zip_path in zip_paths:
        try:
            with zipfile.ZipFile(zip_path) as z:
                count = sum(1 for i in z.infolist() if not i.is_dir())
        except Exception as exc:
            if log:
                log(f"⚠ Skipping unreadable ZIP {os.path.basename(zip_path)}: {exc}")
            per_zip_counts[zip_path] = None
            continue
        per_zip_counts[zip_path] = count
        stats["total"] += count

    done = 0
    for zip_path in zip_paths:
        if per_zip_counts.get(zip_path) is None:
            continue
        if stop_check and stop_check():
            stats["aborted"] = True
            return stats
        if log:
            size = format_size(os.path.getsize(zip_path))
            log(f"📦 Extracting {os.path.basename(zip_path)} "
                f"({size}, {per_zip_counts[zip_path]:,} files)...")
        try:
            with zipfile.ZipFile(zip_path) as z:
                for info in z.infolist():
                    if info.is_dir():
                        continue
                    if stop_check and stop_check():
                        stats["aborted"] = True
                        return stats
                    done += 1
                    target = _safe_target(dest_root_abs, info.filename)
                    if target is None:
                        logging.warning(
                            f"Skipping unsafe ZIP member path: {info.filename}")
                        stats["errors"] += 1
                        continue
                    try:
                        if os.path.isfile(target) \
                                and os.path.getsize(target) == info.file_size:
                            stats["skipped"] += 1
                        else:
                            os.makedirs(os.path.dirname(target), exist_ok=True)
                            with z.open(info) as src, open(target, "wb") as dst:
                                while True:
                                    chunk = src.read(1024 * 1024)
                                    if not chunk:
                                        break
                                    dst.write(chunk)
                            _restore_mtime(target, info)
                            stats["extracted"] += 1
                    except Exception as exc:
                        logging.warning(
                            f"Failed to extract {info.filename} from "
                            f"{os.path.basename(zip_path)}: {exc}")
                        stats["errors"] += 1
                    if progress:
                        progress(done, stats["total"])
        except Exception as exc:
            if log:
                log(f"⚠ Error reading {os.path.basename(zip_path)}: {exc}")
            stats["errors"] += 1
    return stats
