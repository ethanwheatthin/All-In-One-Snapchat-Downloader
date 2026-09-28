"""Environment for launching external programs (ffmpeg, ffprobe, vlc, ...).

Kept dependency-free so every module (including the GUI) can import it.
"""

import os
import sys

__all__ = ["clean_subprocess_env"]

_LIB_PATH_VARS = ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH")


def clean_subprocess_env():
    """Return an env dict safe for running *system* binaries.

    A PyInstaller build points ``LD_LIBRARY_PATH`` (Linux) /
    ``DYLD_LIBRARY_PATH`` (macOS) at its bundled libraries, and child
    processes inherit it. A system ffmpeg then loads the bundled (older)
    libssl/libcrypto instead of its own and dies with e.g.
    ``symbol lookup error: libldap.so.2: undefined symbol: EVP_md2``.
    PyInstaller saves the user's original value as ``<VAR>_ORIG``; restore
    it, or drop the variable if it was never set.

    Outside a frozen build the environment is returned unchanged, so a
    library path the user set on purpose is kept.
    """
    env = os.environ.copy()
    if not getattr(sys, "frozen", False):
        return env
    for var in _LIB_PATH_VARS:
        orig = env.pop(var + "_ORIG", None)
        if orig is not None:
            env[var] = orig
        else:
            env.pop(var, None)
    return env
