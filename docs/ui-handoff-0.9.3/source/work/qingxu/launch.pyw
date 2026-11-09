"""Windowless entry point; report startup errors without a disappearing console."""
import ctypes
import os
from pathlib import Path
import sys
import traceback


if __name__ == "__main__":
    data_root = Path(__file__).absolute().parent / "data"
    if getattr(sys, "frozen", False):
        local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        data_root = Path(os.environ.get("QINGXU_DATA_DIR") or local / "Qingxu").expanduser().absolute()
    try:
        from app import main
        main(data_root)
    except Exception:
        error = traceback.format_exc()
        destination = "Startup log unavailable"
        try:
            from privacy import ensure_private
            folder = ensure_private(data_root / "logs")
            logfile = folder / "startup-error.txt"
            logfile.write_text(error, encoding="utf-8")
            destination = str(logfile)
        except Exception:
            pass
        ctypes.windll.user32.MessageBoxW(
            None, "轻续启动失败；没有自动修改配置或知识库。\n\n" + destination
            + "\n\n" + error[-1600:], "轻续 · Token 工作台", 0x10)
