"""Animated stickers (GIF/WebP overlays) must stay animated when merged (issue #6)."""
import shutil
import subprocess
import zipfile

import pytest
from PIL import Image, ImageDraw

import zip_utils

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe required")

W, H = 320, 240


def _animated(path, fmt):
    """Red square sweeping left->right over a transparent canvas."""
    frames = []
    for i in range(8):
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(im).rectangle([i * 30, 100, i * 30 + 40, 140], fill=(255, 0, 0, 255))
        frames.append(im)
    kwargs = dict(save_all=True, append_images=frames[1:], duration=100, loop=0)
    if fmt == "gif":
        kwargs["disposal"] = 2
    else:
        kwargs["lossless"] = True
    frames[0].save(path, **kwargs)


def _red_x(video, t):
    """Left edge of the red square at time t, or None."""
    out = video.parent / f"frame_{t}.png"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", str(t), "-i", str(video),
                    "-frames:v", "1", str(out)], check=True)
    im = Image.open(out).convert("RGB")
    xs = [x for x in range(W)
          if (lambda p: p[0] > 180 and p[1] < 90 and p[2] < 90)(im.getpixel((x, 120)))]
    return min(xs) if xs else None


def _duration(video):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", str(video)],
                       capture_output=True, text=True, check=True)
    return float(r.stdout)


@pytest.fixture
def main_video(tmp_path):
    p = tmp_path / "main.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c=blue:size={W}x{H}:rate=25:duration=3", "-pix_fmt", "yuv420p",
                    str(p)], check=True)
    return p


@pytest.mark.parametrize("fmt", ["gif", "webp"])
def test_video_keeps_sticker_animation(tmp_path, main_video, fmt):
    ov = tmp_path / f"ov.{fmt}"
    _animated(ov, fmt)
    out = tmp_path / "out.mp4"
    ok, result = zip_utils.merge_video_overlay(str(main_video), str(ov), str(out))
    assert ok, result
    assert abs(_duration(out) - 3.0) < 0.3
    xs = {_red_x(out, t) for t in (0.1, 0.4, 0.7)}
    assert None not in xs and len(xs) == 3, f"sticker did not move: {xs}"


@pytest.mark.parametrize("fmt", ["gif", "webp"])
def test_photo_with_animated_sticker_becomes_looping_video(tmp_path, fmt):
    main = tmp_path / "main.jpg"
    Image.new("RGB", (W, H), (0, 0, 255)).save(main)
    ov = tmp_path / f"ov.{fmt}"
    _animated(ov, fmt)
    ok, result = zip_utils.merge_images(str(main), str(ov), str(tmp_path / "out.jpg"))
    assert ok, result
    assert result.endswith(".mp4")
    out = tmp_path / "out.mp4"
    assert _duration(out) >= 3.0
    xs = {_red_x(out, t) for t in (0.1, 0.4, 0.7)}
    assert None not in xs and len(xs) == 3, f"sticker did not move: {xs}"


def test_static_overlay_on_photo_unchanged(tmp_path):
    main = tmp_path / "main.jpg"
    Image.new("RGB", (W, H), (0, 0, 255)).save(main)
    ov = tmp_path / "ov.png"
    Image.new("RGBA", (W, H), (255, 0, 0, 255)).save(ov)
    ok, result = zip_utils.merge_images(str(main), str(ov), str(tmp_path / "out.jpg"))
    assert ok and result.endswith("out.jpg")


def test_zip_with_animated_overlay_on_photo(tmp_path):
    main = tmp_path / "abc-main.jpg"
    Image.new("RGB", (W, H), (0, 0, 255)).save(main)
    ov = tmp_path / "abc-overlay.webp"
    _animated(ov, "webp")
    z = tmp_path / "snap.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.write(main, main.name)
        zf.write(ov, ov.name)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    merged = zip_utils.process_zip_overlay(str(z), str(out_dir))
    assert len(merged) == 1 and merged[0].endswith(".mp4")
