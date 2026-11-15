"""Fetch FFmpeg (portable) and VLC (via the OS package manager) on demand.

Users repeatedly get stuck installing these two tools by hand. This module
backs the wizard's "Download for me" buttons:

* FFmpeg is a self-contained pair of executables, so we download the
  official static build into a per-user app directory and add that
  directory to PATH for this process. No admin rights, no installer.
* VLC is a full application bundle that cannot be dropped in portably, so
  we shell out to winget / Homebrew (the same command the wizard shows for
  copy-paste) and stream its output. If neither is present the caller
  falls back to opening the official download page.

Everything here is best-effort: on any failure the caller still shows the
copy-the-command instructions, so a failed download is never a dead end.
"""

import hashlib
import logging
import gzip
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

from subprocess_env import clean_subprocess_env

try:
    import requests
except Exception:  # pragma: no cover - requests is a hard dependency of the app
    requests = None

APP_DIR_NAME = "SnapchatMemoriesDownloader"

# Basenames we lift out of the downloaded archive, everything else ignored.
_WANTED = {"ffmpeg", "ffmpeg.exe", "ffprobe", "ffprobe.exe"}

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------
def app_data_dir():
    """Per-user writable directory for app-managed files."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, APP_DIR_NAME)


def ffmpeg_bin_dir():
    """Directory that holds the portable ffmpeg/ffprobe once installed."""
    return os.path.join(app_data_dir(), "bin")


def _exe(name):
    return name + (".exe" if sys.platform == "win32" else "")


def installed_ffmpeg_dir():
    """Return the bin dir if a managed ffmpeg+ffprobe pair is present, else None."""
    d = ffmpeg_bin_dir()
    if os.path.isfile(os.path.join(d, _exe("ffmpeg"))) and \
            os.path.isfile(os.path.join(d, _exe("ffprobe"))):
        return d
    return None


def ensure_on_path():
    """Prepend the managed bin dir to PATH for this process if it has ffmpeg.

    Cheap and idempotent — safe to call from check_ffmpeg() on every probe.
    Returns True if a managed ffmpeg is now reachable on PATH.
    """
    d = installed_ffmpeg_dir()
    if not d:
        return False
    parts = os.environ.get("PATH", "").split(os.pathsep)
    if d not in parts:
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    return True


# --------------------------------------------------------------------------
# ffmpeg — portable download
# --------------------------------------------------------------------------
# Official static builds. Each entry: url + optional sha256 sidecar url.
_FFMPEG_SOURCES = {
    "win32": {
        "url": "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
        "sha256_url": "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip.sha256",
        "kind": "zip",
    },
    "darwin": {
        # evermeet.cx now answers 410 Gone, so use the eugeneware/ffmpeg-static
        # release assets (gzip'd single binaries, one pair per CPU arch).
        "url": "https://github.com/eugeneware/ffmpeg-static/releases/download/b6.1.1/ffmpeg-darwin-{arch}.gz",
        "url2": "https://github.com/eugeneware/ffmpeg-static/releases/download/b6.1.1/ffprobe-darwin-{arch}.gz",
        "sha256_url": None,
        "kind": "gz",
    },
    "linux": {
        "url": "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz",
        "sha256_url": "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz.sha256",
        "kind": "tar",
    },
}


def _platform_key():
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform


def _log(cb, msg):
    logging.info("tool_installer: %s", msg)
    if cb:
        try:
            cb(msg)
        except Exception:
            pass


def _download(url, dest_path, log=None, progress=None):
    """Stream url to dest_path. progress(done_bytes, total_bytes|None)."""
    if requests is None:
        raise RuntimeError("the 'requests' library is not available")
    _log(log, f"Downloading {url}")
    with requests.get(url, stream=True, timeout=60,
                      headers={"User-Agent": "SnapchatMemoriesDownloader"}) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0) or None
        done = 0
        with open(dest_path, "wb") as fh:
            for chunk in r.iter_content(chunk_size=256 * 1024):
                if not chunk:
                    continue
                fh.write(chunk)
                done += len(chunk)
                if progress:
                    try:
                        progress(done, total)
                    except Exception:
                        pass
    _log(log, f"Downloaded {done:,} bytes")
    return done


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _verify_sha256(path, sha_url, log=None):
    """Return True if ok or unverifiable, False only on a real mismatch."""
    if not sha_url or requests is None:
        _log(log, "Checksum not available for this source — skipping verification")
        return True
    try:
        text = requests.get(sha_url, timeout=30).text.strip()
    except Exception as exc:
        _log(log, f"Could not fetch checksum ({exc}) — skipping verification")
        return True
    expected = text.split()[0].lower() if text else ""
    if len(expected) != 64:
        _log(log, "Checksum file malformed — skipping verification")
        return True
    actual = _sha256(path).lower()
    if actual != expected:
        _log(log, f"CHECKSUM MISMATCH — expected {expected}, got {actual}")
        return False
    _log(log, "Checksum verified")
    return True


def _mac_arch():
    return "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"


def _extract_wanted(archive_path, kind, dest_dir, name=None):
    """Pull ffmpeg/ffprobe out of a zip or tar archive into dest_dir (flat).

    kind "gz" is a single gzip'd binary that is written out as `name`.
    """
    found = []
    if kind == "gz":
        target = os.path.join(dest_dir, name)
        with gzip.open(archive_path) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
        return [name]
    if kind == "zip":
        with zipfile.ZipFile(archive_path) as z:
            for member in z.namelist():
                base = os.path.basename(member)
                if base in _WANTED:
                    target = os.path.join(dest_dir, base)
                    with z.open(member) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    found.append(base)
    else:
        with tarfile.open(archive_path) as t:
            for member in t.getmembers():
                base = os.path.basename(member.name)
                if member.isfile() and base in _WANTED:
                    src = t.extractfile(member)
                    if src is None:
                        continue
                    target = os.path.join(dest_dir, base)
                    with src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    found.append(base)
    return found


def install_ffmpeg(log=None, progress=None):
    """Download the portable static build into ffmpeg_bin_dir().

    Returns (ok: bool, message: str). On failure nothing is left in the bin
    dir beyond what was already there.
    """
    src = _FFMPEG_SOURCES.get(_platform_key())
    if not src:
        return False, f"No portable FFmpeg source for platform '{sys.platform}'"
    if requests is None:
        return False, "The 'requests' library is not available"

    bin_dir = ffmpeg_bin_dir()
    os.makedirs(bin_dir, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="ffmpeg_dl_")
    staging = os.path.join(tmp, "extract")
    os.makedirs(staging, exist_ok=True)
    try:
        urls = [src["url"]] + ([src["url2"]] if src.get("url2") else [])
        urls = [u.format(arch=_mac_arch()) for u in urls]
        names = ["ffmpeg", "ffprobe"]
        got = set()
        for i, url in enumerate(urls):
            arc = os.path.join(tmp, f"dl{i}")
            _download(url, arc, log, progress)
            if not _verify_sha256(arc, src.get("sha256_url") if i == 0 else None, log):
                return False, "Download failed checksum verification — nothing installed"
            got.update(_extract_wanted(arc, src["kind"], staging, names[i]))

        staged = os.listdir(staging)
        missing = [n for n in ("ffmpeg", "ffprobe")
                   if not any(h.startswith(n) for h in staged)]
        if missing:
            return False, f"Archive did not contain: {', '.join(missing)}"

        for name in staged:
            stem = "ffmpeg" if name.startswith("ffmpeg") else \
                   "ffprobe" if name.startswith("ffprobe") else None
            if not stem:
                continue
            final = os.path.join(bin_dir, _exe(stem))
            shutil.move(os.path.join(staging, name), final)
            if sys.platform != "win32":
                os.chmod(final, 0o755)
            _log(log, f"Installed {final}")

        ensure_on_path()
        return True, f"FFmpeg installed to {bin_dir}"
    except Exception as exc:
        logging.exception("install_ffmpeg failed")
        return False, f"FFmpeg download failed: {exc}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# vlc — delegate to the OS package manager
# --------------------------------------------------------------------------
_BREW_PATHS = ("/opt/homebrew/bin/brew", "/usr/local/bin/brew")
_VLC_MAC_INDEX = "https://download.videolan.org/vlc/last/macosx/"


def _find_brew():
    """brew path, also probing the usual spots: apps launched from Finder
    don't inherit the shell PATH, so which() alone misses Homebrew."""
    found = shutil.which("brew")
    if found:
        return found
    for p in _BREW_PATHS:
        if os.path.isfile(p):
            return p
    return None


