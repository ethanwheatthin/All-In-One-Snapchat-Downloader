"""Run the split-video joiner on a real export and build a page to review it.

Older Snapchat memories store a long recording as a run of ~10 s clips,
which the memories pipeline joins back into one video. Unit tests can't tell
whether a join *looks* right, so this tool runs the real pipeline (headless)
on just the part of an export that matters and writes:

  OUT/review/index.html   every joined video next to the clips it was made
                          from, failures and warnings first
  OUT/review/summary.json the same results, machine-readable
  OUT/run.log             the pipeline log

Only memories within --window seconds of a split-video candidate are
processed, so neighbouring photos and snaps that could be mis-matched are
still present, but a run takes minutes instead of hours.

Checks on each joined video:
  FAIL  its length differs from its clips' total by more than 1 s
        (e.g. a stretched timeline), or the join failed
  WARN  video and audio lengths differ by more than 1 s
  WARN  a pre-2021 recording whose last clip is longer than 11.5 s
        (Snapchat capped snaps at ~10 s, so it may be a separate video)
  WARN  no creation date or GPS location written

Exit code is 1 when any join FAILs, so it can gate a change.

usage:
  python tests/tools/review_joins.py EXPORT_DIR OUT_DIR [--window 15] [--limit N]
  python tests/tools/review_joins.py --review-only OUT_DIR

--review-only skips processing and rebuilds the page and summary from the
run already in OUT_DIR, e.g. after changing the checks or the page.

EXPORT_DIR is the folder of mydata~*.zip files as downloaded from Snapchat,
or an extracted export (json/memories_history.json + memories/). OUT_DIR
must not be inside EXPORT_DIR. Output comes from personal data: keep it out
of the repo. Needs ffmpeg and ffprobe on PATH.
"""
import argparse
import glob
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
import types
import zipfile
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import export_zip_utils  # noqa: E402
import segment_utils  # noqa: E402

VIDEO_EXTS = (".mp4", ".mov", ".m4v")
LENGTH_SLACK = 1.0
AV_SLACK = 1.0
LAST_CLIP_MAX = 11.5


# ---- build the input subset -------------------------------------------------

def _candidate_seconds(items, window, limit):
    """JSON times (as naive-local timestamps, like the app) near a candidate."""
    groups = segment_utils.find_segment_groups(items)
    if limit:
        groups = groups[:limit]
    near = set()
    for group in groups:
        for it in group:
            t = int(segment_utils.parse_json_date(it["Date"]).timestamp())
            near.update(range(t - window, t + window + 1))
    return groups, near


def _base(name):
    return re.split(r"-(?:main|overlay)\.", os.path.basename(name))[0]


def _subset_from_zips(zips, mem_dir, near):
    """Extract memories near a candidate, with the exact ZIP entry time."""
    count = 0
    for zp in zips:
        with zipfile.ZipFile(zp) as zf:
            infos = [i for i in zf.infolist()
                     if "memories/" in i.filename and not i.is_dir()]
            keep = set()
            for info in infos:
                if "-main." not in info.filename:
                    continue
                ut = export_zip_utils._zip_ut_mtime(info)
                wall = (datetime.fromtimestamp(ut, timezone.utc).timetuple()[:6]
                        if ut is not None else info.date_time)
                # same convention as extraction: UTC wall clock read as local
                if int(time.mktime(datetime(*wall).timetuple())) in near:
                    keep.add(_base(info.filename))
            for info in infos:
                if _base(info.filename) not in keep:
                    continue
                target = os.path.join(mem_dir, os.path.basename(info.filename))
                if not os.path.exists(target):
                    with zf.open(info) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    export_zip_utils._restore_mtime(target, info)
                count += 1
    return count


def _subset_from_folder(src_mem, mem_dir, near):
    names = os.listdir(src_mem)
    keep = {_base(n) for n in names
            if "-main." in n and int(os.path.getmtime(os.path.join(src_mem, n))) in near}
    count = 0
    for n in names:
        if _base(n) in keep:
            target = os.path.join(mem_dir, n)
            if not os.path.exists(target):
                shutil.copy2(os.path.join(src_mem, n), target)
            count += 1
    return count


