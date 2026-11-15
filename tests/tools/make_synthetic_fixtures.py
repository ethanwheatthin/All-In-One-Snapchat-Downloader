"""Build look-alike test fixtures from a real Snapchat export sample.

Real memories are personal, so they live in the git-ignored
tests/fixtures/local/ (same layout as an extracted export: memories/,
chat_media/, json/). This script mirrors that folder into
tests/fixtures/synthetic/ with generated content that keeps everything the
pipeline cares about:

  * file names and folder layout (dates and IDs drive matching)
  * file modification times (used to match memories to the JSON)
  * video codec, profile, pixel format / color range, resolution, frame
    rate, display rotation, audio codec / sample rate / channels
  * image and overlay dimensions and formats (overlays keep transparency)
  * the JSON fields the pipeline reads, with personal data removed:
    usernames and conversation titles become placeholders, message text is
    dropped, GPS is rounded to whole degrees, and only entries for the
    fixture files' days are kept

Videos are shortened to 2 seconds so the synthetic set stays small. The
output still carries real dates, IDs and timings, so it is git-ignored too.

usage: python tests/tools/make_synthetic_fixtures.py [--src DIR] [--dst DIR]
Needs an ffmpeg build with libx264, libx265 and libwebp.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(os.path.dirname(HERE), "fixtures")
VIDEO_EXT = {".mp4", ".mov", ".m4v"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_SECONDS = 2.0


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}\n{r.stderr[-1500:]}")
    return r.stdout


def probe(path):
    return json.loads(run(["ffprobe", "-v", "error", "-print_format", "json",
                           "-show_format", "-show_streams", "-count_packets", path]))


def _rotation(stream):
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            return int(round(float(sd["rotation"])))
    rot = (stream.get("tags") or {}).get("rotate")
    return -int(rot) if rot else 0


def _fps(stream):
    num, _, den = (stream.get("avg_frame_rate") or "30/1").partition("/")
    try:
        fps = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        fps = 30.0
    return round(fps, 3) if 1 <= fps <= 120 else 30.0


def make_video(src, dst):
    info = probe(src)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    seconds = min(MAX_SECONDS, float(info["format"].get("duration") or MAX_SECONDS))
    w, h, fps = v["width"], v["height"], _fps(v)
    full_range = v.get("color_range") == "pc" or v.get("pix_fmt", "").startswith("yuvj")
    pix_fmt = "yuvj420p" if full_range else "yuv420p"
    profile = (v.get("profile") or "").lower()

    rot = _rotation(v)
    cmd = ["ffmpeg", "-y", "-v", "error"]
    # Display rotation is stored as metadata, exactly like the phone did.
    # -noautorotate stops ffmpeg baking that rotation into the pixels.
    if rot:
        cmd += ["-noautorotate", "-display_rotation", str(rot)]
    cmd += ["-f", "lavfi", "-i", f"testsrc2=s={w}x{h}:r={fps}:d={seconds}"]
    if a:
        cmd += ["-f", "lavfi", "-i",
                f"sine=frequency=440:sample_rate={a.get('sample_rate', 44100)}:duration={seconds}"]
    # Keep the source's stream order: Snapchat writes audio first.
    audio_first = bool(a) and a["index"] < v["index"]
    if audio_first:
        cmd += ["-map", "1:a", "-map", "0:v"]
    else:
        cmd += ["-map", "0:v"] + (["-map", "1:a"] if a else [])

    if v["codec_name"] == "hevc":
        cmd += ["-c:v", "libx265", "-crf", "35", "-preset", "fast", "-tag:v", "hvc1",
                "-x265-params", "log-level=error"]
        if profile in ("main", "main 10"):
            cmd += ["-profile:v", "main10" if "10" in profile else "main"]
    else:
        cmd += ["-c:v", "libx264", "-crf", "35", "-preset", "fast"]
        x264_profile = {"constrained baseline": "baseline", "baseline": "baseline",
                        "main": "main", "high": "high"}.get(profile)
        if x264_profile:
            cmd += ["-profile:v", x264_profile]
    cmd += ["-pix_fmt", pix_fmt]
    if v.get("color_range") in ("tv", "pc"):
        cmd += ["-color_range", v["color_range"]]
    for key, opt in (("color_primaries", "-color_primaries"), ("color_transfer", "-color_trc"),
                     ("color_space", "-colorspace")):
        if v.get(key) and v[key] != "unknown":
            cmd += [opt, v[key]]
    if a:
        cmd += ["-c:a", "aac", "-b:a", "64k", "-ac", str(a.get("channels", 2))]
    cmd += ["-shortest", "-movflags", "+faststart", "-f", "mp4", dst]
    run(cmd)


def make_image(src, dst):
    info = probe(src)
    v = info["streams"][0]
    # Snapchat sometimes stores WebP data under a .png name, so the real
    # codec (not the extension) decides the format.
    ext = {"mjpeg": ".jpg", "png": ".png", "webp": ".webp", "gif": ".gif"}.get(
        v.get("codec_name"), os.path.splitext(src)[1].lower())
    fmt = {".jpg": "image2", ".png": "image2", ".webp": "webp", ".gif": "gif"}[ext]
    w, h = v["width"], v["height"]
    frames = int(v.get("nb_read_packets") or 1)
    is_overlay = "overlay" in os.path.basename(src).lower()
    has_alpha = any(t in (v.get("pix_fmt") or "") for t in ("rgba", "bgra", "ya", "yuva", "pal8"))

    if is_overlay or (has_alpha and ext != ".jpg"):
        # Transparent canvas with an opaque box, like a caption overlay.
        lavfi = (f"color=c=black@0.0:s={w}x{h}:r=10,format=rgba,"
                 f"drawbox=x=0:y={h * 2 // 3}:w={w}:h={max(h // 12, 2)}:color=white@0.9:t=fill,"
                 f"drawbox=x={w // 10}:y={h // 10}:w={w // 4}:h={h // 8}:color=yellow@1.0:t=fill")
    else:
        lavfi = f"testsrc2=s={w}x{h}:r=10"
    animated = frames > 1 and ext in (".webp", ".gif")
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", lavfi]
    if animated:
        cmd += ["-frames:v", str(min(frames, 10))]
        if ext == ".webp":
            cmd += ["-c:v", "libwebp_anim", "-loop", "0"]
    else:
        cmd += ["-frames:v", "1", "-update", "1"]
        if ext == ".webp":
            cmd += ["-c:v", "libwebp"]
    if ext == ".jpg":
        cmd += ["-q:v", "8"]
        if w % 2 or h % 2:
            cmd += ["-pix_fmt", "yuvj444p"]
    if ext == ".png":
        cmd += ["-c:v", "png"]
    cmd += ["-f", fmt, dst]
    run(cmd)


def mirror_media(src_dir, dst_dir):
    os.makedirs(dst_dir, exist_ok=True)
    made = []
    for fname in sorted(os.listdir(src_dir)):
        src = os.path.join(src_dir, fname)
        ext = os.path.splitext(fname)[1].lower()
        dst = os.path.join(dst_dir, fname)
        if ext in VIDEO_EXT:
            make_video(src, dst)
        elif ext in IMAGE_EXT:
            make_image(src, dst)
        else:
            continue  # sidecars / unreadable files carry no media to mirror
        st = os.stat(src)
        os.utime(dst, (st.st_atime, st.st_mtime))
        made.append(fname)
    return made


# ---- JSON anonymisation ---------------------------------------------------

def _round_location(loc):
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)", loc or "")
    if not m:
        return loc
    lat, lon = round(float(m.group(1))), round(float(m.group(2)))
    return f"Latitude, Longitude: {lat:.1f}, {lon:.1f}"


def _days(names):
    return {n[:10] for n in names if re.match(r"\d{4}-\d{2}-\d{2}", n)}


def anonymise_memories(src, dst, days):
    data = json.load(open(src, encoding="utf-8"))
    keep = []
    for item in data.get("Saved Media", []):
        if item.get("Date", "")[:10] not in days:
            continue
        keep.append({"Date": item["Date"], "Media Type": item.get("Media Type", ""),
                     "Location": _round_location(item.get("Location", "")),
                     "Download Link": "", "Media Download Url": ""})
    json.dump({"Saved Media": keep}, open(dst, "w", encoding="utf-8"), indent=1)
    return len(keep)


class Names:
    def __init__(self, prefix):
        self.prefix, self.map = prefix, {}

    def __call__(self, name):
        if name in (None, "", "None"):
            return name
        return self.map.setdefault(name, f"{self.prefix}{len(self.map) + 1}")


def anonymise_history(src, dst, days, users, titles, near):
    """chat_history / snap_history: {conversation: [message, ...]}."""
    data = json.load(open(src, encoding="utf-8"))
    out = {}
    for conv, msgs in data.items():
        kept = []
        for m in msgs:
            day = (m.get("Created") or "")[:10]
            if day not in near or m.get("Media Type") == "TEXT":
                continue
            k = {"From": users(m.get("From")), "Media Type": m.get("Media Type"),
                 "Created": m.get("Created"),
                 "Conversation Title": titles(m.get("Conversation Title")),
                 "IsSender": m.get("IsSender"),
                 "Created(microseconds)": m.get("Created(microseconds)")}
            for key in ("IsSaved", "Media IDs"):
                if key in m:
                    k[key] = m[key]
            if "Content" in m:
                k["Content"] = "None"
            kept.append(k)
        if kept:
            out[users(conv)] = kept
    json.dump(out, open(dst, "w", encoding="utf-8"), indent=1)
    return sum(len(v) for v in out.values())


def _near_days(days):
    """Chat media can be stamped a day off its message (time zones)."""
    from datetime import date, timedelta
    out = set()
    for d in days:
        y, m, dd = map(int, d.split("-"))
        base = date(y, m, dd)
        out.update((base + timedelta(days=k)).isoformat() for k in (-1, 0, 1))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--src", default=os.path.join(FIXTURES, "local"))
    ap.add_argument("--dst", default=os.path.join(FIXTURES, "synthetic"))
    args = ap.parse_args()
    if not os.path.isdir(args.src):
        sys.exit(f"No real sample at {args.src} - copy part of an extracted export there first")
    if os.path.isdir(args.dst):
        shutil.rmtree(args.dst)

    mem = chat = []
    if os.path.isdir(os.path.join(args.src, "memories")):
        mem = mirror_media(os.path.join(args.src, "memories"), os.path.join(args.dst, "memories"))
    if os.path.isdir(os.path.join(args.src, "chat_media")):
        chat = mirror_media(os.path.join(args.src, "chat_media"), os.path.join(args.dst, "chat_media"))
    print(f"media: {len(mem)} memories files, {len(chat)} chat media files")

    jsrc, jdst = os.path.join(args.src, "json"), os.path.join(args.dst, "json")
    os.makedirs(jdst, exist_ok=True)
    if os.path.exists(os.path.join(jsrc, "memories_history.json")):
        n = anonymise_memories(os.path.join(jsrc, "memories_history.json"),
                               os.path.join(jdst, "memories_history.json"), _days(mem))
        print(f"memories_history.json: {n} entries")
    users, titles, near = Names("user"), Names("Group chat "), _near_days(_days(chat))
    for name in ("chat_history.json", "snap_history.json"):
        if os.path.exists(os.path.join(jsrc, name)):
            n = anonymise_history(os.path.join(jsrc, name), os.path.join(jdst, name),
                                  _days(chat), users, titles, near)
            print(f"{name}: {n} messages")


if __name__ == "__main__":
    main()
