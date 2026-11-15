"""End-to-end run of the local-export pipelines on an export-shaped fixture.

Runs the same memories and chat-media processing the GUI runs, headless,
then checks the output against the input (issue #14):

  * every file is processed, with no failures
  * H.264 videos without an overlay come out bit-identical (packet hashes)
    and keep their display rotation
  * videos with an overlay are re-encoded (the overlay is burned in)
  * HEVC is converted to H.264 by default
  * audio is always stream-copied
  * orientation, dates and GPS metadata survive
  * with the opt-in iCloud setting, every video is Main profile, limited range

Fixtures (both git-ignored; each is tested only when present, else skipped):
  tests/fixtures/local/      a sample copied from a real extracted export
  tests/fixtures/synthetic/  look-alikes built from it by
                             tests/tools/make_synthetic_fixtures.py
"""
import json
import os
import shutil
import subprocess
import sys
import types
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pytest

FIXTURES = os.path.join(ROOT, "tests", "fixtures")
HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
pytestmark = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")

VIDEO_EXT = (".mp4", ".mov", ".m4v")


# ---- helpers ---------------------------------------------------------------

def _files(root, exts=None):
    out = []
    for d, _, fs in os.walk(root):
        for f in fs:
            if exts is None or f.lower().endswith(exts):
                out.append(os.path.join(d, f))
    return sorted(out)


def _packet_hash(path, stream):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-map", f"0:{stream}:0?",
                        "-c", "copy", "-f", "hash", "-hash", "md5", "-"],
                       capture_output=True, text=True)
    return r.stdout.strip() or None


def _probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json",
                        "-show_format", "-show_streams", path],
                       capture_output=True, text=True, encoding="utf-8", check=True)
    j = json.loads(r.stdout)
    v = next(s for s in j["streams"] if s["codec_type"] == "video")
    rot = 0
    for sd in v.get("side_data_list") or []:
        if "rotation" in sd:
            rot = int(round(float(sd["rotation"])))
    w, h = v["width"], v["height"]
    portrait = (h > w) if rot % 180 == 0 else (w > h)
    tags = {k.lower(): val for k, val in (j["format"].get("tags") or {}).items()}
    return {
        "codec": v["codec_name"], "profile": (v.get("profile") or "").lower(),
        "color_range": v.get("color_range"), "pix_fmt": v.get("pix_fmt"),
        "rotation": rot % 360, "portrait": portrait,
        "has_audio": any(s["codec_type"] == "audio" for s in j["streams"]),
        "creation_time": tags.get("creation_time"),
        "location": tags.get("location") or tags.get("com.apple.quicktime.location.iso6709"),
        "video_hash": _packet_hash(path, "v"), "audio_hash": _packet_hash(path, "a"),
    }


class _Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


def _run(mode, export_dir, out_dir, monkeypatch):
    """Drive SnapchatDownloaderGUI's worker on a stand-in for the Tk app."""
    import download_snapchat_memories_gui as gui
    for name in ("showinfo", "showerror", "showwarning"):
        monkeypatch.setattr(gui.messagebox, name, lambda *a, **k: None)

    lines = []
    app = types.SimpleNamespace(
        stop_download=False,
        overlay_mode=_Var("merge"), use_gps_tz=_Var(True),
        skip_existing_local=_Var(False), stitch_segments_local=_Var(False),
        group_by_conversation=_Var(True),
        log=lambda message, refresh=True: lines.append(message),
        update_progress=lambda *a, **k: None,
        download_complete=lambda: None,
    )
    for meth in ("_detect_tz_offset", "_maybe_extract_export_zips",
                 "_process_local_file", "_process_chat_media_item"):
        setattr(app, meth, getattr(gui.SnapchatDownloaderGUI, meth).__get__(app))

    cls = gui.SnapchatDownloaderGUI
    if mode == "memories":
        cls.process_local_files_thread(
            app, os.path.join(export_dir, "json", "memories_history.json"),
            export_dir, out_dir)
    else:
        cls.process_chat_media_thread(app, os.path.join(export_dir, "chat_media"), out_dir)
    return "\n".join(lines)


def _source_videos(src_dir):
    """(path, probe, may_have_overlay) for every source video."""
    names = os.listdir(src_dir)
    out = []
    for f in sorted(names):
        if not f.lower().endswith(VIDEO_EXT):
            continue
        if "-main." in f:  # memories: <base>-main.mp4 + optional <base>-overlay.*
            base = f.split("-main.")[0]
            overlay = any(n.startswith(base + "-overlay") for n in names)
        else:  # chat media: only snap bundles (~zip / .zip.nomedia) carry captions
            overlay = "~zip-" in f or ".zip." in f
        out.append((f, _probe(os.path.join(src_dir, f)), overlay))
    return out


