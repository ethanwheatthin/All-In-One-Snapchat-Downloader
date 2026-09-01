"""Tiny filesystem helpers shared across the ZIP and media pipelines.

Kept dependency-free so every module (including the GUI) can import it.
"""

import os

__all__ = ["is_macos_metadata", "drop_macos_metadata"]


def is_macos_metadata(name):
    """True for macOS junk that must never be treated as real media.

    When a Mac writes to a non-HFS+ volume (an exFAT/NTFS external drive, or
    inside a ZIP) it stores each file's resource fork and Finder info in a
    sidecar AppleDouble file whose basename is the original name prefixed
    with ``._``. It also drops ``.DS_Store`` files, and the macOS Archive
    Utility nests everything under ``__MACOSX/``. None of these are media,
    but ``._IMG.jpg`` / ``._VID.mp4`` look enough like media to reach the
    decoders and blow up with "Invalid PNG signature" / "not a MP4 file".

    Accepts a bare name, a relative path, or a ZIP member name (which always
    uses forward slashes).
    """
    if not name:
        return False
    normalized = name.replace("\\", "/")
    if normalized == "__MACOSX" or normalized.startswith("__MACOSX/"):
        return True
    base = os.path.basename(normalized.rstrip("/"))
    return base.startswith("._") or base == ".DS_Store"


def drop_macos_metadata(names):
    """Filter an iterable of names/paths, removing macOS metadata entries."""
    return [n for n in names if not is_macos_metadata(n)]
