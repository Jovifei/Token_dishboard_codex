"""Capture the real current Tk workbench with isolated synthetic data.

Development-only dependency: Pillow. Linux: xvfb-run -a python tools/capture-ui-review.py
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import tkinter as tk

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'work/qingxu'), str(ROOT / 'tools/fixtures')]
import demo
from app import VERSION
from relay import Relay
from workbench import Workbench
from PIL import ImageGrab


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/design/apple-ui-v2/screens-ui2')
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    captures = []
    with tempfile.TemporaryDirectory(prefix='qingxu-ui-review-') as folder:
        fixture = demo.create(Path(folder) / 'synthetic')
        root = tk.Tk()
        ui = Workbench(root, Relay(fixture['settings']), fixture['prefs'])
        root.title('轻续 · ' + VERSION + ' · 合成演示 / UI 验收')
        root.geometry('1240x840+30+30')
        ui.closed = True  # Disable periodic polling during deterministic capture.
        if ui.metric_timer:
            root.after_cancel(ui.metric_timer); ui.metric_timer = None
        ui.metric_worker.shutdown(wait=True, cancel_futures=True)
        ui.metric_job = None
        ui.monitor.should_notify = lambda _: False
        record = next(r for r in ui.catalog['chats'] if r['chat_id'] == demo.IDS['normal'])
        ui.choose_record(record)
        rows, detail, catalog = ui.collect_metrics(dict(ui.relay.settings), ui.monitor,
            ui.catalog['chats'], record['chat_id'], {})
        ui.render_metrics(rows, detail, catalog)
        root.update()

        def shot(name, target=root, note='Real Tk with synthetic fixture'):
            root.update_idletasks(); root.update()
            x, y = target.winfo_rootx(), target.winfo_rooty()
            w, h = target.winfo_width(), target.winfo_height()
            ImageGrab.grab(bbox=(x, y, x+w, y+h)).save(args.output / name)
            captures.append({'file': name, 'size': [w,h], 'version': VERSION,
                             'note': note, 'tk_scaling': float(root.tk.call('tk','scaling'))})

        ui.show_page('usage'); shot('01-usage.png')
        ui.show_page('handoff'); ui.handoff_panel.show_step(1); shot('02-handoff-old.png')
        h = ui.handoff_panel
        h.show_step(2); h._put(h.summary_text, demo.SUMMARY); h._summary_edited(); h.save_summary()
        shot('03-handoff-saved.png')
        h.show_step(3); h.generate_new(force=True); shot('04-handoff-new.png')
        ui.show_page('settings'); ui.settings_tabs.select(ui.preferences_page); shot('05-settings.png')
        ui.settings_tabs.select(ui.config_page); ui.load_config(); shot('06-config.png')
        h.edit_templates(); dialog = h._template_dialog
        dialog.geometry('800x580+150+100'); shot('07-template-dialog.png', dialog)
        dialog.event_generate('<Escape>'); root.update()
        ui.show_page('usage'); ui.floating.render(rows); ui.floating.show()
        f = ui.floating; f.window.geometry('390x560+1350+40'); root.update()
        shot('08-floating-full.png', f.window)
        f._set_mode('selected')
        stale = next(r for r in rows if r['chat_id'] == record['chat_id']).copy()
        stale.update(state='stale', occupancy_percent=100., progress_percent=100.)
        f.render([stale]); f.toggle_compact(); root.update()
        shot('09-floating-compact-stale.png', f.window,
             'Real Tk; injected synthetic stale/100% state for clipping regression')
        f.close(); root.geometry('980x680+30+30'); ui.show_page('handoff'); h.show_step(3)
        shot('10-narrow-handoff.png')
        ui.show_page('usage'); shot('11-narrow-usage.png')
        root.destroy()
    (args.output / 'metadata.json').write_text(json.dumps({
        'timestamp_utc': datetime.now(timezone.utc).isoformat(),
        'platform': sys.platform, 'synthetic_data_only': True,
        'not_windows_dpi_acceptance': True, 'captures': captures}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'captures': len(captures), 'output': str(args.output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
