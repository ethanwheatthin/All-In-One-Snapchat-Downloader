"""Tests for the ~10s multi-segment video stitcher (issue #2).

Snapchat split pre-2021 recordings longer than ~10s into separate clips with
nothing linking them but their timestamps. The grouping tests below run as
pure logic — durations are injected, so they need no ffmpeg and cover the
false-positive cases that matter (rapid-fire snaps, different locations,
different recordings). The concat tests build real videos and are skipped
where ffmpeg isn't installed.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import stitch_utils


BASE = datetime(2020, 7, 14, 19, 51, 32, tzinfo=timezone.utc)

HAS_FFMPEG = shutil.which('ffmpeg') is not None and shutil.which('ffprobe') is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")


def clip(offset_seconds, duration, name=None, group_key='loc', **extra):
    """A candidate dict with its duration already known (no probing)."""
    candidate = dict(
        path=name or f"/out/{offset_seconds}.mp4",
        timestamp=BASE + timedelta(seconds=offset_seconds),
        duration=duration,
        group_key=group_key,
        width=1080, height=1920, codec='h264',
    )
    candidate.update(extra)
    return candidate


def group_paths(groups):
    return [[c['path'] for c in g] for g in groups]


# ==================== Grouping: true positives ====================

def test_groups_a_run_of_ten_second_clips():
    """The issue's case: 10s gaps matching 10s clips, last one short."""
    candidates = [
        clip(0, 10.0, 'a.mp4'),
        clip(10, 10.0, 'b.mp4'),
        clip(20, 10.0, 'c.mp4'),
        clip(30, 4.2, 'd.mp4'),
    ]
    assert group_paths(stitch_utils.find_segment_groups(candidates)) == \
        [['a.mp4', 'b.mp4', 'c.mp4', 'd.mp4']]


def test_groups_when_last_segment_is_also_full_length():
    """A recording that happens to be an exact multiple of the segment cap."""
    candidates = [clip(0, 10.0, 'a.mp4'), clip(10, 10.0, 'b.mp4')]
    assert len(stitch_utils.find_segment_groups(candidates)) == 1


def test_absorbs_one_second_timestamp_rounding():
    """Export timestamps are whole seconds, so a 9.6s clip reports a 10s gap."""
    candidates = [
        clip(0, 9.6, 'a.mp4'),
        clip(10, 9.6, 'b.mp4'),
        clip(20, 3.0, 'c.mp4'),
    ]
    assert len(stitch_utils.find_segment_groups(candidates)) == 1


def test_unsorted_input_is_ordered_by_timestamp():
    candidates = [clip(20, 5.0, 'c.mp4'), clip(0, 10.0, 'a.mp4'),
                  clip(10, 10.0, 'b.mp4')]
    assert group_paths(stitch_utils.find_segment_groups(candidates)) == \
        [['a.mp4', 'b.mp4', 'c.mp4']]


def test_two_separate_recordings_become_two_groups():
    candidates = [
        clip(0, 10.0, 'a.mp4'), clip(10, 10.0, 'b.mp4'), clip(20, 2.0, 'c.mp4'),
        clip(600, 10.0, 'd.mp4'), clip(610, 6.0, 'e.mp4'),
    ]
    assert group_paths(stitch_utils.find_segment_groups(candidates)) == \
        [['a.mp4', 'b.mp4', 'c.mp4'], ['d.mp4', 'e.mp4']]


# ==================== Grouping: false positives ====================

def test_rapid_fire_snaps_are_not_grouped():
    """Three 10s snaps saved seconds apart are separate memories.

    This is what a fixed "within 11 seconds" window gets wrong: the gap has
    to match the clip's own length, not merely be small.
    """
    candidates = [clip(0, 10.0, 'a.mp4'), clip(2, 10.0, 'b.mp4'),
                  clip(5, 10.0, 'c.mp4')]
    assert stitch_utils.find_segment_groups(candidates) == []


def test_short_clips_ten_seconds_apart_are_not_grouped():
    """A 3s clip cannot be a segment of a split recording, whatever the gap."""
    candidates = [clip(0, 3.0, 'a.mp4'), clip(10, 3.0, 'b.mp4')]
    assert stitch_utils.find_segment_groups(candidates) == []


