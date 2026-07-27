"""Re-join Snapchat recordings that the export split into ~10s segments.

Before ~August 2021 a single snap was capped at about 10 seconds, so a longer
recording left the camera as a run of contiguous clips. The export ships them
as separate entries with no field linking them (issue #2) — `memories_history`
carries only Date/Media Type/Location, and chat media carries even less. The
only usable signal is that each segment's timestamp sits exactly one segment
length after the previous one's.

So a group is a run of clips where, for every clip but the last:

    |(next.timestamp - this.timestamp) - this.duration| <= GAP_TOLERANCE
    SEGMENT_MIN_DURATION <= this.duration <= SEGMENT_MAX_DURATION

Comparing the gap against each clip's *measured* duration is what makes this
safe. Two unrelated snaps would have to be captured exactly one clip-length
apart, at the same location/conversation, at the same resolution and codec.
A fixed "within 11 seconds" window would instead sweep up ordinary rapid-fire
snaps, which is why the gap is matched to the duration rather than a constant.

The stitched file keeps the first segment's name plus a `_MERGED` suffix, and
the segments it consumed are moved into a `segments/` subfolder rather than
deleted — a wrong grouping should never cost anyone their originals.
"""

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import zip_utils

CREATE_NO_WINDOW = 0x08000000 if sys.platform == 'win32' else 0

MERGED_SUFFIX = "_MERGED"
SEGMENTS_DIRNAME = "segments"

# Snapchat's cap was nominally 10s but real exports drift either side of it.
SEGMENT_MIN_DURATION = 9.0
SEGMENT_MAX_DURATION = 11.0
# Export timestamps have 1-second resolution, so a 9.6s clip legitimately
# reports a 10s gap. The tolerance has to absorb that rounding.
GAP_TOLERANCE = 1.0
# A stitched file shorter than the sum of its parts means ffmpeg dropped
# something; anything outside this slack is treated as a failed stitch.
DURATION_SLACK = 1.0

VIDEO_EXTS = ('.mp4', '.mov', '.m4v', '.avi', '.mkv')


def is_video_path(path):
    return os.path.splitext(str(path))[1].lower() in VIDEO_EXTS


# ==================== Probing ====================

def probe_video(path):
    """Duration/dimensions/codec of a video, or None if it can't be read.

    Returns a dict with duration (float seconds), width, height, codec and
    has_audio. Needs ffprobe — without it there is no way to measure a
    segment, and no way to concatenate one either, so callers treat a None
    here as "this file cannot take part in stitching".
    """
    if shutil.which('ffprobe') is None:
        return None
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error',
             '-show_entries', 'format=duration:stream=codec_type,codec_name,width,height',
             '-of', 'json', str(path)],
            capture_output=True, text=True, timeout=30,
            creationflags=CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            return None
        import json as _json
        data = _json.loads(result.stdout)
    except Exception as exc:
        logging.debug("probe_video failed for %s: %s", path, exc)
        return None

    try:
        duration = float(data.get('format', {}).get('duration'))
    except (TypeError, ValueError):
        return None

    info = {'duration': duration, 'width': None, 'height': None,
            'codec': None, 'has_audio': False}
    for stream in data.get('streams', []):
        if stream.get('codec_type') == 'video' and info['codec'] is None:
            info['codec'] = stream.get('codec_name')
            info['width'] = stream.get('width')
            info['height'] = stream.get('height')
        elif stream.get('codec_type') == 'audio':
            info['has_audio'] = True
    if info['codec'] is None:
        return None
    return info


# ==================== Grouping ====================

def _compatible(a, b):
    """Whether two segments look like parts of the same recording."""
    for key in ('width', 'height', 'codec'):
        va, vb = a.get(key), b.get(key)
        if va is not None and vb is not None and va != vb:
            return False
    return True


