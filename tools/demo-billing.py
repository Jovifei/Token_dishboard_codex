"""Preview the billing screen with labelled synthetic data and an isolated Codex home."""
import argparse
from pathlib import Path
import sys
import tempfile
import tkinter as tk

PROJECT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(PROJECT/'work/qingxu'),str(PROJECT/'tools/fixtures')]
from billing import Request
import billing_demo
import demo
from relay import Relay
from workbench import Workbench


def make(root_dir):
    fixture=demo.create(Path(root_dir)/'fixture')
    root=tk.Tk()
    ui=Workbench(root,Relay(fixture['settings']),fixture['prefs'])
    root.title(root.title()+' · 计费合成演示（不是用户账单）')
    root.geometry('1280x1000+0+0')
    panel=ui.usage_panel.billing_panel
    panel.active=True
    panel.set_result({'requests':[Request.from_dict(r) for r in billing_demo.requests()],
                      'issues':{},'synthetic':True,'mapping_note':''})
    panel.sync_text.set('合成演示 · 无真实账户读取 · 84 条请求')
    ui.show_page('usage')
    ui.usage_panel.tabs.select(ui.usage_panel.billing_tab)
    return root,ui,panel


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='qingxu-billing-demo-') as tmp:
        root,ui,panel=make(tmp)
        try:
            if args.check:
                root.update_idletasks()
                assert panel.stats['requests']==84
                assert panel.stats['priced']==84
                print('PASS: 84 labelled synthetic billing requests')
            else:root.mainloop()
        finally:
            try:root.destroy()
            except tk.TclError:pass

if __name__=='__main__':main()
