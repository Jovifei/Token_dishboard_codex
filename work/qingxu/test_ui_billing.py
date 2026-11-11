"""Real Tk checks on synthetic request metadata; no real user files touched."""
from concurrent.futures import Future
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import gc
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from billing import Request, quote
from billing_index import BillingIndex, zstd
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
        self.panel.executor.shutdown(wait=True,cancel_futures=True)
        del self.panel,self.app,self.root
        gc.collect()

    def wait_for_sync(self):
        deadline=time.monotonic()+15
        while self.panel.future is not None and time.monotonic()<deadline:
            self.root.update();time.sleep(.015)
        self.assertIsNone(self.panel.future)
    def test_default_summary_matches_rows(self):
        self.assertEqual(self.panel.stats['requests'],2)
        self.assertEqual(self.panel.stats['priced'],2)
        self.assertEqual(len(self.panel.trees['model'].get_children()),2)
    def test_filter_reconciles_kpis_chart_and_tables(self):
        self.panel.model.set('gpt-6-astra');self.panel.apply_filters()
        self.assertEqual(self.panel.stats['requests'],1)
        self.assertEqual(sum(s['requests'] for _,s in self.panel.date_rows),1)
        self.assertEqual(len(self.panel.trees['request'].get_children()),1)

    def test_project_filter_and_drill_use_verified_identity(self):
        first = replace(self.r1, app_project_id='project-a')
        worktree = replace(self.r2, app_project_id='project-a', cwd='/another/worktree')
        other = replace(first, response_id='same-name-other', app_project_id='project-b')
        unassigned = replace(first, response_id='unassigned', app_project_id='')
        self.panel.set_result({'requests':[first, worktree, other, unassigned], 'issues':{}})
        self.app.chat['app_project_id']='project-a'
        self.panel.scope.set('当前项目');self.panel.apply_filters()
        self.assertEqual(self.panel.stats['requests'], 2)
        self.assertIn('项目归属未确认', self.panel.notice.get())
        self.app.chat['app_project_id']=''
        self.panel.apply_filters()
        self.assertEqual(self.panel.stats['requests'], 1)
        self.assertEqual(self.panel.filtered[0].response_id, 'unassigned')
        self.panel.scope.set('全部项目');self.panel.apply_filters()
        tree = self.panel.trees['project']
        other_row = next(iid for iid, (ident, _) in self.panel.row_objects['project'].items()
                         if ident[0] == 'project:project-b')
        tree.selection_set(other_row);self.panel.drill('project')
        self.assertIn('1 条请求', self.panel.detail_text.get())

    def test_file_limit_discloses_incomplete_scope(self):
        self.panel.set_result({'requests':[self.r1], 'issues':{'file_limit_reached':1}})
        self.assertIn('范围可能不完整', self.panel.notice.get())
    def test_compressed_failures_disclose_incomplete_scope(self):
        self.panel.set_result({'requests':[self.r1], 'issues':{'compressed_unavailable':1}})
        self.assertIn('范围可能不完整', self.panel.notice.get())
        self.assertIn('Python 3.11–3.13 需安装 backports.zstd', self.panel.notice.get())
        self.panel.set_result({'requests':[self.r1], 'issues':{'compressed_read_errors':1}})
        self.assertIn('范围可能不完整', self.panel.notice.get())
        self.assertIn('压缩归档损坏或截断，未纳入统计', self.panel.notice.get())
        self.panel.set_result({'requests':[self.r1], 'issues':{'compressed_files_unsupported':1}})
        self.assertIn('范围可能不完整', self.panel.notice.get())
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
        self.assertEqual(len(target.read_text(encoding='utf-8').splitlines()),151)
    def test_no_automatic_worker_until_tab_activated(self):
        self.assertIsNone(self.panel.future)
    def test_narrow_filters_not_cut(self):
        self.root.geometry('730x620');self.root.update()
        b=self.panel.refresh_button
        self.assertLessEqual(b.winfo_rootx()+b.winfo_width(),self.root.winfo_rootx()+self.root.winfo_width())
        self.assertGreaterEqual(b.winfo_width(),b.winfo_reqwidth())

    def test_filter_controls_fit_at_windows_dpi_scales(self):
        selectors = self.panel.refresh_button.master
        dates = self.panel.start_entry.master.master
        controls = list(selectors.winfo_children())
        controls.extend((self.panel.start_entry, self.panel.end_entry))
        controls.extend(widget for widget in dates.winfo_children()
                        if isinstance(widget, (ttk.Combobox, ttk.Button)))
        self.assertEqual(len(controls), 11)

        self.root.geometry('730x620')
        for dpi in (96, 144, 192):
            with self.subTest(dpi=dpi):
                self.root.tk.call('tk', 'scaling', dpi / 72)
                ttk.Style(self.root).configure('.', font=('Microsoft YaHei UI', 10))
                self.root.update()
                left = self.panel.winfo_rootx()
                right = left + self.panel.winfo_width()
                top = self.panel.winfo_rooty()
                bottom = top + self.panel.winfo_height()
                for control in controls:
                    with self.subTest(control=str(control)):
                        self.assertTrue(control.winfo_ismapped())
                        self.assertGreaterEqual(control.winfo_rootx(), left)
                        self.assertLessEqual(control.winfo_rootx() + control.winfo_width(), right)
                        self.assertGreaterEqual(control.winfo_rooty(), top)
                        self.assertLessEqual(control.winfo_rooty() + control.winfo_height(), bottom)
                        self.assertGreaterEqual(control.winfo_width(), control.winfo_reqwidth())

        self.panel.period.set('自定义')
        self.panel.changed(self.panel.period)
        self.assertTrue(self.panel.start_entry.instate(['!readonly', '!disabled']))
        self.assertTrue(self.panel.end_entry.instate(['!readonly', '!disabled']))

    def test_short_panel_scrolls_focused_filters_into_view(self):
        self.root.tk.call('tk', 'scaling', 192 / 72)
        ttk.Style(self.root).configure('.', font=('Microsoft YaHei UI', 10))
        self.root.geometry('730x152')
        self.root.update()
        self.assertTrue(self.panel._filters_in_canvas)

        selectors = self.panel.refresh_button.master
        dates = self.panel.start_entry.master.master
        controls = list(selectors.winfo_children())
        controls.extend((self.panel.start_entry, self.panel.end_entry))
        controls.extend(widget for widget in dates.winfo_children()
                        if isinstance(widget, (ttk.Combobox, ttk.Button)))
        canvas = self.panel.canvas
        for control in controls:
            with self.subTest(control=str(control)):
                control.event_generate('<FocusIn>')
                self.root.update()
                x = control.winfo_rootx() - canvas.winfo_rootx()
                y = control.winfo_rooty() - canvas.winfo_rooty()
                self.assertGreaterEqual(x, 0)
                self.assertGreaterEqual(y, 0)
                self.assertLessEqual(x + control.winfo_width(), canvas.winfo_width())
                self.assertLessEqual(y + control.winfo_height(), canvas.winfo_height())

        canvas.yview_moveto(0)
        self.root.update()
        before = canvas.yview()
        unrelated = ttk.Label(self.root)
        self.panel._wheel(SimpleNamespace(widget=unrelated, delta=-120, num=0))
        self.assertEqual(canvas.yview(), before)
        self.panel._wheel(SimpleNamespace(widget=self.panel.start_entry, delta=-120, num=0))
        self.assertGreater(canvas.yview()[0], before[0])

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
        self.wait_for_sync()
        self.assertEqual(len(self.panel.requests),1)

    @unittest.skipIf(zstd is None,'Python Zstandard decoder is unavailable')
    def test_real_compressed_archive_syncs_multiple_batches(self):
        items=[meta(),ctx(),event('request-a'),event('request-b'),event('request-c')]
        source=b''.join((json.dumps(item)+'\n').encode('utf-8') for item in items)
        (self.home/'sessions'/'synthetic.jsonl.zst').write_bytes(zstd.compress(source))
        calls=[]
        class SmallBudgetIndex(BillingIndex):
            def scan(self,*args,**kwargs):
                calls.append(('scan',kwargs.get('include_requests')))
                return super().scan(*args,budget=128,**kwargs)
            def load(self,*args,**kwargs):
                calls.append(('load',None))
                return super().load(*args,**kwargs)
        with patch('ui_billing.BillingIndex',SmallBudgetIndex):
            self.panel.refresh()
            self.wait_for_sync()
        self.assertGreater(len(calls),2)
        self.assertEqual(calls[-1],('load',None))
        self.assertEqual(sum(item[0]=='load' for item in calls),1)
        self.assertTrue(all(not include for kind,include in calls if kind=='scan'))
        self.assertEqual({r.response_id for r in self.panel.requests},
                         {'request-a','request-b','request-c'})
        self.assertGreater(self.panel.result['issues']['sync_batches'],1)
        self.assertFalse(self.panel.result['issues'].get('resume_required'))
        self.panel.tz.set('UTC');self.panel.period.set('自定义');self.panel._apply_period()
        self.panel.start.set('2026-11-11');self.panel.end.set('2026-11-11');self.panel.apply_filters()
        self.assertEqual(self.panel.stats['requests'],3)

    def test_refresh_worker_does_not_capture_widgets(self):
        pending=Future()
        with patch.object(self.panel.executor,'submit',return_value=pending) as submit:
            self.panel.refresh()
            work=submit.call_args.args[0]
        self.assertFalse(any(isinstance(cell.cell_contents,tk.Misc) for cell in work.__closure__))
        pending.cancel()

    def test_refresh_continues_batches_and_imports_only_once(self):
        calls=[];r1=self.r1
        def scan(home,db,*,stop,include_requests):
            calls.append(('scan',threading.get_ident(),include_requests))
            return {'issues':{'resume_required':len(calls)<4,'bytes_read':100}}
        def load(home,db,issues):
            calls.append(('load',threading.get_ident()))
            return {'requests':[r1],'issues':dict(issues)}
        def import_file(path):
            calls.append(('import',threading.get_ident()))
            return 1
        index=SimpleNamespace(scan=scan,load=load,import_file=import_file)
        with patch('ui_billing.BillingIndex',return_value=index):
            self.panel.refresh('ledger.json')
            self.wait_for_sync()
        self.assertEqual([c[0] for c in calls],['import','scan','scan','scan','load'])
        self.assertTrue(all(c[1]!=threading.get_ident() for c in calls))
        self.assertTrue(all(not c[2] for c in calls if c[0]=='scan'))
        self.assertEqual(self.panel.result['issues']['bytes_read'],300)
        self.assertEqual(self.panel.result['issues']['sync_batches'],3)
        self.assertEqual(self.panel.result['imported'],1)
        self.assertIn('3 批',self.panel.sync_text.get())

    def test_source_change_stops_batch_loop(self):
        started=threading.Event();release=threading.Event();calls=[];r1=self.r1
        self.addCleanup(release.set)
        def scan(home,db,*,stop,include_requests):
            calls.append('scan');started.set()
            if not release.wait(10):raise TimeoutError('test did not release scan')
            return {'issues':{'resume_required':1,'bytes_read':100}}
        def load(*args):
            calls.append('load')
            return {'requests':[r1],'issues':{}}
        index=SimpleNamespace(scan=scan,load=load)
        with patch('ui_billing.BillingIndex',return_value=index):
            self.panel.refresh()
            self.assertTrue(started.wait(10))
            self.app.prefs['codex_home']=str(self.home/'new')
            self.panel.source_changed();release.set()
            self.wait_for_sync()
        self.assertEqual(calls,['scan'])
        self.assertFalse(self.panel.requests)
        self.assertIn('丢弃旧',self.panel.sync_text.get())

    def test_source_change_starts_new_scan_only_after_old_worker_stops(self):
        started=threading.Event();release=threading.Event();calls=[];r1=self.r1
        self.addCleanup(release.set)
        def old_scan(home,db,*,stop,include_requests):
            calls.append('old');started.set()
            if not release.wait(10):raise TimeoutError('test did not release scan')
            return {'issues':{'resume_required':1,'bytes_read':100}}
        def new_scan(home,db,*,stop,include_requests):
            if not release.is_set():raise AssertionError('old worker did not finish')
            calls.append('new')
            return {'issues':{'resume_required':0,'bytes_read':10}}
        def new_load(home,db,issues):
            calls.append('load')
            return {'requests':[r1],'issues':dict(issues)}
        old=SimpleNamespace(scan=old_scan)
        new=SimpleNamespace(scan=new_scan,load=new_load)
        with patch('ui_billing.BillingIndex',side_effect=[old,new]):
            self.panel.refresh()
            self.assertTrue(started.wait(10))
            self.app.prefs['codex_home']=str(self.home/'new')
            self.panel.source_changed();self.panel.activate();release.set()
            self.wait_for_sync()
        self.assertEqual(calls,['old','new','load'])
        self.assertEqual(self.panel.result['issues']['bytes_read'],10)

    def test_zero_progress_stops_automatic_retry_and_warns(self):
        calls=[];r1=self.r1
        def scan(home,db,*,stop,include_requests):
            calls.append('scan')
            return {'issues':{'resume_required':1,'bytes_read':0,'compressed_read_errors':1}}
        def load(home,db,issues):
            calls.append('load')
            return {'requests':[r1],'issues':dict(issues)}
        index=SimpleNamespace(scan=scan,load=load)
        with patch('ui_billing.BillingIndex',return_value=index):
            self.panel.refresh()
            self.wait_for_sync()
        self.assertEqual(calls,['scan','load'])
        self.assertEqual(self.panel.result['issues']['continuation_stalled'],1)
        self.assertIn('范围可能不完整',self.panel.notice.get())
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
