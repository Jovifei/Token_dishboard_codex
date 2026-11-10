"""Build the shareable Windows x64 EXE and ZIP from this handoff source."""
import argparse
from pathlib import Path
import platform
import sys
import zipfile

from PyInstaller.__main__ import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="new build directory")
    args = parser.parse_args()
    if sys.platform != "win32" or platform.machine().lower() not in ("amd64", "x86_64") or sys.maxsize < 2**32:
        parser.error("Use 64-bit Python on Windows x64.")
    project = Path(__file__).resolve().parents[1]
    app = project / "work/qingxu"
    sys.path.insert(0, str(app))
    from app import VERSION
    from ui_brand import write_ico
    output = args.output.absolute()
    output.mkdir()
    # The EXE and taskbar must share the new continuity mark, not the 0.9.3 icon.
    icon = write_ico(output / "qingxu.ico")
    run(["--onefile", "--windowed", "--name", "Qingxu", "--noupx", "--icon", str(icon),
         "--add-data", f"{icon}:.", "--distpath", str(output / "dist"), "--workpath", str(output / "work"),
         "--specpath", str(output), str(app / "launch.pyw")])
    exe = output / "dist" / "Qingxu.exe"
    package = output / f"Qingxu-{VERSION}-Windows-x64.zip"
    with zipfile.ZipFile(package, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(exe, "轻续.exe")
        archive.write(app / "SHARE-README.txt", "使用说明.txt")
        for path in sorted((project / "docs/ui-handoff-0.9.3/source/licenses").glob("*.txt")):
            archive.write(path, "许可证/" + path.name)
    print(package)


if __name__ == "__main__":
    main()
