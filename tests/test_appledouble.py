"""Tests for macOS AppleDouble / metadata filtering (GitHub issue #5)."""

import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fs_utils
import export_zip_utils


@pytest.mark.parametrize("name", [
    "._2024-01-01_1-main.jpg",
    "memories/._2024-01-01_1-main.jpg",
    "memories\\._2024-01-01_1-main.jpg",
    ".DS_Store",
    "chat_media/.DS_Store",
    "__MACOSX/memories/._x.jpg",
    "__MACOSX",
])
def test_is_macos_metadata_true(name):
    assert fs_utils.is_macos_metadata(name) is True


@pytest.mark.parametrize("name", [
    "2024-01-01_1-main.jpg",
    "memories/2024-01-01_1-main.mp4",
    "json/memories_history.json",
    "my._weird.jpg",  # ._ not at the start of the basename
    "",
])
def test_is_macos_metadata_false(name):
    assert fs_utils.is_macos_metadata(name) is False


FIXED_DT = (2024, 6, 15, 12, 30, 0)


def _make_zip(path, members):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in members.items():
            z.writestr(zipfile.ZipInfo(name, date_time=FIXED_DT), data)


@pytest.fixture
def export_dir(tmp_path):
    _make_zip(tmp_path / "mydata~1.zip", {
        "json/memories_history.json": b'{"Saved Media": []}',
        "memories/2024-06-15_abc-main.jpg": b"jpeg-main",
        "memories/._2024-06-15_abc-main.jpg": b"\x00\x05\x16\x07appledouble",
        "._.DS_Store": b"\x00\x05\x16\x07",
        ".DS_Store": b"junk",
        "__MACOSX/memories/._2024-06-15_abc-main.jpg": b"\x00\x05\x16\x07",
    })
    return tmp_path


def test_extract_skips_appledouble_files(export_dir):
    zips = export_zip_utils.find_export_zips(str(export_dir))
    dest = export_zip_utils.default_extract_root(str(export_dir))
    stats = export_zip_utils.extract_export_zips(zips, dest)

    assert stats["errors"] == 0
    assert os.path.isfile(os.path.join(dest, "memories", "2024-06-15_abc-main.jpg"))
    assert not os.path.exists(os.path.join(dest, "memories", "._2024-06-15_abc-main.jpg"))
    assert not os.path.exists(os.path.join(dest, ".DS_Store"))
    assert not os.path.exists(os.path.join(dest, "._.DS_Store"))
    assert not os.path.exists(os.path.join(dest, "__MACOSX"))
    # Only the two real files counted
    assert stats["extracted"] == 2


def test_extract_memories_json_ignores_appledouble(export_dir):
    zips = export_zip_utils.find_export_zips(str(export_dir))
    dest = export_zip_utils.default_extract_root(str(export_dir))
    json_path = export_zip_utils.extract_memories_json(zips, dest)
    assert json_path is not None and json_path.endswith("memories_history.json")