def test_identical_timestamps_are_not_grouped():
    """Exports that stamp every clip with the same time must not chain."""
    candidates = [clip(0, 10.0, 'a.mp4'), clip(0, 10.0, 'b.mp4')]
    assert stitch_utils.find_segment_groups(candidates) == []


def test_different_group_keys_never_merge():
    """Different locations (or conversations) are different recordings."""
    candidates = [clip(0, 10.0, 'a.mp4', group_key='paris'),
                  clip(10, 5.0, 'b.mp4', group_key='tokyo')]
    assert stitch_utils.find_segment_groups(candidates) == []


def test_group_key_defaults_to_the_output_folder():
    """Without an explicit key, files in different folders stay apart."""
    a = clip(0, 10.0, os.path.join('x', 'a.mp4'))
    b = clip(10, 5.0, os.path.join('y', 'b.mp4'))
    a.pop('group_key')
    b.pop('group_key')
    assert stitch_utils.find_segment_groups([a, b]) == []


def test_resolution_change_breaks_the_chain():
    candidates = [clip(0, 10.0, 'a.mp4'),
                  clip(10, 5.0, 'b.mp4', width=720, height=1280)]
    assert stitch_utils.find_segment_groups(candidates) == []


def test_unmeasurable_clips_are_left_alone():
    """No duration means no way to verify the gap — so no stitching."""
    candidates = [dict(clip(0, None, 'a.mp4'), duration=None),
                  dict(clip(10, None, 'b.mp4'), duration=None)]
    assert stitch_utils.find_segment_groups(candidates, probe=False) == []


def test_single_clip_is_never_a_group():
    assert stitch_utils.find_segment_groups([clip(0, 10.0, 'a.mp4')]) == []


def test_gap_matching_the_next_clip_rather_than_this_one_is_rejected():
    """Chaining is defined by the *earlier* clip's length, not the later's."""
    candidates = [clip(0, 10.0, 'a.mp4'), clip(4, 4.0, 'b.mp4')]
    assert stitch_utils.find_segment_groups(candidates) == []


# ==================== Naming ====================

def test_merged_name_uses_the_MERGED_suffix():
    with tempfile.TemporaryDirectory() as tmp:
        first = Path(tmp) / "20200714_195132_45.mp4"
        first.touch()
        assert stitch_utils.merged_output_path(first).name == \
            "20200714_195132_45_MERGED.mp4"


def test_merged_name_is_derived_not_uniquified():
    """A stable name is what lets a re-run recognise an already-joined group."""
    with tempfile.TemporaryDirectory() as tmp:
        first = Path(tmp) / "clip.mp4"
        first.touch()
        (Path(tmp) / "clip_MERGED.mp4").touch()
        assert stitch_utils.merged_output_path(first).name == "clip_MERGED.mp4"


# ==================== Concatenation (needs ffmpeg) ====================

