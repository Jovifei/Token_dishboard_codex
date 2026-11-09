"""Launch the active Qingxu source with the handoff package's synthetic fixtures."""
import argparse
from pathlib import Path
import runpy
import sys
import tkinter as tk


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="new directory for synthetic data")
    parser.add_argument("--check", action="store_true", help="verify startup and exit")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project / "work" / "qingxu"))
    from app import App, VERSION
    from context_usage import chat_metrics
    from relay import Relay

    fixture = runpy.run_path(str(project / "docs" / "ui-handoff-0.9.3" / "fixtures" / "demo.py"))
    data = fixture["create"](args.root)
    root = tk.Tk()
    try:
        if args.check:
            root.withdraw()
        ui = App(root, Relay(data["settings"]), data["prefs"])
        if args.check:
            assert len(ui.chat_usage.chats) == 5
            chat_id = fixture["IDS"]["normal"]
            ui.choose_usage_chat(ui.chat_usage.chats[chat_id])
            report = chat_metrics(data["settings"]["chat_db"], chat_id, include_totals=True)
            assert report["sample"]["usage"]["totalTokens"] == 128000
            assert report["requests"]["requests"] == 1 and report["requests"]["duplicates"] == 1
            print(f"PASS Qingxu {VERSION}, 5 synthetic chats and request deduplication")
        else:
            root.mainloop()
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


if __name__ == "__main__":
    main()
