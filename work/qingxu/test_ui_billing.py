"""Real Tk checks on synthetic request metadata; no real user files touched."""
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from billing import Request, quote
from ui_billing import BillingPanel, PriceEditor
from test_billing import request
from test_billing_index import meta,ctx,event,write


class BillingGuiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name)/'home';(self.home/'sessions').mkdir(parents=True)
        self.root=tk.Tk();self.root.geometry('1000x800+0+0')
        style=ttk.Style(self.root);style.theme_use('clam')
        style.configure('.',font=('Noto Sans CJK SC',10));style.configure('Card.TFrame',background='white')
        self.app=SimpleNamespace(root=self.root,data_root=Path(self.tmp.name)/'data',
                prefs={'codex_home':str(self.home)},relay=SimpleNamespace(settings={'chat_db':str(self.home/'state.sqlite')}),
                chat={'chat_id':'t1','path':'/project'},font_family='Noto Sans CJK SC')
        self.panel=BillingPanel(self.root,self.app);self.panel.pack(fill='both',expand=True)
        self.now=datetime.now(timezone.utc).replace(microsecond=0)
        self.r1=request(time=(self.now-timedelta(hours=1)).isoformat(),status='success',tokens_per_second=20,ttft_ms=1500)
        self.r2=request(response_id='r2',time=(self.now-timedelta(days=1)).isoformat(),model='gpt-6-astra',status='error')
        self.panel.set_result({'requests':[self.r1,self.r2],'issues':{}})
        self.root.update()
    def tearDown(self):
        self.root.destroy()
    def test_default_summary_matches_rows(self):
        self.assertEqual(self.panel.stats['requests'],2)
        self.assertEqual(self.panel.stats['priced'],2)
        self.assertEqual(len(self.panel.trees['model'].get_children()),2)
    def test_filter_reconciles_kpis_chart_and_tables(self):
        self.panel.model.set('gpt-6-astra');self.panel.apply_filters()
        self.assertEqual(self.panel.stats['requests'],1)
        self.assertEqual(sum(s['requests'] for _,s in self.panel.date_rows),1)
        self.assertEqual(len(self.panel.trees['request'].get_children()),1)
    def test_daily_drill_keeps_selected_date(self):
        d=self.now.date().isoformat();self.panel.tz.set('UTC');self.panel.select_day(d)
        self.assertEqual(self.panel.start.get(),d);self.assertEqual(self.panel.end.get(),d)
        self.assertEqual(self.panel.tables.select(),str(self.panel.tab_frames['request']))
    def test_provider_model_double_click_drill(self):
        tree=self.panel.trees['model'];iid=tree.get_children()[0]
        ident,_=self.panel.row_objects['model'][iid];tree.selection_set(iid)
        self.panel.drill('model')
        self.assertEqual(self.panel.provider.get(),ident[0]);self.assertEqual(self.panel.model.get(),ident[1])
    def test_unknown_price_visible_not_zero(self):
        self.panel.set_result({'requests':[request(time=self.now.isoformat(),model='not-priced')],'issues':{}})
        self.assertEqual(self.panel.kpis['cost'][1].cget('text'),'—')
        self.assertIn('1 条价格或用量未知',self.panel.notice.get())
    def test_zero_and_missing_usage_not_confused(self):
        self.panel.set_result({'requests':[request(time=self.now.isoformat(),usage=None,status='error')],'issues':{}})
        self.assertEqual(self.panel.kpis['tokens'][1].cget('text'),'—')
        self.assertEqual(self.panel.stats['requests'],1)
    def test_missing_timing_status_displayed_unknown(self):
        self.panel.set_result({'requests':[request(time=self.now.isoformat())],'issues':{}})
        vals=self.panel.trees['model'].item('model-0','values')
        self.assertEqual(vals[4:7],('—','—','—'))
    def test_all_export_not_only_page(self):
        rows=[request(response_id=str(i),time=self.now.isoformat()) for i in range(150)]
        self.panel.set_result({'requests':rows,'issues':{}})
        self.assertEqual(len(self.panel.trees['request'].get_children()),100)
        self.panel.turn_page(1);self.assertEqual(len(self.panel.trees['request'].get_children()),50)
        target=Path(self.tmp.name)/'out.csv'
        with patch('ui_billing.filedialog.asksaveasfilename',return_value=str(target)):self.panel.export()
        self.assertEqual(len(target.read_text().splitlines()),151)
    def test_no_automatic_worker_until_tab_activated(self):
        self.assertIsNone(self.panel.future)
    def test_narrow_filters_not_cut(self):
        self.root.geometry('730x620');self.root.update()
        b=self.panel.refresh_button
        self.assertLessEqual(b.winfo_rootx()+b.winfo_width(),self.root.winfo_rootx()+self.root.winfo_width())
        self.assertGreaterEqual(b.winfo_width(),b.winfo_reqwidth())
    def test_pricing_save_actions_pinned_at_small_height(self):
        win=PriceEditor(self.panel);win.geometry('720x650');self.root.update()
        def descend(node):
            for w in node.winfo_children():
                yield w;yield from descend(w)
        button=next(w for w in descend(win) if isinstance(w,ttk.Button) and w.cget('text')=='备份并保存单价')
        self.assertTrue(button.winfo_ismapped())
        self.assertLessEqual(button.winfo_rooty()+button.winfo_height(),win.winfo_rooty()+win.winfo_height())
        win.destroy()
    def test_price_editor_changes_calculation_after_save(self):
        old=self.panel.stats['cost']
        win=PriceEditor(self.panel);win.tree.selection_set('1');win.choose()
        win.vars['input'].set('100');win.save();self.root.update()
        self.assertGreater(self.panel.stats['cost'],old)
        self.assertTrue(self.panel.price_store.path.is_file())
    def test_price_editor_preserves_source_without_field_edits(self):
        win=PriceEditor(self.panel);win.tree.selection_set('1');win.choose()
        win.allow.set(False);win.save()
        self.assertTrue(self.panel.prices['rates'][1]['source'].startswith('https:'))
    def test_source_change_clears_amount_and_pending_result(self):
        pending=Future();pending.set_result({'requests':[self.r1],'issues':{}})
        self.panel.future=pending;self.panel.source=str(self.home)
        self.app.prefs['codex_home']=str(self.home/'new')
        self.panel.source_changed();self.panel._poll()
        self.assertFalse(self.panel.requests)
        self.assertIn('丢弃旧',self.panel.sync_text.get())
    def test_real_async_scan_uses_isolated_log(self):
        write(self.home/'sessions'/'r.jsonl',[meta(),ctx(),event()])
        self.panel.refresh()
        deadline=time.monotonic()+4
        while self.panel.future is not None and time.monotonic()<deadline:
            self.root.update();time.sleep(.015)
        self.assertIsNone(self.panel.future);self.assertEqual(len(self.panel.requests),1)
    def test_reset_filters(self):
        self.panel.model.set('gpt-6-astra');self.panel.scope.set('当前会话');self.panel.reset()
        self.assertEqual(self.panel.model.get(),'全部模型');self.assertEqual(self.panel.scope.get(),'全部项目')
    def test_kpi_switches_chart_metric(self):
        self.panel.select_metric('tokens');self.assertEqual(self.panel.chart.metric,'tokens')
    def test_request_details_open(self):
        self.panel.request_detail(self.r1);self.root.update()
        dialogs=[c for c in self.panel.winfo_children() if isinstance(c,tk.Toplevel)]
        self.assertEqual(len(dialogs),1);dialogs[0].destroy()

if __name__=='__main__':unittest.main()
