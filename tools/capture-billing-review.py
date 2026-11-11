"""Optional screenshot QA: requires Pillow and a desktop (or Xvfb); not a runtime dependency."""
import importlib.util
from pathlib import Path
import tempfile
from PIL import ImageGrab
import time
path=Path(__file__).resolve().with_name('demo-billing.py')
s=importlib.util.spec_from_file_location('demo_billing',path);module=importlib.util.module_from_spec(s);s.loader.exec_module(module)
d=Path(__file__).resolve().parents[1]/'billing-review-screens';d.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory() as tmp:
 root,ui,panel=module.make(tmp)
 root.geometry("1280x1140+0+0")
 root.update()
 def snap(name):
  for _ in range(5):root.update();time.sleep(.025)
  x,y=root.winfo_rootx(),root.winfo_rooty()
  ImageGrab.grab(bbox=(x,y,x+root.winfo_width(),y+root.winfo_height())).save(d/name)
 snap('01-billing-overview.png')
 panel.tables.select(panel.tab_frames['request']);panel.canvas.yview_moveto(1)
 snap('02-billing-requests.png')
 panel.provider.set('openai');panel.model.set('gpt-6.1-terra');panel.apply_filters();panel.canvas.yview_moveto(0)
 snap('03-billing-filter.png')
 panel.reset();panel.edit_prices();root.update()
 win=next(w for w in panel.winfo_children() if w.winfo_class()=='Toplevel')
 win.geometry('880x740+140+80');win.update()
 win.tree.selection_set('1');win.choose();win.update()
 for _ in range(4):root.update();time.sleep(.04)
 x,y=win.winfo_rootx(),win.winfo_rooty()
 ImageGrab.grab(bbox=(x,y,x+win.winfo_width(),y+win.winfo_height())).save(d/'04-price-settings.png')
 win.destroy()
 root.geometry('980x680+0+0');panel.canvas.yview_moveto(0);snap('05-narrow-top.png')
 panel.canvas.yview_moveto(1);snap('06-narrow-table.png')
 root.geometry('1280x1140+0+0');panel.reset();panel.canvas.yview_moveto(0)
 panel.request_detail(panel.requests[0]);root.update()
 win=next(w for w in panel.winfo_children() if w.winfo_class()=='Toplevel')
 win.geometry('760x560+180+90');win.update()
 x,y=win.winfo_rootx(),win.winfo_rooty()
 ImageGrab.grab(bbox=(x,y,x+win.winfo_width(),y+win.winfo_height())).save(d/'07-request-detail.png')
 win.destroy()
 from dataclasses import replace
 panel.set_result({'requests':[replace(r,model='未配置模型') if i%3==0 else replace(r,status='unknown',tokens_per_second=None,ttft_ms=None) for i,r in enumerate(panel.requests)],'issues':{},'synthetic':True})
 panel.tables.select(panel.tab_frames['model']);snap('08-partial-prices.png')
 panel.set_result({'requests':[],'issues':{'files_seen':2,'files_without_requests':2,'context_snapshots_ignored':14},'synthetic':True})
 snap('09-no-request-records.png')
 root.destroy()
