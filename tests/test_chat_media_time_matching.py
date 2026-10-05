"""Time-based matching of chat media to snap_history.json (issue #11)."""

import json
import os
import time
from datetime import datetime, timezone

import chat_media_utils as cmu


def _utc(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def _make_file(folder, name, utc_str, zip_rounding=0):
    """Create a file as an extractor would: zip time (UTC) read as local."""
    path = os.path.join(folder, name)
    with open(path, "wb") as f:
        f.write(b"\xff\xd8\xff\xe0")
    wall = datetime.strptime(utc_str, "%Y-%m-%d %H:%M:%S")
    ts = time.mktime(wall.timetuple()) - zip_rounding
    os.utime(path, (ts, ts))
    return path


def _snap(created, conv_title=None, media_type="IMAGE"):
    return {"From": "me", "Media Type": media_type, "Created": created,
            "Conversation Title": conv_title, "IsSender": True}


def _index(tmp_path, snaps):
    json_dir = tmp_path / "json"
    json_dir.mkdir()
    (json_dir / "snap_history.json").write_text(json.dumps(snaps), encoding="utf-8")
    return cmu.build_chat_index(str(json_dir))


def _scan(tmp_path):
    return cmu.scan_chat_media(str(tmp_path / "chat_media"))


def _setup(tmp_path):
    media = tmp_path / "chat_media"
    media.mkdir()
    return media


def test_export_mtime_reinterprets_local_wall_clock_as_utc(tmp_path):
    media = _setup(tmp_path)
    path = _make_file(media, "2024-06-15_b~AAA.jpg", "2024-06-15 21:59:24")
    record = {"path": path, "date": "2024-06-15"}
    assert cmu.get_export_mtime(record) == _utc("2024-06-15 21:59:24")


def test_time_match_when_ordinal_counts_disagree(tmp_path):
    media = _setup(tmp_path)
    _make_file(media, "2024-06-15_b~AAA.jpg", "2024-06-15 10:00:00", zip_rounding=1)
    _make_file(media, "2024-06-15_b~BBB.jpg", "2024-06-15 12:00:00")
    index = _index(tmp_path, {
        "alice": [_snap("2024-06-15 10:00:01 UTC")],
        "bob": [_snap("2024-06-15 12:00:00 UTC"),
                _snap("2024-06-15 15:00:00 UTC")],  # snap with no saved file
    })
    standalone = _scan(tmp_path)["standalone"]
    direct, via_snap = cmu.match_standalone(standalone, index)

    assert (direct, via_snap) == (0, 2)
    by_name = {r["fname"]: r for r in standalone}
    assert cmu.conversation_folder(by_name["2024-06-15_b~AAA.jpg"]) == "alice"
    assert cmu.conversation_folder(by_name["2024-06-15_b~BBB.jpg"]) == "bob"


def test_multi_recipient_snap_gets_extra_folders(tmp_path):
    media = _setup(tmp_path)
    _make_file(media, "2024-06-15_b~AAA.jpg", "2024-06-15 10:00:00")
    index = _index(tmp_path, {
        "alice": [_snap("2024-06-15 10:00:00 UTC")],
        "Ski Trip": [_snap("2024-06-15 10:00:00 UTC", conv_title="Ski Trip")],
    })
    standalone = _scan(tmp_path)["standalone"]
    cmu.match_standalone(standalone, index)

    record = standalone[0]
    folders = {cmu.conversation_folder(record)} | set(cmu.extra_conversation_folders(record))
    assert folders == {"alice", "Ski Trip"}


def test_file_outside_window_is_not_time_matched(tmp_path):
    media = _setup(tmp_path)
    _make_file(media, "2024-06-15_b~AAA.jpg", "2024-06-15 10:00:00")
    index = _index(tmp_path, {
        "alice": [_snap("2024-06-15 10:05:00 UTC"), _snap("2024-06-15 11:00:00 UTC")],
    })
    standalone = _scan(tmp_path)["standalone"]
    _, via_snap = cmu.match_standalone(standalone, index)

    # Two snaps vs one file: ordinal pairing refuses, nothing is guessed
    assert via_snap == 0
    assert cmu.conversation_folder(standalone[0]) == cmu.UNSORTED_FOLDER


def test_zip_group_time_match_wins_over_ordinal(tmp_path):
    media = _setup(tmp_path)
    _make_file(media, "2024-06-15_media~zip-1111.jpg", "2024-06-15 09:00:00")
    index = _index(tmp_path, {
        "alice": [_snap("2024-06-15 09:00:00 UTC")],
        "bob": [_snap("2024-06-15 14:00:00 UTC")],
    })
    scan = _scan(tmp_path)
    cmu.match_standalone(scan["standalone"], index)
    cmu.match_zip_groups(scan["zip_by_date"], index, set())

    record = scan["zip_by_date"]["2024-06-15"]["media"][0]
    assert cmu.conversation_folder(record) == "alice"


# ---- offset calibration: the extracting machine's zone is not trusted ----

def _make_epoch_file(folder, name, utc_str, shift_seconds):
    """File whose mtime = true UTC time + an arbitrary extraction shift."""
    path = os.path.join(folder, name)
    with open(path, "wb") as f:
        f.write(b"\xff\xd8\xff\xe0")
    ts = _utc(utc_str).timestamp() + shift_seconds
    os.utime(path, (ts, ts))
    return path


def test_calibration_detects_shift_independent_of_machine_zone(tmp_path):
    media = _setup(tmp_path)
    snaps = {"alice": [], "bob": []}
    # A shift (+5.5h) no machine here would plausibly have, with decoys
    for i, conv in enumerate(["alice", "bob", "alice", "bob"]):
        stamp = f"2024-06-15 {8 + i}:{10 + 7 * i:02d}:{10 + 13 * i:02d}"
        _make_epoch_file(media, f"2024-06-15_b~F{i}.jpg", stamp, 5.5 * 3600)
        snaps[conv].append(_snap(stamp + " UTC"))
    index = _index(tmp_path, snaps)
    standalone = _scan(tmp_path)["standalone"]

    assert cmu.calibrate_time_offsets(standalone, index) == [5.5 * 3600]
    direct, via_snap = cmu.match_standalone(standalone, index)
    assert via_snap == 4
    for r in standalone:
        assert cmu.conversation_folder(r) in ("alice", "bob")
        assert abs((r["match"]["_dt"] - r["_mtime"]).total_seconds()) < 1


def test_calibration_handles_dst_change_within_export(tmp_path):
    media = _setup(tmp_path)
    snaps = {"alice": []}
    for day, shift in (("2024-03-05", 3600), ("2024-07-05", 7200)):
        for i in range(4):
            stamp = f"{day} 1{i}:{20 + 11 * i}:{20 + 9 * i:02d}"
            _make_epoch_file(media, f"{day}_b~{day}{i}.jpg", stamp, shift)
            snaps["alice"].append(_snap(stamp + " UTC"))
    index = _index(tmp_path, snaps)
    standalone = _scan(tmp_path)["standalone"]

    assert cmu.calibrate_time_offsets(standalone, index) == [3600, 7200]
    for r in standalone:
        # mtime is corrected per date, not with one global offset
        assert abs((r["_mtime"] - _utc(r["date"] + " 00:00:00")).total_seconds()) < 86400


def test_calibration_uses_chat_history_media_ids(tmp_path):
    media = _setup(tmp_path)
    _make_epoch_file(media, "2024-06-15_b~KNOWN.jpg", "2024-06-15 10:00:00", -4 * 3600)
    json_dir = tmp_path / "json"
    json_dir.mkdir()
    (json_dir / "chat_history.json").write_text(json.dumps({"carol": [{
        "From": "carol", "Media Type": "MEDIA", "Media IDs": "b~KNOWN",
        "Created": "2024-06-15 10:00:00 UTC", "Conversation Title": None,
    }]}), encoding="utf-8")
    index = cmu.build_chat_index(str(json_dir))
    standalone = _scan(tmp_path)["standalone"]

    assert cmu.calibrate_time_offsets(standalone, index) == [-4 * 3600]
    assert standalone[0]["_mtime"] == _utc("2024-06-15 10:00:00")