def build_subset(export_dir, inp, window, limit):
    os.makedirs(os.path.join(inp, "memories"), exist_ok=True)
    os.makedirs(os.path.join(inp, "json"), exist_ok=True)
    zips = export_zip_utils.find_export_zips(export_dir)
    if zips:
        json_src = export_zip_utils.extract_memories_json(
            zips, os.path.join(inp, "_json_tmp"))
    else:
        json_src = os.path.join(export_dir, "json", "memories_history.json")
    if not json_src or not os.path.isfile(json_src):
        sys.exit(f"memories_history.json not found under {export_dir}")
    json_dst = os.path.join(inp, "json", "memories_history.json")
    shutil.copy2(json_src, json_dst)
    shutil.rmtree(os.path.join(inp, "_json_tmp"), ignore_errors=True)

    with open(json_dst, encoding="utf-8") as fh:
        items = json.load(fh).get("Saved Media", [])
    groups, near = _candidate_seconds(items, window, limit)
    mem_dir = os.path.join(inp, "memories")
    if zips:
        files = _subset_from_zips(zips, mem_dir, near)
    else:
        files = _subset_from_folder(os.path.join(export_dir, "memories"), mem_dir, near)
    return json_dst, len(groups), sum(map(len, groups)), files


# ---- run the pipeline headless ---------------------------------------------

class _Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


def run_pipeline(json_path, inp, out, log_path):
    import download_snapchat_memories_gui as gui
    for name in ("showinfo", "showerror", "showwarning"):
        setattr(gui.messagebox, name, lambda *a, **k: None)
    with open(log_path, "w", encoding="utf-8") as logf:
        def log(message, refresh=True):
            logf.write(f"{message}\n")
            logf.flush()
        app = types.SimpleNamespace(
            stop_download=False, overlay_mode=_Var("merge"), use_gps_tz=_Var(True),
            skip_existing_local=_Var(False), stitch_segments_local=_Var(True),
            group_by_conversation=_Var(True),
            log=log, update_progress=lambda *a, **k: None, download_complete=lambda: None,
        )
        for meth in ("_detect_tz_offset", "_maybe_extract_export_zips", "_process_local_file"):
            setattr(app, meth, getattr(gui.SnapchatDownloaderGUI, meth).__get__(app))
        gui.SnapchatDownloaderGUI.process_local_files_thread(app, json_path, inp, out)


# ---- check the joins -------------------------------------------------------

def _probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json",
                        "-show_format", "-show_streams", path],
                       capture_output=True, text=True, encoding="utf-8")
    j = json.loads(r.stdout or "{}")
    fmt = j.get("format", {})
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    streams = {}
    for s in j.get("streams", []):
        streams.setdefault(s.get("codec_type"), s)
    v, a = streams.get("video", {}), streams.get("audio")
    return {
        "duration": float(fmt.get("duration") or 0),
        "video": float(v.get("duration") or fmt.get("duration") or 0),
        "audio": float(a["duration"]) if a and a.get("duration") else None,
        "codec": v.get("codec_name"),
        "created": tags.get("creation_time", ""),
        "location": tags.get("com.apple.quicktime.location.iso6709") or tags.get("location", ""),
    }


def parse_joins(log_path):
    """[(joined name, [clip names], ok, error)] from the pipeline log."""
    joins, cur = [], None
    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            m = re.search(r"Joining \d+ segments → (.+)$", line)
            if m:
                cur = {"file": m.group(1).strip(), "clips": [], "ok": False, "error": "",
                       "line": line}
                joins.append(cur)
                continue
            if cur is None:
                continue
            if line.startswith("  from: "):
                cur["clips"] = [c.strip() for c in line[8:].split(",")]
            elif "✓ Stitched" in line:
                cur["ok"] = True
            elif "✗ Stitch failed" in line:
                cur["error"] = line.split("failed:", 1)[-1].strip()
    if any(not j["clips"] for j in joins):
        _clips_from_file_log(log_path, joins)
    return joins