# ---- fixtures --------------------------------------------------------------

@pytest.fixture(params=["synthetic", "local"])
def export(request, tmp_path):
    src = os.path.join(FIXTURES, request.param)
    if not os.path.isdir(src):
        pytest.skip(f"no {request.param} fixture at {src}")
    dst = tmp_path / "export"
    shutil.copytree(src, dst)  # copy2 keeps mtimes, which matching relies on
    return str(dst)


@pytest.fixture
def policy():
    import video_utils
    saved = dict(video_utils.VIDEO_POLICY)
    video_utils.set_video_policy(icloud_compatible=False, transcode_hevc=True)
    yield video_utils
    video_utils.VIDEO_POLICY.clear()
    video_utils.VIDEO_POLICY.update(saved)


# ---- tests -----------------------------------------------------------------

def _check_videos(sources, out_videos, lossless_required):
    outs = {p: _probe(p) for p in out_videos}
    by_hash = {o["video_hash"]: o for o in outs.values()}
    src_audio = {s["audio_hash"] for _, s, _ in sources if s["audio_hash"]}

    assert len(outs) == len(sources)
    for name, s, overlay in sources:
        if s["codec"] == "h264" and not overlay and lossless_required(name):
            out = by_hash.get(s["video_hash"])
            assert out is not None, f"{name}: H.264 without overlay was re-encoded"
            assert out["rotation"] == s["rotation"], f"{name}: rotation lost"
        if overlay and "-main." in name:
            assert s["video_hash"] not in by_hash, f"{name}: overlay was not merged"
    for p, o in outs.items():
        assert o["codec"] == "h264", f"{p}: {o['codec']} not converted to H.264"
        if o["has_audio"]:
            assert o["audio_hash"] in src_audio, f"{p}: audio was re-encoded"
        assert o["creation_time"], f"{p}: no creation_time"
    assert (Counter(s["portrait"] for _, s, _ in sources)
            == Counter(o["portrait"] for o in outs.values())), "orientation changed"
    return outs


def test_memories_pipeline(export, tmp_path, monkeypatch, policy):
    src_dir = os.path.join(export, "memories")
    mains = [f for f in os.listdir(src_dir) if "-main." in f]
    out = str(tmp_path / "out")

    log = _run("memories", export, out, monkeypatch)

    assert f"Files processed:  {len(mains)} / {len(mains)}" in log, log[-2000:]
    assert "Failed:           0" in log, log[-2000:]
    assert len(_files(out)) == len(mains)

    outs = _check_videos(_source_videos(src_dir), _files(out, VIDEO_EXT), lambda n: True)
    for p, o in outs.items():
        assert o["location"], f"{p}: GPS location missing"

    from PIL import Image
    for p in _files(out, (".jpg", ".jpeg")):
        exif = Image.open(p).getexif()
        assert exif.get_ifd(0x8769).get(36867), f"{p}: no DateTimeOriginal"
        assert exif.get_ifd(0x8825), f"{p}: no GPS"


def test_chat_media_pipeline(export, tmp_path, monkeypatch, policy):
    src_dir = os.path.join(export, "chat_media")
    out = str(tmp_path / "out")

    log = _run("chatmedia", export, out, monkeypatch)

    assert "✗ Errors: 0" in log, log[-2000:]
    out_videos = [p for p in _files(out, VIDEO_EXT) if "unmatched_overlays" not in p]
    # Standalone sends never carry a caption, so they must stay bit-identical.
    _check_videos(_source_videos(src_dir), out_videos,
                  lambda n: "~zip-" not in n and ".zip." not in n)


def test_icloud_option_reencodes_to_main_limited_range(export, tmp_path, monkeypatch, policy):
    policy.set_video_policy(icloud_compatible=True, transcode_hevc=True)
    out = str(tmp_path / "out")

    log = _run("memories", export, out, monkeypatch)

    assert "Failed:           0" in log, log[-2000:]
    for p in _files(out, VIDEO_EXT):
        o = _probe(p)
        assert o["codec"] == "h264" and o["profile"] in ("main", "constrained baseline", "baseline"), \
            f"{p}: {o['profile']}"
        assert o["color_range"] != "pc" and not (o["pix_fmt"] or "").startswith("yuvj"), \
            f"{p}: still full range"
