"""Capture actual Tk Quiet UI with synthetic data. Dev-only Pillow dependency."""
import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import tkinter as tk
from tkinter import ttk
from PIL import ImageGrab

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('quiet_demo',ROOT/'tools/demo-billing.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
from ui_controls import install_controls
from ui_quiet import install_quiet, decorate_panels, CANVAS, INK, MUTED


def capture(out):
    out.mkdir(parents=True,exist_ok=True)
    scenes=[]
    def snapshot(root,name):
        for _ in range(4):root.update();time.sleep(.02)
        x,y=root.winfo_rootx(),root.winfo_rooty()
        ImageGrab.grab(bbox=(x,y,x+root.winfo_width(),y+root.winfo_height())).save(out/(name+'.png'))
        scenes.append({'file':name+'.png','width':root.winfo_width(),'height':root.winfo_height(),
                       'source':'actual Tk screenshot; isolated synthetic data'})
    with tempfile.TemporaryDirectory(prefix='qingxu-quiet-review-') as tmp:
        root,ui,billing=module.make(tmp)
        root.geometry('1320x880+0+0')
        snapshot(root,'01-statistics')
        ui.show_page('usage');snapshot(root,'02-context')
        ui.show_page('handoff');snapshot(root,'03-handoff')
        ui.handoff_panel.show_step(2);snapshot(root,'04-summary')
        ui.show_page('settings');snapshot(root,'05-settings')
        ui.show_page('usage');ui.floating.render(ui.rows);ui.floating.show()
        snapshot(ui.floating.window,'06-floating')
        ui.floating.toggle_compact();snapshot(ui.floating.window,'07-compact')
        ui.floating.close()
        root.geometry('980x680+0+0');ui.show_page('statistics');billing.canvas.yview_moveto(0)
        snapshot(root,'08-narrow-statistics')
        ui.show_page('handoff');ui.handoff_panel.show_step(1)
        snapshot(root,'09-narrow-handoff')
        ui.handoff_panel.edit_templates();root.update()
        window=ui.handoff_panel._template_dialog
        snapshot(window,'10-template-dialog')
        window.destroy();root.destroy()
    root=tk.Tk();root.configure(background=CANVAS);root.geometry('1020x420+0+0')
    style=ttk.Style(root);style.theme_use('clam');install_controls(root)
    # Use the available CJK default carried by the demo in this interpreter.
    from tkinter import font
    names=set(font.families(root))
    family=next((x for x in ('Microsoft YaHei UI','Noto Sans CJK SC','Noto Sans CJK JP') if x in names),'TkDefaultFont')
    install_quiet(root,family)
    body=ttk.Frame(root,style='App.TFrame',padding=30);body.pack(fill='both',expand=True)
    ttk.Label(body,text='点击反馈 · 无凹陷，无位移',style='Title.TLabel').grid(row=0,column=0,columnspan=5,sticky='w',pady=(0,10))
    ttk.Label(body,text='同一套原生控件的真实状态；不播放渐变、缩放或弹跳。',style='Muted.TLabel').grid(row=1,column=0,columnspan=5,sticky='w',pady=(0,24))
    for col,(caption,state) in enumerate((('默认',()),('悬停',('active',)),('按下',('pressed',)),('键盘焦点',('focus',)),('禁用',('disabled',)))):
        body.columnconfigure(col,weight=1)
        ttk.Label(body,text=caption,style='Muted.TLabel').grid(row=2,column=col,pady=8)
        for row,name,label in ((3,'Primary.TButton','复制新聊指令'),(4,'Outline.TButton','查看数据来源')):
            button=ttk.Button(body,text=label,style=name,takefocus=True)
            button.grid(row=row,column=col,padx=8,pady=12,sticky='ew');button.state(state)
    decorate_panels(root);snapshot(root,'11-interaction-states');root.destroy()
    (out/'manifest.json').write_text(json.dumps({'synthetic':True,'platform':'Linux Tk / Xvfb; not Windows DPI acceptance','screens':scenes},ensure_ascii=False,indent=2),encoding='utf-8')
    return scenes


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'docs/design/quiet/screens')
    args=parser.parse_args();print(json.dumps(capture(args.output),ensure_ascii=False))