def _clips_from_file_log(log_path, joins):
    """Older runs didn't log a join's clips: rebuild them from per-file lines.

    A join's first clip is the file whose output name it extends; the rest
    are the next videos at the same location, each 9-11 s later.
    """
    files, cur = [], None
    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"^\[\d+/\d+\] (\S+-main\.\w+)$", line.rstrip("\n"))
            if m:
                cur = {"src": m.group(1), "date": None, "loc": None, "out": None}
                files.append(cur)
                continue
            if cur is None:
                continue
            m = re.search(r"JSON match: (.+ UTC)", line)
            if m:
                cur["date"] = segment_utils.parse_json_date(m.group(1))
            m = re.search(r"📍 (.+)$", line)
            if m:
                cur["loc"] = m.group(1).strip()
            m = re.search(r"→ (\S+)$", line)
            if m and cur["out"] is None:
                cur["out"] = m.group(1)
    videos = sorted((f for f in files if f["date"] and f["out"]
                     and f["out"].lower().endswith(VIDEO_EXTS)), key=lambda f: f["date"])
    by_stem = {os.path.splitext(f["out"])[0]: f for f in videos}
    for j in joins:
        if j["clips"]:
            continue
        n = int(re.search(r"Joining (\d+)", j.get("line", "")).group(1)) if j.get("line") else 0
        first = by_stem.get(re.sub(r"_joined(_\d+)?$", "", os.path.splitext(j["file"])[0]))
        if not first:
            continue
        chain = [first]
        for f in videos:
            if n and len(chain) == n:
                break
            gap = (f["date"] - chain[-1]["date"]).total_seconds()
            if 9 <= gap <= 11 and f["loc"] == first["loc"]:
                chain.append(f)
        j["clips"] = [f["out"] for f in chain]
        j["sources"] = {f["out"]: f["src"] for f in chain}


def check(joins, out):
    paths = {}
    for d, _, fs in os.walk(out):
        for f in fs:
            paths.setdefault(f, os.path.join(d, f))
    raw_dir = os.path.join(os.path.dirname(out), "input", "memories")
    results = []
    for j in joins:
        clips = []
        for c in j["clips"]:
            path, raw = paths.get(c), False
            # Older versions deleted a joined clip's output; fall back to the
            # export file it came from (no caption overlay burned in).
            src = j.get("sources", {}).get(c)
            if not path and src and os.path.isfile(os.path.join(raw_dir, src)):
                path, raw = os.path.join(raw_dir, src), True
            clips.append({"file": c, "path": path, "raw": raw})
        r = {"file": j["file"], "path": paths.get(j["file"]), "status": "ok", "issues": [],
             "clips": clips}
        if not j["ok"] or not r["path"]:
            r.update(status="fail", issues=[f"Join failed: {j['error'] or 'no output'}"])
            results.append(r)
            continue
        info = _probe(r["path"])
        r.update(info)
        for c in r["clips"]:
            c["duration"] = _probe(c["path"])["duration"] if c["path"] else None
        known = [c["duration"] for c in r["clips"] if c["duration"] is not None]
        total = sum(known)
        r["clips_total"] = total
        if not r["clips"]:
            r["issues"].append("Clips unknown: the log doesn't say what this was made from")
        elif len(known) == len(r["clips"]) and abs(info["duration"] - total) > LENGTH_SLACK:
            r["issues"].append(f"Length {info['duration']:.1f}s, but the clips add up to "
                               f"{total:.1f}s (timeline stretched or a clip lost)")
            r["status"] = "fail"
        if info["audio"] is not None and abs(info["video"] - info["audio"]) > AV_SLACK:
            r["issues"].append(f"Video {info['video']:.1f}s vs audio {info['audio']:.1f}s "
                               f"(audio may drift out of sync)")
        year = re.match(r"(\d{4})", j["file"])
        last = r["clips"][-1]["duration"] if r["clips"] else None
        if year and int(year.group(1)) < 2021 and last and last > LAST_CLIP_MAX:
            r["issues"].append(f"Last clip is {last:.1f}s, longer than a pre-2021 snap "
                               f"(may be a separate video)")
        if not info["created"] or not info["location"]:
            r["issues"].append("Missing creation date or GPS location")
        if r["issues"] and r["status"] == "ok":
            r["status"] = "warn"
        results.append(r)
    return results


# ---- the review page -------------------------------------------------------

