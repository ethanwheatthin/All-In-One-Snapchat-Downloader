"""Wiring tests: the app's own stitch hooks, not just stitch_utils.

test_stitch_utils.py covers the grouping and concat logic directly. These
tests go through SnapchatDownloaderGUI's methods so the glue is covered too —
candidate shape, the group key it builds, the metadata callback, and the
resume check that stops retired segments being downloaded again.
"""
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import stitch_utils

tk = pytest.importorskip("tkinter")

HAS_FFMPEG = shutil.which('ffmpeg') is not None and shutil.which('ffprobe') is not None
pytestmark = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")

BASE = datetime(2020, 7, 14, 19, 51, 32, tzinfo=timezone.utc)


def _make_clip(path, duration):
    proc = subprocess.run(
        ['ffmpeg', '-y', '-v', 'error',
         '-f', 'lavfi', '-i', f'testsrc=duration={duration}:size=320x240:rate=30',
         '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(path)],
        capture_output=True, timeout=120)
    return proc.returncode == 0 and os.path.exists(path)


@pytest.fixture
def app(monkeypatch, tmp_path):
    """A real GUI object with its settings file redirected and log captured."""
    import download_snapchat_memories_gui as gui

    monkeypatch.setattr(gui, 'SETTINGS_FILE', str(tmp_path / 'settings.json'))
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available for tkinter")
    root.withdraw()

    instance = gui.SnapchatDownloaderGUI(root)
    instance.logged = []
    monkeypatch.setattr(instance, 'log', instance.logged.append)
    try:
        yield instance
    finally:
        root.destroy()


@pytest.fixture
def segments(tmp_path):
    """Three clips on disk, timed like one recording split at 10s."""
    made = []
    for name, duration, offset in (("20200714_195132_1.mp4", 10, 0),
                                   ("20200714_195142_2.mp4", 10, 10),
                                   ("20200714_195152_3.mp4", 5, 20)):
        path = tmp_path / name
        if not _make_clip(path, duration):
            pytest.skip("could not render test clips")
        made.append((str(path), BASE + timedelta(seconds=offset)))
    return made


def test_registered_segments_are_joined_with_MERGED_suffix(app, segments, tmp_path):
    app.stitch_segments.set(True)
    app._reset_stitch_candidates()
    for path, stamp in segments:
        app._register_stitch_candidate(path, stamp, 40.7128, -74.006, "-05:00",
                                       group_key="40.7128, -74.006")

    summary = app._run_stitch_pass()

    assert summary["stitched"] == 1, app.logged
    merged = tmp_path / "20200714_195132_1_MERGED.mp4"
    assert merged.exists()
    assert stitch_utils.probe_video(merged)["duration"] == pytest.approx(25.0, abs=1.0)


def test_merged_file_carries_the_recordings_start_time(app, segments, tmp_path):
    """The joined file must be dated when the recording began, not when it ended."""
    app.stitch_segments.set(True)
    app._reset_stitch_candidates()
    for path, stamp in segments:
        app._register_stitch_candidate(path, stamp.astimezone(), None, None, "+00:00")

    app._run_stitch_pass()

    merged = tmp_path / "20200714_195132_1_MERGED.mp4"
    written = datetime.fromtimestamp(merged.stat().st_mtime, tz=timezone.utc)
    assert abs((written - BASE).total_seconds()) < 5


def test_disabling_the_option_registers_nothing(app, segments, tmp_path):
    app.stitch_segments.set(False)
    app._reset_stitch_candidates()
    for path, stamp in segments:
        app._register_stitch_candidate(path, stamp)

    assert app._run_stitch_pass()["groups"] == 0
    assert list(tmp_path.glob("*_MERGED*")) == []


def test_images_are_never_registered(app, tmp_path):
    app.stitch_segments.set(True)
    app._reset_stitch_candidates()
    app._register_stitch_candidate(str(tmp_path / "photo.jpg"), BASE)
    assert app._stitch_candidates == []


def test_separate_output_folders_do_not_merge(app, tmp_path):
    """Two conversations' clips land in different folders and stay apart."""
    app.stitch_segments.set(True)
    app._reset_stitch_candidates()
    for folder in ("alice", "bob"):
        (tmp_path / folder).mkdir()
        for i, (duration, offset) in enumerate(((10, 0), (5, 10))):
            path = tmp_path / folder / f"clip{i}.mp4"
            if not _make_clip(path, duration):
                pytest.skip("could not render test clips")
            app._register_stitch_candidate(str(path), BASE + timedelta(seconds=offset),
                                           group_key=folder)

    summary = app._run_stitch_pass()
    assert summary["groups"] == 2
    assert summary["stitched"] == 2
    assert (tmp_path / "alice" / "clip0_MERGED.mp4").exists()
    assert (tmp_path / "bob" / "clip0_MERGED.mp4").exists()


def test_resume_does_not_redownload_retired_segments(app, tmp_path):
    """A clip moved into segments/ counts as present, or resume re-fetches it."""
    output = tmp_path / "out"
    (output / stitch_utils.SEGMENTS_DIRNAME).mkdir(parents=True)
    retired = output / stitch_utils.SEGMENTS_DIRNAME / "20200714_195132_7.mp4"
    if not _make_clip(retired, 2):
        pytest.skip("could not render test clip")

    local_dt = BASE.astimezone()
    should_skip, path, reason = app.should_skip_download(
        {"Date": "2020-07-14 19:51:32 UTC"}, output, 7, BASE, local_dt, ".mp4")

    assert should_skip, f"resume would re-download a joined segment ({reason})"
    assert Path(path) == retired


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
