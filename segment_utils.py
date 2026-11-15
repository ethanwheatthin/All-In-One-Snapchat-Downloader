"""Matching export files to memories_history.json and finding split recordings.

Snapchat used to cap a snap at ~10 s, so a longer recording was saved as a
run of separate memories whose JSON dates sit exactly 10 s apart (issues #47,
#53). Each export file carries its exact capture second in the ZIP entry
time, which is how files are paired with JSON entries.

Pure logic only (no tkinter / ffmpeg) so it can be unit tested.
"""

from datetime import datetime

# Real segments start 10 s apart; allow 1 s of slack for rounding.
SEGMENT_GAP_MIN = 9
SEGMENT_GAP_MAX = 11
# A non-final segment is a full ~10 s clip (observed 9.8-10.6 s). Anything
# longer is a separate, longer snap that merely started close by.
SEGMENT_MAX_DURATION = 11.5
# A non-final segment must last about as long as the gap to the next one,
# otherwise the next clip overlaps it (duplicate / separate save).
SEGMENT_DURATION_SLACK = 1.5

# Offsets to try, nearest first. ZIP/DOS times round down to 2 s, so the
# file time sits 0-1 s *before* the true time: +0, +1 are the likeliest.
MATCH_DELTAS = (0, 1, -1, 2, -2, 3, -3)

VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".avi", ".mkv")


def parse_json_date(date_str):
    return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S UTC")


def media_type_for(fname):
    """JSON 'Media Type' a file of this name should match."""
    lower = fname.lower()
    return "Video" if lower.endswith(VIDEO_EXTS) else "Image"


def pick_json_entry(json_by_ts, utc_ts, media_type, claimed):
    """Nearest unclaimed JSON entry of the right media type within ±3 s.

    json_by_ts maps an int timestamp to a list of JSON items with that time
    (several memories can share a second). claimed is a set of
    item["_json_idx"] already given to another file; the chosen entry is
    added to it so two files never receive the same entry. Returns the item
    or None.
    """
    fallback = None
    for delta in MATCH_DELTAS:
        for item in json_by_ts.get(utc_ts + delta, ()):
            if item.get("_json_idx") in claimed:
                continue
            if item.get("Media Type", media_type) == media_type:
                claimed.add(item.get("_json_idx"))
                return item
            if fallback is None:
                fallback = item
    # Only a wrong-typed entry is near: better than nothing, but don't let it
    # steal a slot from a file of the right type later.
    return fallback


def find_segment_groups(json_items):
    """Group JSON video entries that look like one split recording.

    Consecutive Video entries at the same Location whose dates are
    SEGMENT_GAP_MIN..SEGMENT_GAP_MAX seconds apart. Returns a list of groups
    (each a chronological list of >= 2 items).
    """
    videos = []
    for item in json_items:
        if item.get("Media Type") != "Video":
            continue
        try:
            videos.append((parse_json_date(item["Date"]), item))
        except Exception:
            continue
    videos.sort(key=lambda t: t[0])

    groups = []
    current = []
    prev_dt = prev_loc = None
    for vdt, item in videos:
        loc = item.get("Location", "")
        contiguous = (
            prev_dt is not None
            and SEGMENT_GAP_MIN <= (vdt - prev_dt).total_seconds() <= SEGMENT_GAP_MAX
            and loc == prev_loc
        )
        if contiguous:
            current.append(item)
        else:
            if len(current) > 1:
                groups.append(current)
            current = [item]
        prev_dt, prev_loc = vdt, loc
    if len(current) > 1:
        groups.append(current)
    return groups


def segment_link_ok(gap_seconds, duration):
    """Can a clip of `duration` be followed by the next segment `gap_seconds` later?"""
    if duration is None:
        return False
    if duration > SEGMENT_MAX_DURATION:
        return False
    return abs(duration - gap_seconds) <= SEGMENT_DURATION_SLACK


def split_runs(parts):
    """Split a candidate group into runs that really are one recording.

    parts: chronological list of (datetime, duration_seconds_or_None, payload).
    Each clip except a run's last must be a full segment ending where the
    next one starts. Returns lists of payloads, only runs of >= 2.
    """
    runs = []
    current = []
    for i, (dt, dur, payload) in enumerate(parts):
        if not current:
            current = [payload]
        else:
            prev_dt, prev_dur, _ = parts[i - 1]
            gap = (dt - prev_dt).total_seconds()
            if segment_link_ok(gap, prev_dur):
                current.append(payload)
            else:
                if len(current) > 1:
                    runs.append(current)
                current = [payload]
    if len(current) > 1:
        runs.append(current)
    return runs
