"""Usage statistics UI integration with synthetic records only."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import gc
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest

from billing import short_number
from test_billing import request
from ui_billing import BillingPanel, DailyChart, PriceEditor, TEAL


class UsageStatisticsUiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=tk.Tk();self.root.geometry('1000x800+0+0')
        self.root.withdraw()
        style=ttk.Style(self.root);style.theme_use('clam')
        style.configure('Card.TFrame',background='white')
        self.home=Path(self.tmp.name)/'home';(self.home/'sessions').mkdir(parents=True)
        self.today_values=[]
        self.app=SimpleNamespace(root=self.root,data_root=Path(self.tmp.name)/'data',
            prefs={'codex_home':str(self.home)},relay=SimpleNamespace(settings={'chat_db':str(self.home/'state.sqlite')}),
            chat={'chat_id':'t1','path':'/project'},font_family='TkDefaultFont',set_today_cost=self.today_values.append)
        self.panel=BillingPanel(self.root,self.app);self.panel.pack(fill='both',expand=True)
        self.now=datetime.now(timezone.utc)
        self.recent=request(response_id='recent',time=(self.now-timedelta(seconds=3)).isoformat())
        self.panel.set_result({'requests':[self.recent],'issues':{}})
        self.root.update()

    def tearDown(self):
        self.root.destroy()
        self.panel.executor.shutdown(wait=True,cancel_futures=True)
        del self.panel,self.app,self.root
        gc.collect()

    def choose_period(self,kind):
        self.panel.period.set(kind);self.panel._apply_period();self.panel.apply_filters()
        self.root.update_idletasks()

    def test_periods_only_all_uses_heatmap(self):
        for kind,expected in (('今天','hour'),('近 24 小时','hour'),
                              ('近 7 天','day'),('近 30 天','day'),('全部','heatmap')):
            with self.subTest(period=kind):
                self.choose_period(kind)
                self.assertEqual(self.panel.chart.period,expected)
                self.assertEqual(self.panel.stats['requests'],1)
        self.panel.period.set('自定义');self.panel._apply_period()
        for days,expected in ((5,'day'),(90,'week'),(400,'month')):
            with self.subTest(custom_days=days):
                self.panel.start.set((self.now.date()-timedelta(days=days)).isoformat())
                self.panel.end.set(self.now.date().isoformat())
                self.panel.apply_filters()
                self.assertEqual(self.panel.chart.period,expected)
                self.assertNotEqual(self.panel.chart.period,'heatmap')

    def test_metric_switch_updates_both_chart_and_choice(self):
        for kind in ('近 7 天','全部'):
            self.choose_period(kind)
            for metric in ('tokens','requests','cost'):
                with self.subTest(period=kind,metric=metric):
                    self.panel.select_metric(metric)
                    self.assertEqual(self.panel.chart.metric,metric)
                    self.assertEqual(self.panel.metric_choice.get(),metric)

    def test_small_cost_bar_has_flat_base_and_nonzero_axis_labels(self):
        self.root.deiconify();self.root.update()
        chart=DailyChart(self.root,lambda _:None)
        chart.pack(fill='x');self.root.update()
        chart.render([('2026-11-11',{'requests':1,'cost':0.002,'unpriced':0})],metric='cost')
        width,height=chart.winfo_width(),chart.winfo_height()
        center=(56+width-16)/2
        for x in (center-18,center+18):
            items=chart.find_overlapping(x,height-36,x,height-36)
            self.assertTrue(any(chart.itemcget(item,'fill')==TEAL for item in items))
        labels=[chart.itemcget(item,'text') for item in chart.find_all() if chart.type(item)=='text']
        self.assertTrue(any(label.startswith('$0.00') and label!='$0.00' for label in labels))

    def test_metric_keyboard_focus_scrolls_into_a_short_window(self):
        self.root.deiconify();self.root.geometry('730x220');self.root.update()
        button=self.panel.metric_buttons['tokens']
        button.event_generate('<FocusIn>');self.root.update()
        canvas=self.panel.canvas
        top=button.winfo_rooty()-canvas.winfo_rooty()
        self.assertGreaterEqual(top,0)
        self.assertLessEqual(top+button.winfo_height(),canvas.winfo_height())

    def test_request_columns_and_unknown_values(self):
        unknown=replace(request(response_id='unknown',time=(self.now-timedelta(seconds=6)).isoformat(),
                                model='no-such-price'),cache_known=False)
        self.panel.set_result({'requests':[self.recent,unknown],'issues':{}})
        self.panel.tables.select(self.panel.tab_frames['request']);self.panel.render_tables()
        tree=self.panel.trees['request']
        self.assertEqual(tuple(tree.cget('columns')),('time','model','input','output','cached','cost'))
        rows={tree.item(iid,'values')[1]:tree.item(iid,'values') for iid in tree.get_children()}
        unknown_values=rows['no-such-price']
        self.assertEqual(unknown_values[2],short_number(unknown.usage['inputTokens']))
        self.assertEqual(unknown_values[3],short_number(unknown.usage['outputTokens']))
        self.assertEqual(unknown_values[4:6],('—','—'))
        self.panel.cost_mode.set('来源报告金额');self.panel.apply_filters()
        self.assertEqual(tree.heading('cost','text'),'来源金额 USD')

    def test_request_sort_compares_instants_across_offsets(self):
        older=request(response_id='older',time='2026-11-11T23:00:00+08:00')
        newer=request(response_id='newer',time='2026-11-11T18:00:00+00:00')
        self.panel.period.set('全部')
        self.panel.set_result({'requests':[older,newer],'issues':{}})
        first=self.panel.trees['request'].get_children()[0]
        self.assertEqual(self.panel.row_objects['request'][first].response_id,'newer')

    def test_all_heatmap_exposes_days_across_years(self):
        history=[request(response_id='old',time=(self.now-timedelta(days=800)).isoformat()),
                 request(response_id='middle',time=(self.now-timedelta(days=400)).isoformat()),self.recent]
        self.panel.set_result({'requests':history,'issues':{}})
        self.panel.tz.set('UTC')
        self.choose_period('全部')
        cells={hit[2] for hit in self.panel.chart.hitboxes}
        self.assertTrue({r.time[:10] for r in history} <= cells)
        self.assertGreater(int(float(self.panel.chart.cget('height'))),188)
        selected=[]
        self.panel.chart.on_day=selected.append
        for hit in self.panel.chart.hitboxes:
            if hit[2] in {r.time[:10] for r in history}:
                self.panel.chart.click(SimpleNamespace(x=(hit[0]+hit[1])/2,y=(hit[4]+hit[5])/2))
        self.assertEqual(set(selected),{r.time[:10] for r in history})

    def test_today_callback_and_source_change_clear(self):
        self.assertTrue(self.today_values[-1].startswith('今日 $'))
        self.panel.after_cancel(self.panel.today_job);self.panel.today_job=None
        self.panel.today_day-=timedelta(days=1)
        self.panel._check_today()
        self.assertEqual(self.panel.today_day,datetime.now(timezone.utc).astimezone().date())
        unknown=request(response_id='unpriced-today',time=(self.now-timedelta(seconds=2)).isoformat(),model='not-priced')
        self.panel.set_result({'requests':[self.recent,unknown],'issues':{}})
        self.assertTrue(self.today_values[-1].endswith('*'))
        self.panel.source_changed()
        self.assertEqual(self.today_values[-1],'今日 —')
        self.assertFalse(self.panel.requests)
        self.assertFalse(self.panel.quote_cache)

    def test_changing_price_invalidates_quote_cache(self):
        old=self.panel.stats['cost']
        old_quote=self.panel.quote_cache[id(self.recent)]
        editor=PriceEditor(self.panel)
        index=next(i for i,r in enumerate(editor.data['rates'])
                   if r['provider']==self.recent.provider and r['model']==self.recent.model)
        editor.tree.selection_set(str(index));editor.choose()
        editor.vars['input'].set('100');editor.save();self.root.update()
        self.assertGreater(self.panel.stats['cost'],old)
        self.assertIsNot(self.panel.quote_cache[id(self.recent)],old_quote)
        self.assertTrue(self.panel.price_store.path.is_file())
        self.assertTrue(self.today_values[-1].startswith('今日 $'))


if __name__=='__main__':
    unittest.main()