def find_segment_groups(candidates, min_duration=SEGMENT_MIN_DURATION,
                        max_duration=SEGMENT_MAX_DURATION,
                        gap_tolerance=GAP_TOLERANCE, probe=True):
    """Find runs of clips that were one recording before the export split it.

    `candidates` are dicts with at least:
        path       — the produced video file
        timestamp  — capture time (datetime; all must share tz-awareness)
    and optionally:
        group_key  — anything hashable that must match across a group
                     (location for memories, conversation for chat media).
                     Defaults to the file's own folder, so files written to
                     different folders never merge.
        duration / width / height / codec — skips the ffprobe call when the
                     caller already knows them (also how tests inject values).

    Returns a list of groups, each a timestamp-ordered list of the same dicts,
    each with at least two members. Candidates whose duration can't be
    determined are left out entirely rather than guessed at.
    """
    prepared = []
    for cand in candidates:
        if cand.get('timestamp') is None or not cand.get('path'):
            continue
        if cand.get('duration') is None and probe:
            info = probe_video(cand['path'])
            if info:
                cand = dict(cand)
                cand.update(info)
        if cand.get('duration') is None:
            continue
        prepared.append(cand)

    by_key = {}
    for cand in prepared:
        key = cand.get('group_key')
        if key is None:
            key = ('__dir__', os.path.dirname(os.path.abspath(cand['path'])))
        by_key.setdefault(key, []).append(cand)

    groups = []
    for key in sorted(by_key, key=lambda k: str(k)):
        run = []
        previous = None
        for cand in sorted(by_key[key], key=lambda c: (c['timestamp'], c['path'])):
            chains = False
            if previous is not None:
                gap = (cand['timestamp'] - previous['timestamp']).total_seconds()
                chains = (
                    min_duration <= previous['duration'] <= max_duration
                    and abs(gap - previous['duration']) <= gap_tolerance
                    and _compatible(previous, cand)
                )
            if chains:
                run.append(cand)
            else:
                if len(run) > 1:
                    groups.append(run)
                run = [cand]
            previous = cand
        if len(run) > 1:
            groups.append(run)
    return groups


# ==================== Naming ====================

def merged_output_path(first_segment_path, suffix=MERGED_SUFFIX):
    """Where a group's stitched file goes: first segment's name + `_MERGED`.

    Naming after the first segment keeps the recording sorted where the user
    already expects to find it. The name is derived, not uniquified, so that
    an existing `_MERGED` file identifies a group as already joined and a
    re-run skips it instead of littering `_MERGED_1`, `_MERGED_2`, …
    """
    path = Path(first_segment_path)
    return path.with_name(f"{path.stem}{suffix}{path.suffix}")


# ==================== Concatenation ====================

def _uniform(infos):
    """Whether every segment shares codec, dimensions and audio presence."""
    if not infos or any(i is None for i in infos):
        return False
    first = infos[0]
    return all(
        i['codec'] == first['codec'] and i['width'] == first['width']
        and i['height'] == first['height']
        and i['has_audio'] == first['has_audio']
        for i in infos
    )


def _concat_stream_copy(paths, output_path):
    """Join with ffmpeg's concat demuxer without re-encoding.

    Segments of one recording share encoder settings, so this is both lossless
    and near-instant. Returns (ok, detail).
    """
    list_dir = tempfile.mkdtemp(prefix='snapstitch_')
    list_file = os.path.join(list_dir, 'segments.txt')
    try:
        with open(list_file, 'w', encoding='utf-8') as fh:
            for p in paths:
                # concat's own quoting: wrap in single quotes, escape any
                # single quote in the path itself.
                escaped = str(Path(p).resolve()).replace("'", r"'\''")
                fh.write(f"file '{escaped}'\n")

        proc = subprocess.run(
            ['ffmpeg', '-y', '-v', 'error', '-f', 'concat', '-safe', '0',
             '-i', list_file, '-c', 'copy', '-movflags', '+faststart',
             str(output_path)],
            capture_output=True, text=True, timeout=max(300, 60 * len(paths)),
            creationflags=CREATE_NO_WINDOW,
        )
        if proc.returncode != 0:
            return False, (proc.stderr or 'ffmpeg concat copy failed').strip()[-300:]
        return True, str(output_path)
    except subprocess.TimeoutExpired:
        return False, 'ffmpeg concat copy timed out'
    except Exception as exc:
        return False, str(exc)
    finally:
        shutil.rmtree(list_dir, ignore_errors=True)


