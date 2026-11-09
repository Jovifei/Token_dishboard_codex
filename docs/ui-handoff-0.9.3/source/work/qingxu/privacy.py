"""Private local directories, including a protected inheritable Windows DACL."""
import csv
import ctypes
from functools import lru_cache
import io
import os
from pathlib import Path
import re
import subprocess


@lru_cache(maxsize=1)
def user_sid():
    command = str(Path(os.environ["SystemRoot"]) / "System32" / "whoami.exe")
    result = subprocess.run([command, "/user", "/fo", "csv", "/nh"],
                            capture_output=True, check=True, timeout=10,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    # Only the SID column is ASCII; account names may use any local code page.
    row = next(csv.reader(io.StringIO(result.stdout.decode("ascii", errors="replace"))))
    sid = row[1]
    if not re.fullmatch(r"S-1-\d+(?:-\d+)+", sid):
        raise ValueError("Cannot determine the current Windows user SID")
    return sid


def ensure_private(path):
    from sync import safe_path
    root = safe_path(path)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        root.chmod(0o700)
        return root
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    descriptor = ctypes.c_void_p()
    convert = security.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32,
                        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    convert.restype = ctypes.c_int
    apply = security.SetFileSecurityW
    apply.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_void_p]
    apply.restype = ctypes.c_int
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    sddl = f"D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;{user_sid()})"
    if not convert(sddl, 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not apply(str(root), 0x80000004, descriptor):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.LocalFree(descriptor)
    return root