PAGE_CSS = """
:root{--bg:#f6f6f4;--card:#fff;--ink:#1d1d1b;--muted:#6b6b66;--line:#e3e2dd;
--fail:#c2410c;--fail-bg:#fff4ed;--warn:#a16207;--warn-bg:#fefbe8;--ok:#15803d}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--card:#1f1f1d;--ink:#ecebe6;
--muted:#9b9a94;--line:#33332f;--fail:#fb923c;--fail-bg:#2a1a10;--warn:#facc15;
--warn-bg:#29240f;--ok:#4ade80}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
header{padding:28px 24px 8px;max-width:1400px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px}
.sub{color:var(--muted);margin:0}
.stats{display:flex;flex-wrap:wrap;gap:10px;margin:18px 0 6px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;min-width:120px}
.stat b{display:block;font-size:22px}
.stat span{color:var(--muted);font-size:12px}
.stat.fail b{color:var(--fail)}.stat.warn b{color:var(--warn)}.stat.ok b{color:var(--ok)}
nav{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
nav button{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;
padding:6px 12px;font:inherit;cursor:pointer}
nav button.on{background:var(--ink);color:var(--bg);border-color:var(--ink)}
main{max-width:1400px;margin:0 auto;padding:12px 24px 40px;
display:grid;grid-template-columns:repeat(auto-fill,minmax(380px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;
display:flex;flex-direction:column;gap:8px}
.card.fail{border-color:var(--fail);background:var(--fail-bg)}
.card.warn{border-color:var(--warn);background:var(--warn-bg)}
.top{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.name{font-weight:600;word-break:break-all;font-size:13px}
.badge{font-size:11px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;white-space:nowrap}
.fail .badge{color:var(--fail)}.warn .badge{color:var(--warn)}.ok .badge{color:var(--ok)}
.issues{margin:0;padding-left:18px;font-size:12.5px}
.meta{color:var(--muted);font-size:12px}
video{width:100%;max-height:360px;background:#000;border-radius:8px;display:block}
.label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.clips{display:grid;grid-template-columns:repeat(auto-fill,minmax(105px,1fr));gap:6px}
.clips video{max-height:150px;border-radius:6px}
.clips figure{margin:0}.clips figcaption{font-size:11px;color:var(--muted);margin-top:2px}
@media (max-width:440px){main,header{padding-left:16px;padding-right:16px}main{grid-template-columns:1fr}}
"""

PAGE_JS = """
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('nav button').forEach(x=>x.classList.toggle('on',x===b));
  const f=b.dataset.f;
  document.querySelectorAll('.card').forEach(c=>c.hidden=!(f==='all'||c.classList.contains(f)));
});
"""


def _src(path, page_dir):
    return html.escape(os.path.relpath(path, page_dir).replace("\\", "/"))