def concat_segments(paths, output_path, expected_duration=None):
    """Concatenate segments into output_path, verifying the result.

    Tries a lossless stream copy first and falls back to the re-encoding
    concat filter when the segments' parameters don't line up. Either way the
    output's duration must come out within DURATION_SLACK of the sum of its
    parts — a short file means frames were dropped, and half a recording is
    worse than none, so it is discarded.

    Returns (ok, detail) where detail is the output path or an error message.
    """
    paths = [str(p) for p in paths]
    if len(paths) < 2:
        return False, 'need at least two segments to concat'
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        return False, f'{len(missing)} segment(s) missing from disk'
    if shutil.which('ffmpeg') is None or shutil.which('ffprobe') is None:
        return False, 'ffmpeg/ffprobe not found'

    infos = [probe_video(p) for p in paths]
    if expected_duration is None and all(i is not None for i in infos):
        expected_duration = sum(i['duration'] for i in infos)

    attempts = []
    if _uniform(infos):
        attempts.append(('stream copy', _concat_stream_copy))
    attempts.append(('re-encode', zip_utils.concat_video_segments))

    last_error = 'no concat method available'
    for label, fn in attempts:
        try:
            ok, detail = fn(paths, str(output_path))
        except Exception as exc:
            ok, detail = False, str(exc)

        if ok:
            result = probe_video(output_path)
            if result is None:
                ok, detail = False, 'stitched file could not be read back'
            elif expected_duration is not None and \
                    result['duration'] < expected_duration - DURATION_SLACK:
                ok, detail = False, (
                    f'stitched to {result["duration"]:.1f}s but segments total '
                    f'{expected_duration:.1f}s'
                )

        if ok:
            logging.info("Stitched %d segments via %s → %s",
                         len(paths), label, output_path)
            return True, str(output_path)

        last_error = f'{label}: {detail}'
        logging.warning("Concat via %s failed: %s", label, detail)
        try:
            if os.path.exists(output_path):
                os.remove(output_path)
        except Exception:
            pass

    return False, last_error


# ==================== Post-pass ====================

def _retire_segments(paths, segments_dirname=SEGMENTS_DIRNAME):
    """Move consumed segments into a sibling folder. Returns where they went.

    Deliberately not a delete: grouping is a heuristic, and a user who finds a
    wrongly merged clip needs to still have the parts.
    """
    if not paths:
        return None
    dest = Path(paths[0]).parent / segments_dirname
    dest.mkdir(parents=True, exist_ok=True)
    for path in paths:
        target = dest / Path(path).name
        counter = 1
        while target.exists():
            target = dest / f"{Path(path).stem}_{counter}{Path(path).suffix}"
            counter += 1
        try:
            shutil.move(str(path), str(target))
        except Exception as exc:
            logging.warning("Could not move segment %s aside: %s", path, exc)
    return dest


def stitch_groups(candidates, log_fn=None, on_stitched=None,
                  keep_segments=True, segments_dirname=SEGMENTS_DIRNAME,
                  should_stop=None, **group_kwargs):
    """Find multi-segment recordings among `candidates` and join each one.

    `on_stitched(merged_path, first_candidate)` runs after a successful join
    so the caller can stamp the recording's date/GPS onto the merged file —
    stitch_utils deliberately knows nothing about metadata writers.

    Returns a summary dict: groups, stitched, skipped, failed, merged (paths).
    """
    log = log_fn or (lambda _m: None)
    summary = {'groups': 0, 'stitched': 0, 'skipped': 0, 'failed': 0, 'merged': []}

    groups = find_segment_groups(candidates, **group_kwargs)
    summary['groups'] = len(groups)
    if not groups:
        return summary

    total_clips = sum(len(g) for g in groups)
    log(f"🔗 Found {len(groups):,} multi-segment recording(s) across "
        f"{total_clips:,} clips")

    if shutil.which('ffmpeg') is None or shutil.which('ffprobe') is None:
        log("  ⚠ ffmpeg not found — segments left as-is. Install ffmpeg to "
            "join them back together.")
        summary['failed'] = len(groups)
        return summary

    for number, group in enumerate(groups, 1):
        if should_stop is not None and should_stop():
            break

        paths = [c['path'] for c in group]
        out_path = merged_output_path(paths[0])
        if out_path.exists():
            # A previous run already joined this recording. Re-stitching would
            # only produce a duplicate, so leave it be.
            summary['skipped'] += 1
            log(f"[{number}/{len(groups)}] Already joined → {out_path.name}")
            continue

        span = sum(c['duration'] for c in group)
        log(f"[{number}/{len(groups)}] Joining {len(paths)} clips "
            f"(~{span:.0f}s) → {out_path.name}")

        ok, detail = concat_segments(paths, out_path, expected_duration=span)
        if not ok:
            summary['failed'] += 1
            log(f"  ✗ Could not join: {detail}")
            continue

        if on_stitched is not None:
            try:
                on_stitched(str(out_path), group[0])
            except Exception as exc:
                logging.warning("on_stitched callback failed: %s", exc)

        if keep_segments:
            moved_to = _retire_segments(paths, segments_dirname)
            if moved_to is not None:
                log(f"  ✓ Joined — original clips moved to {segments_dirname}/")
        else:
            for path in paths:
                try:
                    os.remove(path)
                except Exception:
                    pass
            log("  ✓ Joined — original clips removed")

        summary['stitched'] += 1
        summary['merged'].append(str(out_path))

    return summary
