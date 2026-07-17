"""Tests for export_zip_utils — processing folders of Snapchat export ZIPs."""

import os
import sys
import time
import zipfile
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import export_zip_utils


FIXED_DT = (2024, 6, 15, 12, 30, 0)


def _make_zip(path, members):
    """Create a zip at path; members is {name: bytes}."""
    with zipfile.ZipFile(path, "w") as z:
        for name, data in members.items():
            info = zipfile.ZipInfo(name, date_time=FIXED_DT)
            z.writestr(info, data)


@pytest.fixture
def export_dir(tmp_path):
    """A folder of ZIPs mimicking a split Snapchat export."""
    _make_zip(tmp_path / "mydata~123.zip", {
        "json/memories_history.json": b'{"Saved Media": []}',
        "json/chat_history.json": b"{}",
        "chat_media/media~AAA.jpg": b"jpeg-a",
        "index.html": b"<html></html>",
    })
    _make_zip(tmp_path / "mydata~123-2.zip", {
        "memories/2024-06-15_abc-main.jpg": b"jpeg-main",
        "chat_media/media~BBB.jpg": b"jpeg-b",
    })
    _make_zip(tmp_path / "mydata~123-3.zip", {
        "memories/2024-06-16_def-main.mp4": b"mp4-main",
    })
    # A non-export zip that must be ignored
    _make_zip(tmp_path / "unrelated.zip", {"notes/todo.txt": b"hi"})
    return tmp_path


def test_find_export_zips_filters_non_exports(export_dir):
    zips = export_zip_utils.find_export_zips(str(export_dir))
    names = [os.path.basename(z) for z in zips]
    assert "unrelated.zip" not in names
    assert len(names) == 3


def test_find_export_zips_empty_and_missing_dir(tmp_path):
    assert export_zip_utils.find_export_zips(str(tmp_path)) == []
    assert export_zip_utils.find_export_zips(str(tmp_path / "nope")) == []


def test_extract_merges_all_zips_into_one_root(export_dir):
    zips = export_zip_utils.find_export_zips(str(export_dir))
    dest = export_zip_utils.default_extract_root(str(export_dir))
    stats = export_zip_utils.extract_export_zips(zips, dest)

    assert stats["extracted"] == 7
    assert stats["errors"] == 0
    assert not stats["aborted"]
    # Contents of all zips merged under one root
    assert os.path.isfile(os.path.join(dest, "json", "memories_history.json"))
    assert os.path.isfile(os.path.join(dest, "chat_media", "media~AAA.jpg"))
    assert os.path.isfile(os.path.join(dest, "chat_media", "media~BBB.jpg"))
    assert os.path.isfile(os.path.join(dest, "memories", "2024-06-15_abc-main.jpg"))
    assert os.path.isfile(os.path.join(dest, "memories", "2024-06-16_def-main.mp4"))


def test_extract_restores_mtime_from_zip(export_dir):
    zips = export_zip_utils.find_export_zips(str(export_dir))
    dest = export_zip_utils.default_extract_root(str(export_dir))
    export_zip_utils.extract_export_zips(zips, dest)

    target = os.path.join(dest, "memories", "2024-06-15_abc-main.jpg")
    expected = time.mktime(datetime(*FIXED_DT).timetuple())
    assert abs(os.path.getmtime(target) - expected) < 3


def test_extract_skips_existing_files(export_dir):
    zips = export_zip_utils.find_export_zips(str(export_dir))
    dest = export_zip_utils.default_extract_root(str(export_dir))
    export_zip_utils.extract_export_zips(zips, dest)
    stats = export_zip_utils.extract_export_zips(zips, dest)
    assert stats["extracted"] == 0
    assert stats["skipped"] == 7


def test_extract_reports_progress_and_stops(export_dir):
    zips = export_zip_utils.find_export_zips(str(export_dir))
    dest = export_zip_utils.default_extract_root(str(export_dir))
    calls = []
    stats = export_zip_utils.extract_export_zips(
        zips, dest, progress=lambda done, total: calls.append((done, total)))
    assert calls[-1] == (7, 7)

    # Stop immediately: nothing new happens, aborted is flagged
    stats = export_zip_utils.extract_export_zips(
        zips, dest, stop_check=lambda: True)
    assert stats["aborted"]


def test_extract_rejects_zip_slip_paths(tmp_path):
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("memories/ok-main.jpg", b"fine")
        z.writestr("../escape.txt", b"bad")
    dest = tmp_path / "out"
    stats = export_zip_utils.extract_export_zips([str(evil)], str(dest))
    assert stats["errors"] == 1
    assert not (tmp_path / "escape.txt").exists()
    assert (dest / "memories" / "ok-main.jpg").exists()


def test_extract_memories_json_pulls_json_folder_only(export_dir):
    zips = export_zip_utils.find_export_zips(str(export_dir))
    dest = export_zip_utils.default_extract_root(str(export_dir))
    json_path = export_zip_utils.extract_memories_json(zips, dest)

    assert json_path is not None
    assert json_path.endswith("memories_history.json")
    assert os.path.isfile(json_path)
    # chat_history.json comes along; media does not
    assert os.path.isfile(os.path.join(dest, "json", "chat_history.json"))
    assert not os.path.exists(os.path.join(dest, "chat_media"))
    assert not os.path.exists(os.path.join(dest, "memories"))


def test_extract_memories_json_none_when_absent(tmp_path):
    _make_zip(tmp_path / "mydata~1-2.zip", {"memories/a-main.jpg": b"x"})
    zips = export_zip_utils.find_export_zips(str(tmp_path))
    dest = export_zip_utils.default_extract_root(str(tmp_path))
    assert export_zip_utils.extract_memories_json(zips, dest) is None