def _vlc_pkg_command():
    if sys.platform == "win32" and shutil.which("winget"):
        return ["winget", "install", "--exact", "--id", "VideoLAN.VLC",
                "--accept-package-agreements", "--accept-source-agreements"]
    brew = _find_brew() if sys.platform != "win32" else None
    if brew:
        return [brew, "install", "--cask", "vlc"]
    return None


def _vlc_dmg_url():
    """Latest VLC dmg URL for this Mac, scraped from the VideoLAN index."""
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "intel64"
    try:
        html = requests.get(_VLC_MAC_INDEX, timeout=30).text
        for kind in (arch, "universal"):
            m = re.search(r'href="(vlc-[\d.]+-%s\.dmg)"' % kind, html)
            if m:
                return _VLC_MAC_INDEX + m.group(1)
    except Exception:
        pass
    return f"{_VLC_MAC_INDEX}vlc-3.0.24-{arch}.dmg"


def _run(cmd, log):
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          env=clean_subprocess_env())
    if proc.returncode != 0:
        _log(log, f"{cmd[0]} failed: {(proc.stderr or proc.stdout).strip()}")
    return proc.returncode == 0


def install_vlc_macos(log=None, progress=None):
    """Download the official dmg and copy VLC.app into ~/Applications.

    Needs no Homebrew and no admin rights; find_vlc_executable() already
    looks in ~/Applications.
    """
    if requests is None:
        return False, "The 'requests' library is not available"
    tmp = tempfile.mkdtemp(prefix="vlc_dl_")
    mount = os.path.join(tmp, "mnt")
    os.makedirs(mount)
    mounted = False
    try:
        url = _vlc_dmg_url()
        dmg = os.path.join(tmp, "vlc.dmg")
        _download(url, dmg, log, progress)
        if not _verify_sha256(dmg, url + ".sha256", log):
            return False, "Download failed checksum verification — nothing installed"
        _log(log, "Mounting disk image...")
        if not _run(["hdiutil", "attach", "-nobrowse", "-readonly",
                     "-mountpoint", mount, dmg], log):
            return False, "Could not open the VLC disk image"
        mounted = True
        src = os.path.join(mount, "VLC.app")
        if not os.path.isdir(src):
            return False, "VLC.app not found in the disk image"
        dest_dir = os.path.expanduser("~/Applications")
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, "VLC.app")
        if os.path.isdir(dest):
            shutil.rmtree(dest, ignore_errors=True)
        _log(log, f"Copying VLC to {dest_dir}...")
        if not _run(["ditto", src, dest], log):
            return False, "Could not copy VLC.app"
        _run(["xattr", "-dr", "com.apple.quarantine", dest], log)
        return True, f"VLC installed to {dest}"
    except Exception as exc:
        logging.exception("install_vlc_macos failed")
        return False, f"VLC download failed: {exc}"
    finally:
        if mounted:
            subprocess.run(["hdiutil", "detach", "-force", mount],
                           capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


def install_vlc(log=None, progress=None):
    """Install VLC: direct dmg on macOS (Homebrew as fallback), else the
    platform package manager, streaming output.

    Returns (ok, message). ok is False with message 'no-package-manager'
    when winget/brew is unavailable so the caller can open the web page.
    """
    if sys.platform == "darwin":
        ok, msg = install_vlc_macos(log, progress)
        if ok or not _find_brew():
            return ok, msg
        _log(log, f"{msg} — trying Homebrew instead")
    cmd = _vlc_pkg_command()
    if not cmd:
        return False, "no-package-manager"
    _log(log, f"Running: {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                creationflags=CREATE_NO_WINDOW, env=clean_subprocess_env())
    except Exception as exc:
        return False, f"Could not start package manager: {exc}"
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            _log(log, line)
    proc.wait()
    if proc.returncode == 0:
        return True, "VLC install finished"
    return False, f"Package manager exited with code {proc.returncode}"
