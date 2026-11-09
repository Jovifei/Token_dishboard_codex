"""Capture real Windows Tk screens with synthetic data and exercise scoped write paths."""
import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from threading import Thread
from time import monotonic, sleep
import tkinter as tk
from tkinter import ttk
from unittest.mock import patch

from PIL import ImageGrab

BUNDLE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BUNDLE / "source/work/qingxu"), str(BUNDLE / "fixtures")]
from app import App, ReminderDialog, VERSION, filedialog
from demo import IDS, PROJECT, SUMMARY, append_events, create, event
from relay import Relay

win = ctypes.WinDLL("user32", use_last_error=True)
kernel = ctypes.WinDLL("kernel32", use_last_error=True)
dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
win.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
win.GetAncestor.restype = wintypes.HWND
win.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
win.LogicalToPhysicalPointForPerMonitorDPI.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
win.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
win.GetDlgItem.restype = wintypes.HWND
win.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
win.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
win.IsWindowVisible.argtypes = [wintypes.HWND]
ENUM = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
win.EnumThreadWindows.argtypes = [wintypes.DWORD, ENUM, wintypes.LPARAM]


def widgets(parent):
    for child in parent.winfo_children():
        yield child
        yield from widgets(child)


def button(parent, text):
    matches = [w for w in widgets(parent) if isinstance(w, ttk.Button) and w.cget("text") == text]
    assert len(matches) == 1, (text, len(matches))
    return matches[0]


class Evidence:
    def __init__(self, bundle):
        self.path = bundle / "assets/screens"
        self.prompts = bundle / "fixtures/prompts"
        self.prompts.mkdir(exist_ok=True)
        self.shots = []
        self.tests = {}

    def window(self, hwnd, key, title, data):
        rect = wintypes.RECT()
        if not win.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise OSError("Unable to measure the captured window")
        a, b = wintypes.POINT(rect.left, rect.top), wintypes.POINT(rect.right, rect.bottom)
        transform = win.LogicalToPhysicalPointForPerMonitorDPI
        if not (transform(hwnd, ctypes.byref(a)) and transform(hwnd, ctypes.byref(b))):
            raise OSError("Unable to determine Windows display scale")
        width, height = rect.right - rect.left, rect.bottom - rect.top
        scale = round((b.x - a.x) * 100 / width)
        frame = wintypes.RECT()
        if dwm.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(frame), ctypes.sizeof(frame)) == 0:
            bounds = (frame.left, frame.top, frame.right, frame.bottom)
        else:
            bounds = (a.x, a.y, b.x, b.y)
        image = ImageGrab.grab(bbox=bounds, all_screens=True)
        raw_size = list(image.size)
        assert image.width > 140 and image.height > 70 and len(image.getcolors(10000) or ()) != 1
        image.thumbnail((1280, 1280))
        filename = f"{key}.jpg"
        image.save(self.path / filename, quality=88, optimize=True)
        assert (self.path / filename).stat().st_size < 2_000_000
        self.shots.append({"id": key, "title": title, "file": f"assets/screens/{filename}", "version": VERSION,
                           "window_logical_px": [width, height], "capture_px": raw_size,
                           "asset_px": list(image.size), "scale_percent": scale, "data_type": data})
        print("CAPTURE", key, raw_size, scale, flush=True)

    def shot(self, window, key, title, data="合成数据 · 导出源码运行"):
        for _ in range(3):
            window.update()
            sleep(0.075)
        self.window(win.GetAncestor(window.winfo_id(), 2), key, title, data)

    def prompt(self, name, ui):
        text = ui.prompt_text.get("1.0", "end-1c")
        assert text.strip(), name
        (self.prompts / (name + ".txt")).write_text(text + "\n", encoding="utf-8")
        return text

    def native_confirm(self, action, key, title, accept=True):
        thread = kernel.GetCurrentThreadId()
        handled, errors = [], []

        @ENUM
        def visit(hwnd, _):
            class_name = ctypes.create_unicode_buffer(30)
            if not win.IsWindowVisible(hwnd) or not win.GetClassNameW(hwnd, class_name, 30) or class_name.value != "#32770":
                return True
            choice = 6 if accept else 7
            target = win.GetDlgItem(hwnd, choice)
            try:
                if not target:
                    raise ValueError("Unexpected native confirmation dialog")
                sleep(0.28)
                self.window(hwnd, key, title, "合成配置副本 · 原生确认弹窗")
                handled.append(key)
            except Exception as exc:
                errors.append(str(exc))
            finally:
                if target:
                    win.PostMessageW(hwnd, 0x0111, choice, target)
                else:
                    win.PostMessageW(hwnd, 0x0010, 0, 0)
            return False

        def read_dialog():
            deadline = monotonic() + 10
            while monotonic() < deadline and not (handled or errors):
                win.EnumThreadWindows(thread, visit, 0)
                sleep(0.04)

        worker = Thread(target=read_dialog, daemon=True)
        worker.start()
        action()
        worker.join(11)
        assert handled == [key] and not errors, (key, handled, errors)


