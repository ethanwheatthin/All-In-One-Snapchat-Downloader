"""
Test video GPS/date metadata writers.

Regression test for the macOS "no GPS on mp4" bug: on machines without an
ffmpeg CLI the app falls back from set_video_metadata_ffmpeg. The fallback
must write QuickTime 'mdta' key metadata (com.apple.quicktime.location.ISO6709)
because Apple Photos/Finder/Spotlight ignore the iTunes-style freeform atoms
that mutagen writes. set_video_metadata_pyav provides that via the bundled
PyAV, so GPS survives even with no ffmpeg installed.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import subprocess
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest
import video_utils


TEST_LAT = 40.712776
TEST_LON = -74.005974
TEST_DATE = datetime(2023, 6, 15, 14, 30, 0, tzinfo=timezone(timedelta(hours=-5)))


def _make_test_video(path):
    """Create a 1-second test mp4; returns True on success."""
    cmd = [
        'ffmpeg', '-f', 'lavfi', '-i', 'testsrc=duration=1:size=320x240:rate=1',
        '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1',
        '-y', str(path)
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=10)
    return result.returncode == 0 and path.exists()


def _read_format_tags(path):
    """Return the container-level metadata tags ffprobe sees."""
    cmd = [
        'ffprobe', '-v', 'error', '-show_entries', 'format_tags',
        '-of', 'json', str(path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, f"ffprobe failed: {result.stderr}"
    return json.loads(result.stdout).get('format', {}).get('tags', {})


def _atom_structure(path):
    """Return (moov_child_names, udta_child_names) for structure assertions."""
    with open(path, 'rb') as f:
        data = f.read()
    moov = next((s, e) for name, s, e in video_utils._iter_boxes(data, 0, len(data))
                if name == b'moov')
    moov_children = list(video_utils._iter_boxes(data, moov[0] + 8, moov[1]))
    udta = next(((s, e) for name, s, e in moov_children if name == b'udta'), None)
    udta_children = []
    if udta:
        udta_children = list(video_utils._iter_boxes(data, udta[0] + 8, udta[1]))
    return ([n for n, s, e in moov_children],
            [n for n, s, e in udta_children],
            data, moov_children)


def test_set_video_metadata_pyav_writes_apple_readable_gps():
    """PyAV writer must produce mdta-key GPS tags Apple software reads."""
    if not video_utils.HAS_PYAV:
        pytest.skip("PyAV not available")
    if not video_utils.check_ffmpeg():
        pytest.skip("ffmpeg/ffprobe not available to create/inspect test video")

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        temp_path = Path(f.name)

    try:
        if not _make_test_video(temp_path):
            pytest.skip("Could not create test video with ffmpeg")

        ok = video_utils.set_video_metadata_pyav(
            str(temp_path), TEST_DATE, TEST_LAT, TEST_LON, "-05:00"
        )
        assert ok, "set_video_metadata_pyav returned False"

        tags = _read_format_tags(temp_path)
        # The key Apple Photos/Finder/Spotlight read GPS from:
        assert tags.get('com.apple.quicktime.location.ISO6709') == '+40.712776-74.005974/'
        assert tags.get('com.apple.quicktime.creationdate') == '2023-06-15T14:30:00-05:00'

        # Apple only reads Keys from a meta box that is a DIRECT child of moov
        # (ffmpeg's default moov/udta/meta placement is invisible to Photos)
        moov_names, udta_names, data, moov_children = _atom_structure(temp_path)
        assert b'meta' in moov_names, "meta box must be a direct child of moov"
        meta = next((s, e) for n, s, e in moov_children if n == b'meta')
        assert video_utils._meta_has_mdta_handler(data, meta[0], meta[1])
        assert b'meta' not in udta_names, "meta box should have been moved out of udta"
        # Android-style (C)xyz GPS atom for broader compatibility
        assert b'\xa9xyz' in udta_names, "udta should contain a (C)xyz GPS atom"
        assert b'+40.712776-74.005974/' in data

        # Remux must preserve the streams
        is_valid, info = video_utils.validate_video_file(temp_path)
        assert is_valid, f"Remuxed file failed validation: {info}"
        assert info['has_video'] and info['has_audio']
    finally:
        if temp_path.exists():
            temp_path.unlink()


def test_set_video_metadata_ffmpeg_relocates_keys_meta():
    """ffmpeg CLI writer must also end up with moov-level Keys metadata."""
    if not video_utils.check_ffmpeg():
        pytest.skip("ffmpeg not available")

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        temp_path = Path(f.name)

    try:
        if not _make_test_video(temp_path):
            pytest.skip("Could not create test video with ffmpeg")

        ok = video_utils.set_video_metadata_ffmpeg(
            str(temp_path), TEST_DATE, TEST_LAT, TEST_LON, "-05:00"
        )
        assert ok, "set_video_metadata_ffmpeg returned False"

        moov_names, udta_names, data, moov_children = _atom_structure(temp_path)
        assert b'meta' in moov_names, "meta box must be a direct child of moov"
        assert b'\xa9xyz' in udta_names, "udta should contain a (C)xyz GPS atom"

        is_valid, info = video_utils.validate_video_file(temp_path)
        assert is_valid, f"File failed validation after metadata write: {info}"
    finally:
        if temp_path.exists():
            temp_path.unlink()


def test_set_video_metadata_pyav_without_gps():
    """Writer must still set dates (and succeed) when no coordinates exist."""
    if not video_utils.HAS_PYAV:
        pytest.skip("PyAV not available")
    if not video_utils.check_ffmpeg():
        pytest.skip("ffmpeg/ffprobe not available to create/inspect test video")

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        temp_path = Path(f.name)

    try:
        if not _make_test_video(temp_path):
            pytest.skip("Could not create test video with ffmpeg")

        ok = video_utils.set_video_metadata_pyav(
            str(temp_path), TEST_DATE, None, None, "-05:00"
        )
        assert ok, "set_video_metadata_pyav returned False"

        tags = _read_format_tags(temp_path)
        assert tags.get('com.apple.quicktime.creationdate') == '2023-06-15T14:30:00-05:00'
        assert 'com.apple.quicktime.location.ISO6709' not in tags
    finally:
        if temp_path.exists():
            temp_path.unlink()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
