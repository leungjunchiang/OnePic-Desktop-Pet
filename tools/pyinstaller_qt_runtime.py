"""Prepare the bundled PySide6/shiboken6 DLL search paths on Windows.

PySide6 keeps ``shiboken6.abi3.dll`` in a sibling package directory during
development. In a PyInstaller onedir build, ``QtCore.pyd`` lives in the
``PySide6`` directory while that sibling DLL remains under ``shiboken6``.
Windows does not search that sibling directory automatically, so the import
can fail before the application has a chance to write its normal diagnostics.
"""

from __future__ import annotations

import _ctypes
import os
import sys
from pathlib import Path


if sys.platform == "win32":
    bundled_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    _dll_directory_handles = []
    # PyInstaller 6 uses both ``_MEIPASS/<package>`` and
    # ``_MEIPASS/_internal/<package>`` layouts depending on the bootloader
    # and collection mode. Register whichever layout is present. Include the
    # executable directory as well because a bootloader can expose either
    # location as ``sys._MEIPASS``.
    executable_root = Path(sys.executable).resolve().parent
    bundled_roots = (
        bundled_root,
        bundled_root / "_internal",
        executable_root,
        executable_root / "_internal",
    )
    dll_directories = []
    root_dll_directories = []
    for root in bundled_roots:
        if root.is_dir() and root not in root_dll_directories:
            root_dll_directories.append(root)
        for package_name in ("PySide6", "shiboken6"):
            dll_directory = root / package_name
            if not dll_directory.is_dir():
                continue
            if dll_directory in dll_directories:
                continue
            dll_directories.append(dll_directory)
            try:
                _dll_directory_handles.append(os.add_dll_directory(str(dll_directory)))
            except OSError:
                # Older Windows environments may not expose AddDllDirectory;
                # PyInstaller's normal DLL search path remains available there.
                pass
    if root_dll_directories or dll_directories:
        os.environ["PATH"] = os.pathsep.join(
            [
                *(str(path) for path in (*dll_directories, *root_dll_directories)),
                os.environ.get("PATH", ""),
            ]
        )

    # PySide6 6.11.2's extension modules use the ABI bridge DLL during module
    # initialization. Preload the bridge and Qt core from the exact bundled
    # paths so Windows cannot resolve an incompatible copy from PATH first.
    for package_name, dll_name in (
        ("PySide6", "Qt6Core.dll"),
        ("shiboken6", "shiboken6.abi3.dll"),
        ("PySide6", "pyside6.abi3.dll"),
    ):
        dll_path = next(
            (root / package_name / dll_name for root in bundled_roots if (root / package_name / dll_name).is_file()),
            None,
        )
        if dll_path is None:
            continue
        try:
            _ctypes.LoadLibrary(str(dll_path))
        except OSError:
            # The normal import will produce the actionable error if a
            # machine has a damaged or incompatible native dependency.
            pass