def root_app(demo):
    root = tk.Tk()
    ui = App(root, Relay(demo["settings"]), demo["prefs"])
    root.geometry("1120x845+40+40")
    root.attributes("-topmost", True)
    root.update()
    return root, ui


def wait_until(root, predicate, label, seconds=7):
    end = monotonic() + seconds
    while monotonic() < end:
        root.update()
        if predicate():
            return
        sleep(0.05)
    raise AssertionError(label)


def collect(sandbox, ev):
    sandbox.mkdir()
    blank = create(sandbox / "首次启动", blank=True)
    root, ui = root_app(blank)
    try:
        assert "暂不可读" in ui.status.get()
        ev.shot(root, "01-empty", "首次启动与无本机聊天数据", "空白隔离目录 · 导出源码运行")
    finally:
        root.destroy()
    demo = create(sandbox / "工作数据")
    settings = demo["settings"]
    cfg = Path(settings["config"])
    config_before = cfg.read_bytes()
    note_before = {p: p.read_bytes() for p in demo["notes"].rglob("*.md")}
    command = [sys.executable, str(Path(settings["runtime"]) / "checkpoint.py"), "--registry", settings["registry"],
               "--state-root", settings["state_root"]]
    subprocess.run(command + ["collect"], check=True, capture_output=True, timeout=30)
    root, ui = root_app(demo)
    flash = patch("app.flash_taskbar")
    flash_call = flash.start()
    try:
        def choose(key, page=0):
            ui.tabs.select(page)
            ui.choose_usage_chat(ui.chat_usage.chats[IDS[key]])
            root.update()

        ui.category_choice.set("示例蓝图")
        ui.category_choice.event_generate("<<ComboboxSelected>>")
        root.update()
        assert ui.chat["chat_id"] == IDS["normal"]
        ev.shot(root, "02-project", "选择项目后定位该项目最新聊天")

        panel = ui.chat_usage
        choose("normal", 1)
        wait_until(root, lambda: "128,000" in panel.context_detail.get(), "normal request")
        assert "1 个独立请求" in panel.usage_detail.get()
        ev.shot(root, "03-usage-normal", "选择聊天、最近请求与累计明细")
        panel.detail_canvas.yview_moveto(1)
        root.update()
        assert panel.diagnosis.winfo_rooty() < panel.detail_canvas.winfo_rooty() + panel.detail_canvas.winfo_height()
        ev.shot(root, "04-diagnosis", "内容来源及本次诊断")
        assert "exec 11 次" in panel.diagnosis_notes["tools"].cget("text")

        choose("history", 1)
        wait_until(root, lambda: "历史测量" in ui.context_notice.get(), "historical request")
        ev.shot(root, "05-usage-history", "历史测量仍可查看，等待新请求")
        choose("unknown", 1)
        wait_until(root, lambda: "容量未知" in ui.context_notice.get(), "unknown window")
        ev.shot(root, "06-usage-unknown", "窗口和运行时压缩线未知")
        choose("alert", 1)
        wait_until(root, lambda: ui.context_banner.cget("style") == "Alert.TLabel", "context alert")
        flash_call.assert_called_once_with(root)
        ev.shot(root, "07-alert", "最近请求达到所设比例")

        toggle = next(w for w in widgets(root) if isinstance(w, ttk.Button)
                      and str(w.cget("textvariable")) == str(ui.reminder_caption))
        dialog_errors = []

        def capture_reminder():
            dialog = next(w for w in root.winfo_children() if isinstance(w, ReminderDialog))
            try:
                ev.shot(dialog, "08-reminder", "提醒设置与百分比", "合成数据 · Tk 提醒弹窗")
                dialog.percent.set("88")
                button(dialog, "保存设置").invoke()
            except Exception as exc:
                dialog_errors.append(str(exc))
                dialog.cancel()

        root.after(180, capture_reminder)
        toggle.invoke()
        assert not dialog_errors and ui.load_reminder() == (True, 88), dialog_errors
        wait_until(root, lambda: "提醒线 88%" in ui.context_notice.get(), "updated percent")
        append_events(demo["logs"]["alert"], event("context_compacted", datetime.now(timezone.utc)))
        ui.refresh_pending = True
        ui.schedule_metrics()
        wait_until(root, lambda: "等待压缩后的新测量" in ui.context_notice.get(), "compaction event")
        ev.shot(root, "09-compaction", "压缩事件提示，当前样本等待刷新")

        ui.usage_tabs.select(1)
        root.update()
        button(ui.frames[1], "刷新用量快照").invoke()
        root.update()
        assert "快照可能过期" in ui.dashboard.info.get()
        ev.shot(root, "10-snapshot", "日期快照、项目与聊天树及过期标记")
        tree = ui.dashboard.trees["projects"]
        for project in tree.get_children():
            tree.item(project, open=True)
        tree.selection_set(tree.get_children()[0])
        tree.event_generate("<<TreeviewSelect>>")
        root.update()
        ev.shot(root, "11-snapshot-project", "项目范围钻取与返回全局快照")
        ui.dashboard.notebook.select(1)
        ev.shot(root, "11b-snapshot-roles", "日期快照按主、子 agent 和其他角色分组")

        choose("normal")
        with patch.object(root, "clipboard_clear"), patch.object(root, "clipboard_append") as clip:
            save = button(ui.frames[0], "复制保存指令")
            save.invoke()
            old = ev.prompt("registered-save", ui)
            assert "semantic-save" in old
            clip.assert_called_once_with(old)
            save.invoke()
            assert clip.call_count == 2
            ev.shot(root, "12-relay-save", "已登记项目的旧聊保存指令")
            save_cmd = command + ["semantic-save", "--project-id", PROJECT, "--expected-revision", "1"]
            saved = subprocess.run(save_cmd, input=SUMMARY.encode(), check=True, capture_output=True, timeout=30)
            assert json.loads(saved.stdout)["revision"] == 2
            conflict = subprocess.run(save_cmd, input=b"stale", capture_output=True, timeout=30)
            assert conflict.returncode != 0 and b"state_conflict" in conflict.stdout + conflict.stderr
            read = subprocess.run(command + ["semantic-read", "--project-id", PROJECT], check=True,
                                  capture_output=True, timeout=30)
            assert json.loads(read.stdout)["summary"] == SUMMARY
            button(ui.frames[0], "核对交接摘要").invoke()
            assert "版本 2" in ui.relay_state.get()
            ev.shot(root, "13-relay-readback", "私有断点写后读回与版本 2")
            button(ui.frames[0], "复制恢复指令").invoke()
            assert "semantic-read" in ev.prompt("registered-restore", ui)
            ev.shot(root, "14-relay-restore", "已保存断点的新聊恢复指令")

            choose("manual")
            button(ui.frames[0], "复制保存指令").invoke()
            ev.prompt("manual-save", ui)
            button(ui.frames[0], "核对交接摘要").invoke()
            ui.summary_text.insert("1.0", SUMMARY)
            root.update()
            ev.shot(root, "15-relay-manual", "未登记项目粘贴短摘要")
            button(ui.frames[0], "复制恢复指令").invoke()
            assert SUMMARY in ev.prompt("manual-restore", ui)
            ui.kb_checks["PLAN"].set(True)
            button(ui.frames[0], "生成知识库指令").invoke()
            assert "按项目映射核实目标页" in ev.prompt("knowledge-unmapped", ui)
            ev.shot(root, "16-knowledge-unmapped", "缺少映射时生成待核实目标的指令")

            choose("normal")
            ui.kb_checks["PLAN"].set(True)
            button(ui.frames[0], "生成知识库指令").invoke()
            assert "01-总体计划.md" in ev.prompt("knowledge-mapped", ui)
            ev.shot(root, "17-knowledge-mapped", "普通 Markdown 文件夹映射到已知目标页")
            save.invoke()
            ui.kb_mode.set("附到旧聊交接")
            button(ui.frames[0], "生成知识库指令").invoke()
            button(ui.frames[0], "生成知识库指令").invoke()
            assert ev.prompt("knowledge-attached", ui).count("【项目知识库更新】") == 1

        ui.tabs.select(2)
        root.update()
        button(ui.frames[2], "读取配置").invoke()
        assert "读取成功" in ui.config_status.get()
        ev.shot(root, "18-config-read", "配置读取、范围、参考值与文件值")
        ui.config_advanced_button.invoke()
        ui.agent_button.invoke()
        root.update()
        ui.config_canvas.yview_moveto(1)
        ev.shot(root, "18b-config-advanced", "主模型高级参数和子 agent 默认设置")
        ui.config_advanced_button.invoke()
        ui.agent_button.invoke()
        selected, value = ui.config_items["tool_output_token_limit"]
        selected.set(True)
        value.set("8000")
        preview = button(ui.frames[2], "预览勾选的修改")
        preview.invoke()
        assert ui.config_preview and cfg.read_bytes() == config_before
        ev.shot(root, "19-config-preview", "配置副本的预览，文件尚未写入")
        changed = config_before + b"\n# synthetic concurrent edit\n"
        cfg.write_bytes(changed)
        ui.config_apply.invoke()
        assert cfg.read_bytes() == changed and ui.config_preview is None
        ev.shot(root, "20-config-conflict", "预览后外部修改，拒绝应用并保留文件")
        cfg.write_bytes(config_before)
        ui.load_config()
        selected.set(True)
        value.set("8000")
        preview.invoke()
        ev.native_confirm(ui.config_apply.invoke, "21-config-cancel", "应用确认弹窗，选择取消", accept=False)
        assert cfg.read_bytes() == config_before and ui.config_preview is not None
        ev.native_confirm(ui.config_apply.invoke, "22-config-confirm", "备份并应用原生确认弹窗")
        assert b"tool_output_token_limit = 8000" in cfg.read_bytes()
        assert ui.config_result_title.get() == "写后读回一致"
        ev.shot(root, "23-config-applied", "备份、应用、写后读回与恢复回执")
        receipt, = ui.config_store.root.glob("*/receipt.json")
        with patch.object(filedialog, "askopenfilename", return_value=str(receipt)):
            ev.native_confirm(button(ui.frames[2], "从配置备份恢复…").invoke,
                              "24-config-restore-confirm", "恢复配置原生确认弹窗")
        assert cfg.read_bytes() == config_before
        assert ui.config_result_title.get() == "已读回恢复结果"
        ev.shot(root, "25-config-restored", "恢复到备份，按字节读回一致")
        assert all(p.read_bytes() == old for p, old in note_before.items())
        ev.tests.update(duplicate_response_dedup=True, taskbar_call_simulated=True, reminder_preference_88=True,
                        checkpoint_revision=2, stale_revision_rejected=True, config_conflict_preserved=True,
                        config_cancel_preserved=True, config_restored_exact=True, knowledge_pages_unchanged=True,
                        clipboard_calls_intercepted=True, new_codex_chat_executed=False)
    finally:
        root.destroy()
        flash.stop()
    root, ui = root_app(demo)
    try:
        assert ui.reminder_percent == 88 and ui.chat["chat_id"] == IDS["normal"]
        ev.tests["reopen_preferences_and_chat"] = True
    finally:
        root.destroy()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sandbox", type=Path, required=True, help="new synthetic data root")
    args = parser.parse_args()
    ev = Evidence(BUNDLE)
    collect(args.sandbox.absolute(), ev)
    (ev.path / "metadata.json").write_text(json.dumps(ev.shots, ensure_ascii=False, indent=2), encoding="utf-8")
    (BUNDLE / "tests/ui-evidence.json").write_text(json.dumps(ev.tests, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PASS", len(ev.shots), "real Tk captures and isolated writes", flush=True)
