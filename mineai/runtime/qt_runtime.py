"""Pin PyQt6 to the Qt runtime bundled by PyInstaller.

Windows can resolve a same-named ``Qt6Core.dll`` from another application
before the copy shipped with a frozen MineAI executable.  Loading that DLL
with a PyQt6 extension built against a different Qt revision produces the
opaque ``The specified procedure could not be found`` import error.  This
module configures the DLL search path before any PyQt6 module is imported.
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path
from typing import Any


_DLL_DIRECTORY_HANDLES: list[Any] = []
_CONFIGURED_DIRECTORIES: set[str] = set()


def _find_qt_bin(base_dir: str | os.PathLike[str]) -> Path | None:
    base = Path(base_dir)
    for relative in (Path("PyQt6") / "Qt6" / "bin", Path("PyQt6") / "Qt" / "bin"):
        candidate = base / relative
        if candidate.is_dir():
            return candidate
    return None


def _prepend_path(directory: Path) -> None:
    directory_text = str(directory)
    current = os.environ.get("PATH", "")
    entries = [entry for entry in current.split(os.pathsep) if entry]
    normalized = os.path.normcase(os.path.abspath(directory_text))
    if any(os.path.normcase(os.path.abspath(entry)) == normalized for entry in entries):
        return
    os.environ["PATH"] = os.pathsep.join([directory_text, *entries])


def _set_windows_dll_directory(directory: Path) -> None:
    """Add *directory* to native DLL lookup without making it mandatory."""
    try:
        add_dll_directory = getattr(os, "add_dll_directory")
    except AttributeError:
        add_dll_directory = None
    if add_dll_directory is not None:
        try:
            _DLL_DIRECTORY_HANDLES.append(add_dll_directory(str(directory)))
        except OSError:
            # PATH/SetDllDirectoryW below still covers older Python builds and
            # systems where AddDllDirectory is unavailable.
            pass

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        set_dll_directory = kernel32.SetDllDirectoryW
        set_dll_directory.argtypes = [ctypes.c_wchar_p]
        set_dll_directory.restype = ctypes.c_int
        set_dll_directory(str(directory))
    except (AttributeError, OSError):
        # The helper is also imported by source runs and non-Windows tooling;
        # failure to access the Windows API must never prevent the UI startup.
        pass


def configure_qt_runtime(base_dir: str | os.PathLike[str] | None) -> None:
    """Prefer the Qt DLLs located below a frozen application's extraction dir.

    The function is intentionally idempotent and a no-op outside Windows or
    when the executable is running from source.  It must run before importing
    ``PyQt6.QtCore``.
    """
    if not sys.platform.startswith("win") or not base_dir:
        return

    qt_bin = _find_qt_bin(base_dir)
    if qt_bin is None:
        return

    key = os.path.normcase(os.path.abspath(str(qt_bin)))
    if key in _CONFIGURED_DIRECTORIES:
        return

    _prepend_path(qt_bin)
    _set_windows_dll_directory(qt_bin)
    _CONFIGURED_DIRECTORIES.add(key)


__all__ = ["configure_qt_runtime"]
