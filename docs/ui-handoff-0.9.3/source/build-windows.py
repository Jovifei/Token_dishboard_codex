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
    source = Path(__file__).resolve().parent
    app = source / "work" / "qingxu"
    output = args.output.absolute()
    output.mkdir()
    icon = app / "qingxu.ico"
    run(["--onefile", "--windowed", "--name", "Qingxu", "--noupx", "--icon", str(icon),
         "--add-data", f"{icon}:.", "--distpath", str(output / "dist"), "--workpath", str(output / "work"),
         "--specpath", str(output), str(app / "launch.pyw")])
    exe = output / "dist" / "Qingxu.exe"
    package = output / "Qingxu-0.9.3-Windows-x64.zip"
    with zipfile.ZipFile(package, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(exe, "轻续.exe")
        archive.write(app / "SHARE-README.txt", "使用说明.txt")
        for path in sorted((source / "licenses").glob("*.txt")):
            archive.write(path, "许可证/" + path.name)
    print(package)


if __name__ == "__main__":
    main()