def _make_clip(path, duration, size='320x240', rate=30, audio=True):
    """Render a real test video of a given length."""
    cmd = ['ffmpeg', '-y', '-v', 'error',
           '-f', 'lavfi', '-i', f'testsrc=duration={duration}:size={size}:rate={rate}']
    if audio:
        cmd += ['-f', 'lavfi', '-i', f'sine=frequency=440:duration={duration}',
                '-c:a', 'aac']
    cmd += ['-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(path)]
    proc = subprocess.run(cmd, capture_output=True, timeout=120)
    return proc.returncode == 0 and os.path.exists(path)


@pytest.fixture
def recording(tmp_path):
    """Three real clips (10s, 10s, 5s) named and timed like a split memory."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not installed")
    specs = [("20200714_195132_1.mp4", 10, 0),
             ("20200714_195142_2.mp4", 10, 10),
             ("20200714_195152_3.mp4", 5, 20)]
    candidates = []
    for name, duration, offset in specs:
        path = tmp_path / name
        if not _make_clip(path, duration):
            pytest.skip("could not render test clips")
        candidates.append({'path': str(path),
                           'timestamp': BASE + timedelta(seconds=offset),
                           'group_key': 'loc'})
    return tmp_path, candidates


@needs_ffmpeg
def test_probe_reads_duration_and_dimensions(tmp_path):
    path = tmp_path / "probe.mp4"
    if not _make_clip(path, 2):
        pytest.skip("could not render test clip")
    info = stitch_utils.probe_video(path)
    assert info is not None
    assert info['duration'] == pytest.approx(2.0, abs=0.3)
    assert (info['width'], info['height']) == (320, 240)
    assert info['codec'] == 'h264'
    assert info['has_audio'] is True


@needs_ffmpeg
def test_probe_returns_none_for_a_non_video(tmp_path):
    junk = tmp_path / "notavideo.mp4"
    junk.write_bytes(b"this is not a video")
    assert stitch_utils.probe_video(junk) is None


@needs_ffmpeg
def test_concat_produces_the_full_length(tmp_path):
    parts = []
    for i, duration in enumerate((10, 10, 5)):
        path = tmp_path / f"part{i}.mp4"
        if not _make_clip(path, duration):
            pytest.skip("could not render test clips")
        parts.append(path)

    out = tmp_path / "joined.mp4"
    ok, detail = stitch_utils.concat_segments(parts, out)
    assert ok, detail
    assert stitch_utils.probe_video(out)['duration'] == pytest.approx(25.0, abs=1.0)


@needs_ffmpeg
def test_concat_falls_back_when_segments_differ(tmp_path):
    """One segment silent, the next not — no stream copy, so re-encode it."""
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    if not (_make_clip(a, 4, audio=True) and _make_clip(b, 4, audio=False)):
        pytest.skip("could not render test clips")

    assert not stitch_utils._uniform([stitch_utils.probe_video(a),
                                      stitch_utils.probe_video(b)])
    out = tmp_path / "joined.mp4"
    ok, detail = stitch_utils.concat_segments([a, b], out)
    assert ok, detail
    assert stitch_utils.probe_video(out)['duration'] == pytest.approx(8.0, abs=1.0)


@needs_ffmpeg
def test_concat_refuses_a_single_segment(tmp_path):
    path = tmp_path / "only.mp4"
    if not _make_clip(path, 2):
        pytest.skip("could not render test clip")
    ok, detail = stitch_utils.concat_segments([path], tmp_path / "out.mp4")
    assert not ok
    assert "two segments" in detail


@needs_ffmpeg
def test_concat_reports_missing_segments(tmp_path):
    ok, detail = stitch_utils.concat_segments(
        [tmp_path / "gone_a.mp4", tmp_path / "gone_b.mp4"], tmp_path / "out.mp4")
    assert not ok
    assert "missing" in detail


@needs_ffmpeg
def test_concat_discards_a_short_result(tmp_path, monkeypatch):
    """A stitch that loses footage must not be left on disk pretending to work."""
    parts = []
    for i in range(2):
        path = tmp_path / f"p{i}.mp4"
        if not _make_clip(path, 3):
            pytest.skip("could not render test clips")
        parts.append(path)

    out = tmp_path / "joined.mp4"
    # Both concat paths "succeed" while silently producing a 1s file.
    truncating = lambda paths, output: (_make_clip(output, 1), str(output))
    monkeypatch.setattr(stitch_utils, '_concat_stream_copy', truncating)
    monkeypatch.setattr(stitch_utils.zip_utils, 'concat_video_segments', truncating)

    ok, detail = stitch_utils.concat_segments(parts, out)
    assert not ok
    assert "segments total" in detail
    assert not out.exists(), "a short stitch must be cleaned up, not left behind"


# ==================== Full post-pass ====================

@needs_ffmpeg
def test_stitch_groups_end_to_end(recording):
    tmp_path, candidates = recording
    logs = []
    stamped = []

    summary = stitch_utils.stitch_groups(
        candidates, log_fn=logs.append,
        on_stitched=lambda path, first: stamped.append((path, first['path'])),
    )

    assert summary['groups'] == 1
    assert summary['stitched'] == 1
    assert summary['failed'] == 0

    merged = Path(summary['merged'][0])
    assert merged.name == "20200714_195132_1_MERGED.mp4"
    assert stitch_utils.probe_video(merged)['duration'] == pytest.approx(25.0, abs=1.0)

    # The callback gets the merged file and the group's first segment, so the
    # caller can stamp the recording's real start time onto it.
    assert stamped == [(str(merged), candidates[0]['path'])]


@needs_ffmpeg
def test_segments_are_preserved_not_deleted(recording):
    tmp_path, candidates = recording
    stitch_utils.stitch_groups(candidates)

    segments_dir = tmp_path / stitch_utils.SEGMENTS_DIRNAME
    assert segments_dir.is_dir()
    assert sorted(p.name for p in segments_dir.iterdir()) == [
        "20200714_195132_1.mp4", "20200714_195142_2.mp4", "20200714_195152_3.mp4"]
    for cand in candidates:
        assert not os.path.exists(cand['path']), "segment should have moved"


@needs_ffmpeg
def test_stitch_groups_can_delete_segments_instead(recording):
    tmp_path, candidates = recording
    stitch_utils.stitch_groups(candidates, keep_segments=False)
    assert not (tmp_path / stitch_utils.SEGMENTS_DIRNAME).exists()
    for cand in candidates:
        assert not os.path.exists(cand['path'])


@needs_ffmpeg
def test_rerunning_does_not_stitch_twice(recording):
    """Resume runs re-see the same segments; they must not pile up duplicates."""
    tmp_path, candidates = recording
    first = stitch_utils.stitch_groups(candidates, keep_segments=False)
    assert first['stitched'] == 1

    # Second run: the segments are back (as a resumed download would restore
    # them) but the joined file already exists.
    for cand in candidates:
        _make_clip(cand['path'], 10)
    logs = []
    second = stitch_utils.stitch_groups(candidates, log_fn=logs.append)

    assert second['stitched'] == 0
    assert second['skipped'] == 1
    assert any("Already joined" in line for line in logs)
    assert len(list(tmp_path.glob("*_MERGED*.mp4"))) == 1


@needs_ffmpeg
def test_unrelated_clips_are_left_untouched(tmp_path):
    """Nothing to stitch means nothing moves, nothing is created."""
    paths = []
    for i, offset in enumerate((0, 120)):
        path = tmp_path / f"solo{i}.mp4"
        if not _make_clip(path, 10):
            pytest.skip("could not render test clips")
        paths.append({'path': str(path), 'group_key': 'loc',
                      'timestamp': BASE + timedelta(seconds=offset)})

    summary = stitch_utils.stitch_groups(paths)
    assert summary == {'groups': 0, 'stitched': 0, 'skipped': 0,
                       'failed': 0, 'merged': []}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["solo0.mp4", "solo1.mp4"]


def test_stitch_groups_without_ffmpeg_is_a_no_op(monkeypatch):
    """No ffmpeg: say so and leave the segments alone rather than half-work."""
    monkeypatch.setattr(stitch_utils.shutil, 'which', lambda _name: None)
    logs = []
    summary = stitch_utils.stitch_groups(
        [clip(0, 10.0, 'a.mp4'), clip(10, 5.0, 'b.mp4')], log_fn=logs.append)
    assert summary['groups'] == 1
    assert summary['stitched'] == 0
    assert summary['failed'] == 1
    assert any("ffmpeg" in line for line in logs)


@needs_ffmpeg
def test_stop_flag_halts_between_groups(tmp_path):
    """Stopping mid-run leaves the remaining groups untouched."""
    candidates = []
    for group_no, base_offset in enumerate((0, 600)):
        for i, (duration, offset) in enumerate(((10, 0), (5, 10))):
            path = tmp_path / f"g{group_no}_{i}.mp4"
            if not _make_clip(path, duration):
                pytest.skip("could not render test clips")
            candidates.append({'path': str(path), 'group_key': f'g{group_no}',
                               'timestamp': BASE + timedelta(seconds=base_offset + offset)})

    summary = stitch_utils.stitch_groups(candidates, should_stop=lambda: True)
    assert summary['groups'] == 2
    assert summary['stitched'] == 0
    for cand in candidates:
        assert os.path.exists(cand['path'])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
