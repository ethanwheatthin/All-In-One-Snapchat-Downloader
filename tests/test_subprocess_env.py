"""clean_subprocess_env must undo PyInstaller's library-path override (issue #10)."""

import sys

import pytest

from subprocess_env import clean_subprocess_env


@pytest.fixture
def frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)


def test_restores_original_library_path_when_frozen(monkeypatch, frozen):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEI123")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/opt/mylibs")
    env = clean_subprocess_env()
    assert env["LD_LIBRARY_PATH"] == "/opt/mylibs"
    assert "LD_LIBRARY_PATH_ORIG" not in env


def test_drops_library_path_without_original_when_frozen(monkeypatch, frozen):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEI123")
    monkeypatch.setenv("DYLD_LIBRARY_PATH", "/tmp/_MEI123")
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    monkeypatch.delenv("DYLD_LIBRARY_PATH_ORIG", raising=False)
    env = clean_subprocess_env()
    assert "LD_LIBRARY_PATH" not in env
    assert "DYLD_LIBRARY_PATH" not in env


def test_leaves_environment_alone_when_not_frozen(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/mylibs")
    env = clean_subprocess_env()
    assert env["LD_LIBRARY_PATH"] == "/opt/mylibs"


def test_does_not_mutate_os_environ(monkeypatch, frozen):
    import os
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEI123")
    clean_subprocess_env()
    assert os.environ["LD_LIBRARY_PATH"] == "/tmp/_MEI123"
