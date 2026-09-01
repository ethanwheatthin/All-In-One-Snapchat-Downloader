"""Tests for tool_installer — portable FFmpeg download + VLC via package manager.

No network: requests.get and subprocess are monkeypatched.
"""

import hashlib
import io
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tool_installer as ti


# --------------------------------------------------------------------------
# fake HTTP layer
# --------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, body=b"", text=""):
        self._body = body
        self.text = text
        self.headers = {"Content-Length": str(len(body))}

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_ffmpeg_zip():
    buf = io.BytesIO()
    exe = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    probe = "ffprobe.exe" if sys.platform == "win32" else "ffprobe"
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"ffmpeg-7.0-essentials_build/bin/{exe}", b"MZ-fake-ffmpeg")
        z.writestr(f"ffmpeg-7.0-essentials_build/bin/{probe}", b"MZ-fake-ffprobe")
        z.writestr("ffmpeg-7.0-essentials_build/README.txt", b"docs")
    return buf.getvalue()


@pytest.fixture
def app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ti, "app_data_dir", lambda: str(tmp_path / "appdata"))
    # force the zip source regardless of host platform
    monkeypatch.setattr(ti, "_platform_key", lambda: sys.platform if sys.platform == "win32" else "linux")
    return tmp_path


# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------
def test_ffmpeg_bin_dir_under_app_data(app_dir):
    assert ti.ffmpeg_bin_dir() == os.path.join(str(app_dir / "appdata"), "bin")


def test_installed_and_ensure_on_path_roundtrip(app_dir, monkeypatch):
    assert ti.installed_ffmpeg_dir() is None
    d = ti.ffmpeg_bin_dir()
    os.makedirs(d)
    for n in ("ffmpeg", "ffprobe"):
        open(os.path.join(d, ti._exe(n)), "w").close()
    assert ti.installed_ffmpeg_dir() == d
    monkeypatch.setenv("PATH", "")
    assert ti.ensure_on_path() is True
    assert d in os.environ["PATH"].split(os.pathsep)


# --------------------------------------------------------------------------
# checksum
# --------------------------------------------------------------------------
def test_verify_sha256_match(tmp_path, monkeypatch):
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello")
    digest = hashlib.sha256(b"hello").hexdigest()
    monkeypatch.setattr(ti.requests, "get", lambda *a, **k: _FakeResponse(text=digest + "  a.bin"))
    assert ti._verify_sha256(str(f), "http://x/a.sha256") is True


def test_verify_sha256_mismatch(tmp_path, monkeypatch):
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello")
    monkeypatch.setattr(ti.requests, "get", lambda *a, **k: _FakeResponse(text="0" * 64))
    assert ti._verify_sha256(str(f), "http://x/a.sha256") is False


def test_verify_sha256_missing_url_is_ok(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"hi")
    assert ti._verify_sha256(str(f), None) is True


# --------------------------------------------------------------------------
# install_ffmpeg
# --------------------------------------------------------------------------
def test_install_ffmpeg_success(app_dir, monkeypatch):
    body = _fake_ffmpeg_zip()
    digest = hashlib.sha256(body).hexdigest()

    def fake_get(url, *a, **k):
        if url.endswith(".sha256"):
            return _FakeResponse(text=digest)
        return _FakeResponse(body=body)

    monkeypatch.setattr(ti.requests, "get", fake_get)
    monkeypatch.setenv("PATH", "")

    ok, msg = ti.install_ffmpeg()
    assert ok, msg
    d = ti.ffmpeg_bin_dir()
    assert os.path.isfile(os.path.join(d, ti._exe("ffmpeg")))
    assert os.path.isfile(os.path.join(d, ti._exe("ffprobe")))
    if sys.platform != "win32":
        assert os.access(os.path.join(d, "ffmpeg"), os.X_OK)
    assert d in os.environ["PATH"].split(os.pathsep)


def test_install_ffmpeg_checksum_mismatch_installs_nothing(app_dir, monkeypatch):
    body = _fake_ffmpeg_zip()

    def fake_get(url, *a, **k):
        if url.endswith(".sha256"):
            return _FakeResponse(text="0" * 64)
        return _FakeResponse(body=body)

    monkeypatch.setattr(ti.requests, "get", fake_get)
    ok, msg = ti.install_ffmpeg()
    assert not ok
    assert "checksum" in msg.lower()
    assert ti.installed_ffmpeg_dir() is None


# --------------------------------------------------------------------------
# install_vlc
# --------------------------------------------------------------------------
class _FakeProc:
    def __init__(self, lines, code=0):
        self.stdout = iter(lines)
        self.returncode = code

    def wait(self):
        pass


def test_install_vlc_streams_and_succeeds(monkeypatch):
    monkeypatch.setattr(ti.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(ti, "_vlc_pkg_command", lambda: ["brew", "install", "--cask", "vlc"])
    monkeypatch.setattr(ti.subprocess, "Popen",
                        lambda *a, **k: _FakeProc(["Downloading...\n", "Installing...\n"], code=0))
    seen = []
    ok, msg = ti.install_vlc(log=seen.append)
    assert ok
    assert any("Installing" in s for s in seen)


def test_install_vlc_no_package_manager(monkeypatch):
    monkeypatch.setattr(ti, "_vlc_pkg_command", lambda: None)
    ok, msg = ti.install_vlc()
    assert not ok
    assert msg == "no-package-manager"


def test_install_vlc_nonzero_exit(monkeypatch):
    monkeypatch.setattr(ti, "_vlc_pkg_command", lambda: ["winget", "install", "x"])
    monkeypatch.setattr(ti.subprocess, "Popen",
                        lambda *a, **k: _FakeProc(["boom\n"], code=1))
    ok, msg = ti.install_vlc()
    assert not ok
    assert "code 1" in msg