def write_page(results, page_dir, title):
    order = {"fail": 0, "warn": 1, "ok": 2}
    results = sorted(results, key=lambda r: (order[r["status"]], r["file"]))
    counts = {s: sum(r["status"] == s for r in results) for s in order}
    clips = sum(len(r["clips"]) for r in results)
    minutes = sum(r.get("duration", 0) for r in results) / 60
    cards = []
    for r in results:
        issues = "".join(f"<li>{html.escape(i)}</li>" for i in r["issues"])
        joined = (f'<video src="{_src(r["path"], page_dir)}#t=0.1" controls preload="metadata"></video>'
                  if r.get("path") else "")
        strip = "".join(
            f'<figure><video src="{_src(c["path"], page_dir)}#t=0.1" controls preload="metadata"></video>'
            f'<figcaption>Clip {i} · {c["duration"]:.1f}s'
            f'{" · original, no caption" if c.get("raw") else ""}</figcaption></figure>'
            if c.get("path") and c.get("duration") is not None else
            f'<figure><figcaption>Clip {i} · missing</figcaption></figure>'
            for i, c in enumerate(r["clips"], 1))
        meta = ""
        if r.get("path"):
            meta = (f'{len(r["clips"])} clips · {r["duration"]:.1f}s '
                    f'(clips add up to {r["clips_total"]:.1f}s) · {r.get("codec") or "?"}')
        cards.append(
            f'<article class="card {r["status"]}"><div class="top">'
            f'<span class="name">{html.escape(r["file"])}</span>'
            f'<span class="badge">{r["status"]}</span></div>'
            + (f'<ul class="issues">{issues}</ul>' if issues else "")
            + f'<div class="label">Joined</div>{joined}<div class="meta">{meta}</div>'
            f'<div class="label">Made from</div><div class="clips">{strip}</div></article>')
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Join review</title><style>{PAGE_CSS}</style></head><body>
<header><h1>Join review</h1><p class="sub">{html.escape(title)}</p>
<div class="stats">
<div class="stat"><b>{len(results)}</b><span>joined videos</span></div>
<div class="stat"><b>{clips}</b><span>clips used</span></div>
<div class="stat fail"><b>{counts['fail']}</b><span>fail</span></div>
<div class="stat warn"><b>{counts['warn']}</b><span>warnings</span></div>
<div class="stat ok"><b>{counts['ok']}</b><span>look correct</span></div>
<div class="stat"><b>{minutes:.0f} min</b><span>of joined video</span></div>
</div>
<nav><button class="on" data-f="all">All</button><button data-f="fail">Fail</button>
<button data-f="warn">Warnings</button><button data-f="ok">Look correct</button></nav>
</header><main>{''.join(cards)}</main><script>{PAGE_JS}</script></body></html>"""
    with open(os.path.join(page_dir, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page)
    return counts


def review(out_dir, title):
    """Check the joins of a finished run in out_dir and (re)write its page."""
    page_dir, log_path = os.path.join(out_dir, "review"), os.path.join(out_dir, "run.log")
    os.makedirs(page_dir, exist_ok=True)
    results = check(parse_joins(log_path), os.path.join(out_dir, "output"))
    with open(os.path.join(page_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=1)
    counts = write_page(results, page_dir, title)
    print(f"{len(results)} joined: {counts['fail']} fail, {counts['warn']} warn, "
          f"{counts['ok']} ok")
    for r in results:
        if r["status"] != "ok":
            print(f"  {r['status'].upper():4} {r['file']}: {'; '.join(r['issues'])}")
    print(f"Review page: {os.path.join(page_dir, 'index.html')}")
    return 1 if counts["fail"] else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("export_dir", nargs="?")
    ap.add_argument("out_dir", nargs="?")
    ap.add_argument("--window", type=int, default=15,
                    help="seconds around each candidate clip to include (default 15)")
    ap.add_argument("--limit", type=int, default=0,
                    help="only the first N candidate groups, oldest first (default all)")
    ap.add_argument("--review-only", metavar="OUT_DIR",
                    help="skip processing: rebuild the review page from a finished run")
    args = ap.parse_args()

    if args.review_only:
        out_dir = os.path.abspath(args.review_only)
        if not os.path.isfile(os.path.join(out_dir, "run.log")):
            sys.exit(f"No run.log in {out_dir}: run the tool on an export first")
        stamp = datetime.fromtimestamp(os.path.getmtime(os.path.join(out_dir, "run.log")))
        return review(out_dir, f"{os.path.basename(out_dir)} · run of {stamp:%Y-%m-%d %H:%M}")
    if not (args.export_dir and args.out_dir):
        ap.error("EXPORT_DIR and OUT_DIR are required (or use --review-only OUT_DIR)")

    export_dir, out_dir = os.path.abspath(args.export_dir), os.path.abspath(args.out_dir)
    if os.path.commonpath([export_dir, out_dir]) == export_dir:
        sys.exit("OUT_DIR must not be inside EXPORT_DIR")
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        sys.exit("ffmpeg and ffprobe must be on PATH")
    inp, out = os.path.join(out_dir, "input"), os.path.join(out_dir, "output")
    page_dir, log_path = os.path.join(out_dir, "review"), os.path.join(out_dir, "run.log")
    for d in (out, page_dir):
        shutil.rmtree(d, ignore_errors=True)
    os.makedirs(page_dir)

    t0 = time.time()
    json_path, n_groups, n_clips, n_files = build_subset(export_dir, inp, args.window, args.limit)
    print(f"{n_groups} candidate recordings ({n_clips} clips); "
          f"processing {n_files} files near them", flush=True)
    run_pipeline(json_path, inp, out, log_path)
    print(f"Pipeline finished in {time.time() - t0:.0f}s", flush=True)
    title = (f"{os.path.basename(export_dir)} · {n_groups} candidate recordings · "
             f"{datetime.now():%Y-%m-%d %H:%M}")
    return review(out_dir, title)


if __name__ == "__main__":
    sys.exit(main())
