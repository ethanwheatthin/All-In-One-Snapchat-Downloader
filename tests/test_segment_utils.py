"""Tests for segment_utils — JSON matching and split-recording detection.

Cases mirror patterns seen in a real 2017-2026 export.
"""
from datetime import datetime, timedelta

import segment_utils as su

LOC_A = "Latitude, Longitude: 10.0, 20.0"
LOC_B = "Latitude, Longitude: 11.0, 21.0"


def item(idx, date, mtype="Video", loc=LOC_A):
    return {"_json_idx": idx, "Date": date, "Media Type": mtype, "Location": loc}


def ts(date):
    return int(su.parse_json_date(date).timestamp())


def index(items):
    by_ts = {}
    for it in items:
        by_ts.setdefault(ts(it["Date"]), []).append(it)
    return by_ts


# --------------------------------------------------------------------------
# pick_json_entry
# --------------------------------------------------------------------------
def test_prefers_exact_second_over_earlier_neighbour():
    # A photo at :31 and a video segment at :32 — the video file must get :32
    photo = item(0, "2021-01-20 13:52:31 UTC", "Image")
    video = item(1, "2021-01-20 13:52:32 UTC")
    got = su.pick_json_entry(index([photo, video]), ts(video["Date"]), "Video", set())
    assert got is video


def test_dos_rounding_down_still_matches_next_second():
    video = item(0, "2019-01-18 19:39:59 UTC")
    # zip DOS time rounded down to :58
    got = su.pick_json_entry(index([video]), ts(video["Date"]) - 1, "Video", set())
    assert got is video


def test_media_type_wins_over_distance():
    photo = item(0, "2020-10-18 12:36:01 UTC", "Image")
    video = item(1, "2020-10-18 12:36:02 UTC")
    got = su.pick_json_entry(index([photo, video]), ts(photo["Date"]), "Video", set())
    assert got is video


def test_claimed_entries_are_not_reused():
    a = item(0, "2021-01-20 13:53:22 UTC")
    b = item(1, "2021-01-20 13:53:24 UTC", loc=LOC_B)
    idx = index([a, b])
    claimed = set()
    first = su.pick_json_entry(idx, ts(a["Date"]), "Video", claimed)
    second = su.pick_json_entry(idx, ts(a["Date"]) + 1, "Video", claimed)
    assert first is a and second is b


def test_identical_timestamps_go_to_different_files():
    a = item(0, "2017-08-26 11:27:53 UTC")
    b = item(1, "2017-08-26 11:27:53 UTC")
    idx = index([a, b])
    claimed = set()
    got = {su.pick_json_entry(idx, ts(a["Date"]), "Video", claimed)["_json_idx"]
           for _ in range(2)}
    assert got == {0, 1}


def test_no_match_outside_window():
    video = item(0, "2019-01-18 19:39:59 UTC")
    assert su.pick_json_entry(index([video]), ts(video["Date"]) + 5, "Video", set()) is None


def test_media_type_for():
    assert su.media_type_for("x-main.mp4") == "Video"
    assert su.media_type_for("x-main.MOV") == "Video"
    assert su.media_type_for("x-main.jpg") == "Image"


# --------------------------------------------------------------------------
# find_segment_groups
# --------------------------------------------------------------------------
def test_groups_10s_runs_at_same_location():
    items = [item(i, f"2019-01-18 19:{39 + (59 + 10 * i) // 60:02d}:{(59 + 10 * i) % 60:02d} UTC")
             for i in range(6)]
    groups = su.find_segment_groups(items)
    assert len(groups) == 1 and len(groups[0]) == 6


def test_burst_of_snaps_seconds_apart_is_not_a_group():
    items = [item(i, f"2019-12-13 11:28:{43 + 2 * i:02d} UTC") for i in range(6)]
    assert su.find_segment_groups(items) == []


def test_location_change_breaks_group():
    items = [item(0, "2020-01-01 10:00:00 UTC"),
             item(1, "2020-01-01 10:00:10 UTC", loc=LOC_B)]
    assert su.find_segment_groups(items) == []


def test_images_are_ignored():
    items = [item(0, "2020-01-01 10:00:00 UTC"),
             item(1, "2020-01-01 10:00:10 UTC", "Image"),
             item(2, "2020-01-01 10:00:20 UTC")]
    assert su.find_segment_groups(items) == []


# --------------------------------------------------------------------------
# split_runs
# --------------------------------------------------------------------------
T0 = datetime(2020, 1, 1, 10, 0, 0)


def parts(gaps_and_durations):
    out, t = [], T0
    for i, (gap, dur) in enumerate(gaps_and_durations):
        t = t + timedelta(seconds=gap)
        out.append((t, dur, i))
    return out


def test_real_segments_join():
    p = parts([(0, 10.06), (10, 10.49), (10, 10.49), (10, 10.5), (10, 10.52), (10, 4.9)])
    assert su.split_runs(p) == [[0, 1, 2, 3, 4, 5]]


def test_two_long_snaps_10s_apart_stay_separate():
    # same 20s video saved twice, 9-10s apart (seen 2023/2024)
    assert su.split_runs(parts([(0, 20.2), (9, 20.2)])) == []


def test_overlapping_clip_stays_separate():
    # 10s clip followed by another snap 4s later
    assert su.split_runs(parts([(0, 10.0), (4, 5.2)])) == []


def test_last_segment_may_be_short_or_long():
    assert su.split_runs(parts([(0, 10.0), (10, 30.0)])) == [[0, 1]]


def test_bad_link_splits_into_two_runs():
    p = parts([(0, 10.0), (10, 10.0), (10, 25.0), (10, 10.0), (10, 3.0)])
    assert su.split_runs(p) == [[0, 1, 2], [3, 4]]


def test_unknown_duration_does_not_join():
    assert su.split_runs(parts([(0, None), (10, 10.0)])) == []
