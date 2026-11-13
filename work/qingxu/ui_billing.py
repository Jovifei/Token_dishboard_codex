"""Native Tk billing screen inspired by the supplied usage-dashboard reference."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
from queue import Empty, SimpleQueue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, font, messagebox
import webbrowser

from billing import (Request, PriceStore, default_prices, validate_prices, summary, quote, project_identity,
                     filter_requests, group_requests, daily, money, short_number, export_csv, fixed_timezone, timestamp,
                     period_bounds, period_buckets)
from billing_index import BillingIndex
from ui_motion import attach_motion_tree, reduced_motion
from ui_brand import set_window_icon
from ui_controls import ChoiceButton, DropButton, install_controls
from ui_date_range import DateRangeButton, custom_bounds

from ui_quiet import CANVAS as BG, SURFACE as CARD, INK, MUTED, BLUE, LINE, AMBER as ORANGE
TEAL = BLUE
PERIODS = ('今天', '近 24 小时', '近 7 天', '近 30 天', '全部', '自定义')


class RoundedCard(tk.Canvas):
    """A bordered card with a normal Tk frame for accessible native controls."""
    def __init__(self, parent, padding=(12, 10)):
        super().__init__(parent, background=BG, highlightthickness=0, width=1, height=80)
        self.content=ttk.Frame(self,style='Card.TFrame',padding=padding)
        self._content_id=self.create_window(8,8,anchor='nw',window=self.content)
        self.bind('<Configure>',self._draw)
        self.content.bind('<Configure>',self._fit)

    def _fit(self,_event=None):
        height=self.content.winfo_reqheight()+16
        if int(float(self.cget('height'))) != height:
            self.configure(height=height)

    def _draw(self,event):
        w,h=max(20,event.width),max(20,event.height)
        self.delete('border')
        r=16
        self.create_polygon(1,r,1,1,r,1,w-r,1,w-1,1,w-1,r,
                            w-1,h-r,w-1,h-1,w-r,h-1,r,h-1,1,h-1,1,h-r,
                            smooth=True,splinesteps=24,fill=CARD,outline=LINE,tags='border')
        self.tag_lower('border')
        self.itemconfigure(self._content_id,width=max(1,w-16))


class DailyChart(tk.Canvas):
    """Bar chart for finite periods and daily calendar heatmap for all history."""
    def __init__(self, parent, on_day):
        super().__init__(parent, height=132, background=CARD, highlightthickness=0, takefocus=True)
        self.on_day=on_day
        self.rows=[]
        self.metric='cost'
        self.period='day'
        self.hitboxes=[]
        self.hover=None
        self.history_expanded=False
        self.bind('<Configure>', self.draw)
        self.bind('<Motion>', self.motion)
        self.bind('<Leave>', lambda _: self._hide_tip())
        self.bind('<ButtonRelease-1>', self.click)

    def render(self, rows, metric='cost', period='day'):
        self.rows,self.metric,self.period=rows,metric,period
        self.draw()

    def draw(self, event=None):
        self.delete('all')
        self.hitboxes=[]
        if self.period=='heatmap':
            self._heatmap()
            return
        target_height=104 if getattr(self,'compact',False) else 132
        if int(float(self.cget('height'))) != target_height:
            self.configure(height=target_height)
        w,h=max(360,self.winfo_width()),max(target_height,self.winfo_height())
        left,right,top,bottom=56,w-16,12,h-26
        values=[float(r[self.metric]) if r[self.metric] is not None else None for _,r in self.rows]
        peak=max([v for v in values if v is not None]+[0.]) or 1.
        if self.metric=='cache_hit':
            peak=100
        peak=peak*1.12 if self.metric!='cache_hit' else peak
        for frac in (0,.5,1):
            y=bottom-(bottom-top)*frac
            self.create_line(left,y,right,y,fill=LINE)
            v=peak*frac
            label=(('$'+f'{v:.2g}' if 0<v<.01 else money(Decimal(str(v)),2))
                   if self.metric=='cost' else f'{v:.0f}%' if self.metric=='cache_hit' else short_number(round(v)))
            self.create_text(left-8,y,text=label,anchor='e',fill=MUTED,font=('TkDefaultFont',9))
        n=len(self.rows)
        if not n:
            self.create_text(w/2,h/2,text='选择时间范围并同步本机用量',fill=MUTED)
            return
        step=(right-left)/n
        stride=max(1,(n+10)//11)
        for i,((day,stats),v) in enumerate(zip(self.rows,values)):
            x=left+(i+.5)*step
            half=min(24,max(2,step*.3))
            if v is None:
                if stats['requests']:
                    self.create_rectangle(x-half,bottom-7,x+half,bottom,outline=ORANGE,fill='#FFF2DB',stipple='gray50')
            elif v>0:
                y=bottom-(bottom-top)*v/peak
                radius=min(5,half/2,(bottom-y)/2)
                inset=radius*.3
                self.create_polygon(x-half,bottom,x-half,y+radius,x-half+inset,y+inset,x-half+radius,y,
                                    x+half-radius,y,x+half-inset,y+inset,x+half,y+radius,x+half,bottom,
                                    outline='',fill=TEAL)
                if self.metric=='cost' and stats['unpriced']:
                    self.create_text(x,max(top,y-9),text='*',fill=ORANGE)
            if i%stride==0 or i==n-1:
                label=datetime.fromisoformat(day).strftime('%H:%M') if self.period=='hour' else day[5:].replace('-','/')
                self.create_text(x,bottom+17,text=label,fill=MUTED,font=('TkDefaultFont',9))
            self.hitboxes.append((x-step/2,x+step/2,day,stats))
        if not any(r['requests'] for _,r in self.rows):
            self.create_text((left+right)/2,(top+bottom)/2,text='此范围尚无已记录请求（不代表未使用）',fill=MUTED)

    def _heatmap(self):
        if not self.rows:
            if int(float(self.cget('height'))) != 188:self.configure(height=188)
            self.create_text(max(360,self.winfo_width())/2,90,text='尚无可用的逐请求记录',fill=MUTED)
            return
        records={date.fromisoformat(key):stats for key,stats in self.rows}
        first=min(records)
        last=max(max(records),getattr(self,'today',datetime.now(timezone.utc).astimezone().date()))
        last_monday=last-timedelta(days=last.weekday())
        first_monday=min(first-timedelta(days=first.weekday()),last_monday-timedelta(weeks=52))
        weeks=(last_monday-first_monday).days//7+1
        sections=(weeks+52)//53
        section_height=188
        visible_sections=sections if self.history_expanded else 1
        height=visible_sections*section_height+16
        if int(float(self.cget('height'))) != height:self.configure(height=height)
        width=max(360,self.winfo_width())
        left,right=62,width-18
        pitch=(right-left)/53
        size=max(3,min(14,pitch-2))
        values=[float(s[self.metric]) for s in records.values() if s[self.metric] is not None]
        maximum=max(values+[0])
        colors=('#ECEEF2','#B8E1EA','#6BC2D4','#1797B4','#116D86')
        for section in range(visible_sections):
            end_index=weeks-section*53
            start_index=max(0,end_index-53)
            start_day=first_monday+timedelta(weeks=start_index)
            end_day=min(last,first_monday+timedelta(weeks=end_index)-timedelta(days=1))
            top=section*section_height+10
            self.create_text(left,top,text=f'{start_day.isoformat()}  —  {end_day.isoformat()}',anchor='nw',fill=INK,font=('TkDefaultFont',9,'bold'))
            for day,label in ((0,'周一'),(2,'周三'),(4,'周五'),(6,'周日')):
                self.create_text(left-7,top+35+day*(size+3),text=label,anchor='e',fill=MUTED,font=('TkDefaultFont',8))
            month_seen=set()
            for week in range(start_index,end_index):
                monday=first_monday+timedelta(weeks=week)
                x=left+(week-start_index)*pitch
                month=(monday.year,monday.month)
                if month not in month_seen:
                    month_seen.add(month)
                    self.create_text(x,top+21,text=f'{monday.month}月',anchor='w',fill=MUTED,font=('TkDefaultFont',8))
                for day in range(7):
                    current=monday+timedelta(days=day)
                    if current>last:continue
                    stats=records.get(current)
                    value=stats.get(self.metric) if stats else None
                    level=0 if value is None or value<=0 or not maximum else min(4,max(1,1+int(float(value)/maximum*3)))
                    y=top+29+day*(size+3)
                    unknown=bool(stats and stats['requests'] and value is None)
                    radius=min(3,size/3)
                    self.create_polygon(x+radius,y,x+size-radius,y,x+size,y,x+size,y+radius,
                                        x+size,y+size-radius,x+size,y+size,x+size-radius,y+size,
                                        x+radius,y+size,x,y+size,x,y+size-radius,x,y+radius,x,y,
                                        smooth=True,splinesteps=6,outline=ORANGE if unknown else '',
                                        fill='#FFF2DB' if unknown else colors[level])
                    if stats:self.hitboxes.append((x,x+size,current.isoformat(),stats,y,y+size))
            for i,color in enumerate(colors):
                self.create_rectangle(right-75+i*14,top+165,right-65+i*14,top+175,outline='',fill=color)
            self.create_text(right-80,top+170,text='少',anchor='e',fill=MUTED,font=('TkDefaultFont',8))
            self.create_text(right-1,top+170,text='多',anchor='w',fill=MUTED,font=('TkDefaultFont',8))

    def _hide_tip(self):
        self.delete('tip')
        self.hover=None
        self.configure(cursor='')

    def motion(self,event):
        target=next((r for r in self.hitboxes if r[0]<=event.x<r[1] and (len(r)==4 or r[4]<=event.y<r[5])),None)
        if target==self.hover:
            return
        self._hide_tip()
        if not target:
            return
        self.hover=target
        self.configure(cursor='hand2' if self.period in ('day','heatmap') else '')
        _,_,day,s=target[:4]
        current = (money(s['cost'],4) if self.metric=='cost' else
                   ('—' if s['cache_hit'] is None else f"{s['cache_hit']:.2f}%") if self.metric=='cache_hit' else
                   short_number(s[self.metric]))
        unit = {'cost':'成本','tokens':'Tokens','requests':'请求数','cache_hit':'缓存命中率'}[self.metric]
        msg=f"{day}  ·  {unit} {current}  ·  {s['requests']} 条请求" + (f" / {s['unpriced']} 条未计价" if s['unpriced'] else '')
        width=min(420,max(100,self.winfo_width()-16))
        x=max(8,min(event.x-width/2,self.winfo_width()-width-8))
        text_id=self.create_text(x+8,6,text=msg,width=width-16,anchor='nw',fill=INK,font=('TkDefaultFont',9),tags='tip')
        bounds=self.bbox(text_id)
        box=self.create_rectangle(x,2,x+width,(bounds[3] if bounds else 20)+5,fill='#EAF3FF',outline='',tags='tip')
        self.tag_lower(box,text_id)

    def click(self,event):
        target=next((r for r in self.hitboxes if r[0]<=event.x<r[1] and (len(r)==4 or r[4]<=event.y<r[5])),None)
        if target and self.period in ('day','heatmap'):
            self.on_day(target[2])


class BillingPanel(ttk.Frame):
    def __init__(self,parent,app):
        super().__init__(parent,style='App.TFrame')
        self.app=app
        self.directory=Path(app.data_root)/'billing'
        self.price_store=PriceStore(self.directory)
        self.prices=default_prices()
        self.requests=[]
        self.result=None
        self.filtered=[]
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='qingxu-billing')
        self.stop=threading.Event()
        self.future=None
        self.progress=SimpleQueue()
        self.poll_job=None
        self.closed=False
        self.source=None
        self.active=False
        self.page=0
        self.page_size=100
        self.sort_keys={}
        self.row_objects={}
        self.metric='cost'
        self.metric_choice=tk.StringVar(self,value='cost')
        self.quote_cache={}
        self.today_day=datetime.now(timezone.utc).astimezone().date()
        self.today_job=None
        self.period=tk.StringVar(self,value='近 7 天')
        self.provider=tk.StringVar(self,value='全部供应商')
        self.model=tk.StringVar(self,value='全部模型')
        self.scope=tk.StringVar(self,value='全部项目')
        self.tz=tk.StringVar(self,value='本机时区')
        self.cost_mode=tk.StringVar(self,value='当前价表估算')
        today=date.today()
        self.start=tk.StringVar(self,value=(today-timedelta(days=6)).isoformat())
        self.end=tk.StringVar(self,value=today.isoformat())
        self.start_time=tk.StringVar(self,value='00:00')
        self.end_time=tk.StringVar(self,value='23:59')
        self.follow_now=tk.BooleanVar(self,value=False)
        self.sync_text=tk.StringVar(self,value='尚未同步')
        self.notice=tk.StringVar(self,value='首次打开会索引本机逐请求记录；不调用模型，不读取登录凭据。')
        self.detail_text=tk.StringVar(self,value='双击模型/供应商可筛选；双击请求查看分解。* = 部分计价；— = 未知。')
        self._build()
        try:
            self.prices=self.price_store.load()
        except (ValueError,OSError) as exc:
            self.prices={**default_prices(),'rates':[]}
            self.notice.set('价格文件不可读，暂停估算：'+str(exc))
        self.bind('<Destroy>',self._destroyed,add='+')
        self._apply_period()
        if hasattr(self.app,'set_today_cost'):
            self.today_job=self.after(60_000,self._check_today)

    def _build(self):
        install_controls(self)
        header=ttk.Frame(self,style='App.TFrame',padding=(0,0,0,18))
        self.header=header
        header.pack(fill='x')
        heading=ttk.Frame(header,style='App.TFrame')
        heading.pack(side='left',fill='x',expand=True)
        self.heading_title=ttk.Label(heading,text='用量与费用',style='Title.TLabel')
        self.heading_title.pack(anchor='w')
        self.heading_subtitle=ttk.Label(heading,text='每一次请求，都有清楚的来处。',style='Muted.TLabel')
        self.heading_subtitle.pack(anchor='w',pady=(4,0))
        self.refresh_button=ttk.Button(header,text='同步用量',command=self.refresh,style='Primary.TButton')
        self.refresh_button.pack(side='right',padx=(6,0))
        self.diagnostics_button=ttk.Button(header,text='数据来源',command=self.show_diagnostics,style='Ghost.TButton')
        self.diagnostics_button.pack(side='right')
        self.notice.trace_add('write',self._update_diagnostics_indicator)
        self.sync_label=ttk.Label(header,textvariable=self.sync_text,style='Muted.TLabel')
        self.sync_label.pack(side='right',padx=8)
        filters=ttk.Frame(self,padding=(0,0,0,12))
        filters.pack(fill='x')
        self.filters=filters
        self._filters_in_canvas=False
        selectors=ttk.Frame(filters)
        selectors.pack(fill='x')
        selector_controls=[]
        for var,values in ((self.scope,('全部项目','当前项目','当前会话')),
                           (self.provider,('全部供应商',)),(self.model,('全部模型',))):
            box=ChoiceButton(selectors,textvariable=var,values=values,state='readonly')
            selector_controls.append(box)
            box.bind('<<ComboboxSelected>>',lambda e,v=var:self.changed(v))
            if var is self.provider:self.provider_box=box
            if var is self.model:self.model_box=box
        self.period_button=DateRangeButton(selectors,presets=PERIODS,period=self.period,
                    start=self.start,end=self.end,start_time=self.start_time,end_time=self.end_time,
                    follow_now=self.follow_now,timezone_label=self.tz,
                    on_apply=lambda:self.changed(self.period),on_error=self._date_error)
        selector_controls.append(self.period_button)
        self.advanced_button=ttk.Button(selectors,text='筛选设置',command=self.toggle_advanced,style='Ghost.TButton')
        selector_controls.append(self.advanced_button)
        dates=ttk.Frame(filters)
        self.extra_filters=dates
        self.advanced_visible=False
        zone=ChoiceButton(dates,textvariable=self.tz,values=('本机时区','UTC+08:00','UTC+09:00','UTC'),state='readonly',width=9)
        zone.bind('<<ComboboxSelected>>',lambda _:self.changed(self.period))
        mode=ChoiceButton(dates,textvariable=self.cost_mode,values=('当前价表估算','来源报告金额'),state='readonly',width=12)
        mode.bind('<<ComboboxSelected>>',lambda _:self.apply_filters())
        apply_button=ttk.Button(dates,text='应用',command=self.apply_filters,style='Outline.TButton')
        price_button=ttk.Button(dates,text='单价设置',command=self.edit_prices,style='Outline.TButton')
        date_controls=(zone,mode,apply_button,price_button)
        for frame,controls in ((selectors,selector_controls),(dates,date_controls)):
            for column,control in enumerate(controls):
                control.grid(row=0,column=column,sticky='w',padx=(0,6),pady=1)
            frame.bind('<Configure>',lambda event,items=controls:self._layout_filters(event,items))
        # A vertical canvas gives 980x680/DPI users all four KPIs and the full table.
        viewport=ttk.Frame(self)
        viewport.pack(fill='both',expand=True)
        self.viewport=viewport
        self.canvas=tk.Canvas(viewport,background=BG,highlightthickness=0)
        scroll=ttk.Scrollbar(viewport,orient='vertical',command=self.canvas.yview,style='Slim.Vertical.TScrollbar')
        scroll.pack(side='right',fill='y')
        self.canvas.pack(side='left',fill='both',expand=True)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.body=ttk.Frame(self.canvas,style='App.TFrame',padding=(2,0,8,6))
        window=self.canvas.create_window(0,0,anchor='nw',window=self.body)
        self.body.bind('<Configure>',lambda _:self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>',lambda e:self.canvas.itemconfigure(window,width=e.width))
        hero=ttk.Frame(self.body,style='App.TFrame')
        hero.pack(fill='x')
        self.hero=hero
        summary_row=ttk.Frame(hero,style='App.TFrame')
        summary_row.pack(fill='x')
        cards=ttk.Frame(summary_row,style='App.TFrame')
        cards.pack(side='left',fill='x',expand=True)
        self.cards=cards
        self.kpi_cards=[]
        self._kpi_layout=None
        self.kpis={}
        labels=(('cost','估算成本'),('requests','总请求数'),('tokens','消耗 Tokens'),('cache_hit','缓存命中率'))
        for i,(key,caption) in enumerate(labels):
            card=RoundedCard(cards,padding=(14,14))
            card.grid(row=0,column=i,sticky='nsew',padx=(0,8),pady=(0,8))
            self.kpi_cards.append(card)
            cell=card.content
            cell.configure(takefocus=True)
            if key!='cache_hit':
                cell.bind('<Return>',lambda _,k=key:self.select_metric(k))
                cell.bind('<space>',lambda _,k=key:self.select_metric(k))
            cards.columnconfigure(i,weight=1,uniform='kpi')
            title=ttk.Label(cell,text=caption,style='CardMuted.TLabel')
            title.pack(anchor='w')
            value=ttk.Label(cell,text='—',style='Card.TLabel',font=(getattr(self.app,'font_family','TkDefaultFont'),25,'bold'))
            value.pack(anchor='w',pady=(8,2))
            note=ttk.Label(cell,text='尚无数据',style='CardMuted.TLabel',wraplength=185)
            # Keep the semantic detail available without growing the default cards.
            bar=tk.Frame(cell,height=2,background=BLUE if key==self.metric else CARD)
            bar.pack(fill='x',pady=(3,0))
            if key!='cache_hit':
                for w in (cell,title,value,note,bar):
                    w.bind('<Button-1>',lambda _,k=key:self.select_metric(k))
                    w.configure(cursor='hand2')
            self.kpis[key]=(title,value,note,bar)
        cards.bind('<Configure>',self._layout_kpis)
        self.more_button=DropButton(summary_row,text='更多指标',command=self.toggle_more_metrics)
        self.more_button.pack(side='right',anchor='n',before=cards)
        self.more_button.bind('<FocusIn>',self._show_body_focus,add='+')
        summary_row.bind('<Configure>',self._layout_more_button)
        self.more_metrics=ttk.Frame(hero,style='App.TFrame')
        self.more_metrics_visible=False
        self.more_metric_cards=[]
        self.more_metric_values={}
        self._more_metric_columns=None
        for index,(key,caption) in enumerate((('new_input','新增输入'),('output','输出'),
                                               ('cache_write','缓存写入'),('cache_read','缓存命中（读取）'))):
            card=ttk.Frame(self.more_metrics,style='Card.TFrame',padding=(10,7))
            card.grid(row=0,column=index,sticky='nsew',padx=(0,8),pady=(0,8))
            ttk.Label(card,text=caption,style='CardMuted.TLabel').pack(anchor='w')
            value=ttk.Label(card,text='—',style='Card.TLabel',font=(getattr(self.app,'font_family','TkDefaultFont'),15,'bold'))
            value.pack(anchor='w',pady=(4,2))
            note=ttk.Label(card,text='无可用样本',style='CardMuted.TLabel',wraplength=140,justify='left')
            note.pack(anchor='w')
            self.more_metric_cards.append(card)
            self.more_metric_values[key]=(value,note)
        self.more_metrics.bind('<Configure>',self._layout_more_metrics)
        chart_card=RoundedCard(hero,padding=(18,12))
        chart_card.pack(fill='x',pady=(6,12))
        self.chart_card=chart_card
        chart_header=ttk.Frame(chart_card.content,style='Card.TFrame')
        chart_header.pack(fill='x')
        self.chart_title=ttk.Label(chart_header,text='使用趋势 · 近 7 天',style='Card.TLabel')
        self.chart_title.pack(side='left',anchor='w')
        self.history_button=ttk.Button(chart_header,text='更早历史',command=self.toggle_history,style='Ghost.TButton')
        metric_bar=ttk.Frame(chart_header,style='Card.TFrame')
        metric_bar.pack(side='right')
        self.metric_buttons={}
        metric_style=ttk.Style(self)
        metric_style.configure('Metric.Toolbutton',padding=(6,3))
        for key,label in (('tokens','Tokens'),('requests','请求'),('cost','成本')):
            button=ttk.Radiobutton(metric_bar,text=label,value=key,variable=self.metric_choice,style='Metric.Toolbutton',
                                   command=lambda k=key:self.select_metric(k))
            button.pack(side='left',padx=1)
            button.bind('<FocusIn>',self._show_body_focus,add='+')
            self.metric_buttons[key]=button
        self.chart=DailyChart(chart_card.content,self.select_day)
        self.chart.pack(fill='x',expand=True,pady=(2,0))
        self.tables=ttk.Notebook(self.body,style='Line.TNotebook')
        self.tables.pack(fill='both',expand=True,pady=(3,0))
        self.tables.enable_traversal()
        self.trees={}
        self.tab_frames={}
        self.labels={'request':'请求日志','provider':'供应商','model':'模型','project':'项目','session':'会话'}
        columns={'name':('名称',200),'requests':('请求数',72),'cost':('成本 USD',102),'tokens':('Tokens',98),
                 'success':('成功率¹',86),'speed':('速度²',88),'ttft':('首字²',86),'coverage':('计价覆盖',92)}
        face=font.nametofont("TkDefaultFont",root=self)
        date_width=face.measure("88-88 88:88:88")+14
        headings=(('time',('时间',date_width)),('model',('模型',162)),('input',('输入',76)),
                  ('output',('输出',76)),('cached',('缓存命中',102)),('cost',('估算价格',106)))
        for key in self.labels:
            pane=ttk.Frame(self.tables,style='Card.TFrame',padding=(0,0,0,0))
            self.tables.add(pane,text=self.labels[key])
            self.tab_frames[key]=pane
            cols=headings if key=='request' else tuple(columns.items())
            tree=ttk.Treeview(pane,columns=[c for c,_ in cols],show='headings',height=9,selectmode='browse',style='Usage.Treeview')
            for col,(caption,width) in cols:
                anchor='w' if col in ('name','time','model') else 'e'
                tree.heading(col,text=caption,anchor=anchor,command=lambda k=key,c=col:self.sort_table(k,c))
                tree.column(col,width=max(width,face.measure(caption)+16),minwidth=64,
                            stretch=col in ('name','model'),anchor=anchor)
            ys=ttk.Scrollbar(pane,orient='vertical',command=tree.yview,style='Slim.Vertical.TScrollbar')
            xs=ttk.Scrollbar(pane,orient='horizontal',command=tree.xview,style='Slim.Horizontal.TScrollbar')
            tree.configure(yscrollcommand=ys.set,xscrollcommand=xs.set)
            ys.pack(side='right',fill='y')
            xs.pack(side='bottom',fill='x')
            tree.pack(fill='both',expand=True)
            tree.bind('<Double-1>',lambda _,k=key:self.drill(k))
            tree.bind('<Return>',lambda _,k=key:self.drill(k))
            self.trees[key]=tree
        ttk.Style(self).configure('Usage.Treeview',rowheight=face.metrics('linespace')+16)
        self.tables.select(self.tab_frames['request'])
        self.tables.bind('<<NotebookTabChanged>>',self._table_changed,add='+')
        footer=ttk.Frame(self,style='App.TFrame',padding=(0,8,0,0))
        self.table_footer=footer
        footer.pack(side='bottom',fill='x',before=self.viewport)
        ttk.Button(footer,text='导入请求…',command=self.import_requests).pack(side='left')
        ttk.Button(footer,text='导出当前范围 CSV…',command=self.export).pack(side='left',padx=6)
        ttk.Button(footer,text='重置筛选',command=self.reset).pack(side='left')
        ttk.Button(footer,text='下一页',command=lambda:self.turn_page(1)).pack(side='right')
        self.page_label=ttk.Label(footer,text='请求 0 / 0',style='Muted.TLabel')
        self.page_label.pack(side='right',padx=8)
        ttk.Button(footer,text='上一页',command=lambda:self.turn_page(-1)).pack(side='right')
        ttk.Label(self.body,textvariable=self.detail_text,style='Muted.TLabel',wraplength=850).pack(fill='x',pady=(6,0))
        self._wheel_bindings=[]
        for seq in ('<MouseWheel>','<Button-4>','<Button-5>'):
            self._wheel_bindings.append((seq,self.app.root.bind(seq,self._wheel,add='+')))
        for control in (*selector_controls,zone,mode,apply_button,price_button):
            control.bind('<FocusIn>',self._show_filter_focus,add='+')
        self.bind('<Configure>',self._fit_filters,add='+')
        filters.bind('<Configure>',self._fit_filters,add='+')

    def _date_error(self,error):
        self.notice.set('筛选未应用：'+str(error))
        messagebox.showerror('日期范围无效',str(error),parent=self.app.root)

    def _update_diagnostics_indicator(self,*_):
        text=self.notice.get()
        warning=any(word in text for word in ('失败','未应用','无法','不可读','损坏','不完整'))
        warning=warning or bool(getattr(self,'stats',{}).get('unpriced'))
        self.diagnostics_button.configure(text='数据来源 · 有提示' if warning else '数据来源')

    def show_diagnostics(self):
        notes='\n'.join(f'{title.cget("text")}：{note.cget("text")}'
                        for title,_,note,_ in self.kpis.values())
        messagebox.showinfo('数据来源与统计说明',self.notice.get()+'\n\n'+notes+'\n\n'+self.detail_text.get(),parent=self.app.root)

    def _layout_kpis(self,event):
        widths=tuple(card.content.winfo_reqwidth()+24 for card in self.kpi_cards)
        two=(max(widths[::2]),max(widths[1::2]))
        if sum(widths)<=event.width:
            minimum=widths
        elif sum(two)<=event.width:
            minimum=two
        else:
            minimum=(max(widths),)
        columns=len(minimum)
        uniform='kpi' if max(minimum)*columns<=event.width else ''
        layout=(minimum,uniform)
        if layout==self._kpi_layout:return
        self._kpi_layout=layout
        for col in range(4):
            self.cards.columnconfigure(col,weight=1 if col<columns else 0,
                                       minsize=minimum[col] if col<columns else 0,uniform=uniform if col<columns else '')
        for index,card in enumerate(self.kpi_cards):
            card.grid_configure(row=index//columns,column=index%columns)

    def _layout_more_button(self,event=None):
        face=font.nametofont("TkDefaultFont",root=self)
        width=event.width if event is not None else self.more_button.master.winfo_width()
        compact=width < face.measure("中"*35)
        caption=('收起' if compact else '收起指标') if self.more_metrics_visible else ('更多' if compact else '更多指标')
        if self.more_button.cget('text') != caption:
            self.more_button.configure(text=caption)

    def toggle_advanced(self,show=None):
        visible=not self.advanced_visible if show is None else bool(show)
        if visible==self.advanced_visible:return
        self.advanced_visible=visible
        if visible:self.extra_filters.pack(fill='x',pady=(5,0))
        else:self.extra_filters.pack_forget()

    def toggle_more_metrics(self):
        self.more_metrics_visible=not self.more_metrics_visible
        self._layout_more_button()
        if self.more_metrics_visible:self.more_metrics.pack(fill='x',before=self.chart_card)
        else:self.more_metrics.pack_forget()

    def _layout_more_metrics(self,event):
        required=max((card.winfo_reqwidth()+8 for card in self.more_metric_cards),default=140)
        columns=4 if event.width>=required*4 else 2 if event.width>=required*2 else 1
        if columns==self._more_metric_columns:return
        self._more_metric_columns=columns
        for col in range(4):
            self.more_metrics.columnconfigure(col,weight=1 if col<columns else 0,
                                              uniform='more' if col<columns else '')
        for index,card in enumerate(self.more_metric_cards):
            card.grid_configure(row=index//columns,column=index%columns)

    def _layout_filters(self,event,controls):
        frame=event.widget
        width=max(1,event.width-4)
        for columns in (len(controls),3,2,1):
            widths=[max(control.winfo_reqwidth()+7 for control in controls[column::columns])
                    for column in range(min(columns,len(controls)))]
            if sum(widths)<=width:
                break
        if getattr(frame,'_filter_columns',None)==columns:
            return
        frame._filter_columns=columns
        for col in range(len(controls)):
            frame.columnconfigure(col,weight=1 if frame is self.period_button.master and columns==len(controls) and col==0 else 0)
        for index,control in enumerate(controls):
            control.grid(row=index//columns,column=index%columns,sticky='w',padx=(0,7),pady=3)

    def _fit_filters(self,event):
        if event.widget not in (self,self.filters):
            return
        self._fit_density()
        reserve=60 if self._filters_in_canvas else 32
        needs_scroll=self.winfo_height()<self.header.winfo_reqheight()+self.filters.winfo_reqheight()+reserve
        if needs_scroll==self._filters_in_canvas:
            return
        self.filters.pack_forget()
        if needs_scroll:
            self.filters.pack(in_=self.body,fill='x',before=self.hero)
        else:
            self.filters.pack(in_=self,fill='x',before=self.viewport)
        self._filters_in_canvas=needs_scroll

    def _fit_density(self):
        # Airy on a normal desktop, compact when height or DPI is constrained.
        compact=self.winfo_width()<950 or self.winfo_height()<740
        if getattr(self,'_quiet_compact',None)!=compact:
            self._quiet_compact=compact
            family=getattr(self.app,'font_family','TkDefaultFont')
            self.heading_title.configure(font=(family,16 if compact else 23,'bold'))
            self.header.configure(padding=(0,0,0,8 if compact else 18))
            self.filters.configure(padding=(0,0,0,5 if compact else 12))
            if compact:self.heading_subtitle.pack_forget()
            else:self.heading_subtitle.pack(anchor='w',pady=(4,0))
            self.sync_label.pack_forget()
            if compact:self.sync_label.pack(side='bottom',anchor='w',pady=(4,0))
            else:self.sync_label.pack(side='right',padx=8)
            for card,(_,value,_,_) in zip(self.kpi_cards,self.kpis.values()):
                card.content.configure(padding=(8,4) if compact else (14,14))
                value.configure(font=(family,16 if compact else 25,'bold'))
                value.pack_configure(pady=(2,0) if compact else (8,2))
            self.chart_card.content.configure(padding=(10,4) if compact else (18,12))
            self.chart.compact=compact
            self.chart.draw()
            self._kpi_layout=None
        scroll_footer=self.winfo_height()<480
        if getattr(self,'_footer_in_canvas',None)!=scroll_footer:
            self._footer_in_canvas=scroll_footer
            self.table_footer.pack_forget()
            if scroll_footer:self.table_footer.pack(in_=self.body,fill='x',pady=(7,0))
            else:self.table_footer.pack(in_=self,side='bottom',fill='x',before=self.viewport)

    def _show_filter_focus(self,event):
        if not self._filters_in_canvas:
            return
        self._show_body_focus(event)

    def _show_body_focus(self,event):
        offset=event.widget.winfo_rooty()-self.canvas.winfo_rooty()
        bottom=offset+event.widget.winfo_height()
        if offset<0 or bottom>self.canvas.winfo_height():
            shift=offset if offset<0 else bottom-self.canvas.winfo_height()
            position=self.canvas.canvasy(0)+shift
            self.canvas.yview_moveto(position/max(1,self.body.winfo_height()))

    def _wheel(self,event):
        inside_body=str(event.widget).startswith(str(self.body))
        inside_filters=self._filters_in_canvas and str(event.widget).startswith(str(self.filters))
        if not (inside_body or inside_filters) or isinstance(event.widget,(ttk.Treeview,ttk.Combobox,tk.Text)):
            return
        if self.canvas.yview()==(0.0,1.0):return
        delta=getattr(event,'delta',0)
        direction=-1 if delta>0 or getattr(event,'num',0)==4 else 1
        self.canvas.yview_scroll(direction*2,'units')

    def activate(self):
        if self.closed:return
        if not self.active:
            self.active=True
            self.refresh()

    def source_changed(self):
        # Imported ledger is scoped by source directory too, preventing bleed.
        self.requests=[]
        self.result=None
        self.source=None
        self.active=False
        self.quote_cache.clear()
        if hasattr(self.app,'set_today_cost'):self.app.set_today_cost('今日 —')
        self.stop.set()
        self.notice.set('数据来源已变化，请重新同步；未沿用旧来源金额。')
        self.apply_filters()

    def selection_changed(self):
        if self.scope.get()!='全部项目':
            self.apply_filters()

    def _source(self):
        database=Path(self.app.relay.settings['chat_db'])
        home=Path(self.app.prefs.get('codex_home') or database.parent)
        return str(home.expanduser().absolute()),str(database)

    def _index_directory(self,home):
        import hashlib
        return self.directory/('source-'+hashlib.sha256(home.encode()).hexdigest()[:16])

    def refresh(self,import_path=None):
        if self.closed or self.future is not None:
            return
        home,db=self._source()
        directory=self._index_directory(home)
        index_factory=BillingIndex
        self.stop=threading.Event()
        stop=self.stop
        progress=self.progress=SimpleQueue()
        self.refresh_button.configure(text='同步中…',state='disabled')
        self.sync_text.set('正在增量读取本机数值记录…')
        def work():
            index=index_factory(directory)
            imported=index.import_file(import_path) if import_path else None
            total_bytes=batches=0
            try:
                while True:
                    if stop.is_set():raise InterruptedError('已停止索引')
                    result=index.scan(home,db,stop=stop,include_requests=False)
                    issues=dict(result['issues'])
                    read=issues.get('bytes_read',0)
                    total_bytes+=read
                    batches+=1
                    progress.put((batches,total_bytes))
                    if not issues.get('resume_required'):break
                    if not read:
                        issues['continuation_stalled']=1
                        break
                if stop.is_set():raise InterruptedError('已停止索引')
                issues['bytes_read']=total_bytes
                issues['sync_batches']=batches
                result=index.load(home,db,issues)
            except ValueError:
                if import_path:
                    result=index.load(home,db,{'source_unavailable':1})
                else:
                    raise
            result['imported']=imported
            return result
        self.future=self.executor.submit(work)
        self.source=home
        self.poll_job=self.after(80,self._poll)

    def _poll(self):
        self.poll_job=None
        if self.closed or self.future is None:return
        latest=None
        while True:
            try:latest=self.progress.get_nowait()
            except Empty:break
        if latest and not self.stop.is_set():
            batches,total_bytes=latest
            self.sync_text.set(f'正在同步 · {batches} 批 / 已读取 {total_bytes/1048576:.1f} MiB…')
        if not self.future.done():
            self.poll_job=self.after(80,self._poll)
            return
        future,self.future=self.future,None
        self.refresh_button.configure(text='同步用量',state='normal')
        if self.stop.is_set() or self.source!=self._source()[0]:
            self.sync_text.set('已丢弃旧数据来源的读取结果，请重新同步')
            if self.active:self.refresh()
            return
        try:
            result=future.result()
            self.set_result(result)
            batches=result.get('issues',{}).get('sync_batches',0)
            suffix=f' · {batches} 批' if batches else ''
            self.sync_text.set('最后同步 '+datetime.now().strftime('%H:%M:%S')+suffix)
        except Exception as exc:
            self.sync_text.set('同步失败；显示的仅为上次结果')
            self.notice.set('同步失败：'+str(exc)+'。不会用 0 代替未知费用。')

    def set_result(self,result):
        self.result=result
        self.requests=list(result['requests'])
        self.quote_cache.clear()
        providers=sorted({r.provider for r in self.requests})
        models=sorted({r.model for r in self.requests})
        self.provider_box.configure(values=('全部供应商',*providers))
        self.model_box.configure(values=('全部模型',*models))
        if self.provider.get() not in ('全部供应商',*providers):self.provider.set('全部供应商')
        if self.model.get() not in ('全部模型',*models):self.model.set('全部模型')
        self.page=0
        self.apply_filters()
        self._refresh_today_cost()

    def _refresh_today_cost(self):
        if not hasattr(self.app,'set_today_cost'):return
        self.today_day=datetime.now(timezone.utc).astimezone().date()
        start,end=period_bounds('今天',datetime.now(timezone.utc),'本机时区')
        rows=filter_requests(self.requests,start_at=start,end_at=end,tz_label='本机时区')
        data=summary(rows,self.prices,mode='estimate',quote_cache=self.quote_cache)
        amount=money(data['cost'])
        self.app.set_today_cost('今日 '+amount+('*' if data['cost'] is not None and data['unpriced'] else ''))

    def _check_today(self):
        self.today_job=None
        if self.closed:return
        if datetime.now(timezone.utc).astimezone().date()!=self.today_day:
            self._refresh_today_cost()
        self.today_job=self.after(60_000,self._check_today)

    def _apply_period(self):
        now=datetime.now(timezone.utc)
        kind=self.period.get()
        if kind not in ('自定义','全部'):
            start,end=period_bounds(kind,now,self.tz.get())
            zone=fixed_timezone(self.tz.get())
            self.start.set(start.astimezone(zone).date().isoformat())
            self.end.set(end.astimezone(zone).date().isoformat())
    def changed(self,var):
        if var is self.period:self._apply_period()
        self.page=0
        self.apply_filters()

    def apply_filters(self):
        try:
            now=datetime.now(timezone.utc)
            kind=self.period.get()
            custom=kind=='自定义'
            if custom:
                start,end,start_at,end_at=custom_bounds(
                    self.start.get(),self.end.get(),self.start_time.get(),self.end_time.get(),
                    now,self.tz.get(),self.follow_now.get())
            else:
                start=end=None
                start_at,end_at=period_bounds(kind,now,self.tz.get())
            cwd=thread=project_id=''
            if self.scope.get()!='全部项目':
                record=getattr(self.app,'chat',None)
                if not record:raise ValueError('先在顶部选择项目和聊天，或使用“全部项目”')
                if self.scope.get()=='当前项目':
                    project_id=str(record.get('app_project_id') or '')
                    if not project_id:cwd=record.get('path','')
                else:thread=record.get('chat_id','')
                if not (cwd or thread or project_id):raise ValueError('当前选择缺少可核对的项目/会话身份')
            self.filtered=filter_requests(self.requests,start=start,end=end,start_at=start_at,end_at=end_at,tz_label=self.tz.get(),
                    provider='' if self.provider.get()=='全部供应商' else self.provider.get(),
                    model='' if self.model.get()=='全部模型' else self.model.get(),cwd=cwd,thread_id=thread,
                    app_project_id=project_id,unassigned_only=bool(cwd))
            mode=self.mode
            data=summary(self.filtered,self.prices,mode=mode,quote_cache=self.quote_cache)
            self.stats=data
            if custom:
                self.date_rows,self.chart_period=daily(self.filtered,start,end,self.tz.get(),self.prices,mode=mode,quote_cache=self.quote_cache)
            else:
                self.date_rows,self.chart_period=period_buckets(self.filtered,kind,now,self.tz.get(),self.prices,mode=mode,quote_cache=self.quote_cache)
                if kind=='全部':self.chart_period='heatmap'
            self.chart.today=now.astimezone(fixed_timezone(self.tz.get())).date()
        except (ValueError,TypeError,OverflowError) as exc:
            self.notice.set('筛选未应用：'+str(exc));return
        self.page=min(self.page,max(0,(len(self.filtered)-1)//self.page_size))
        self._render_kpis(data)
        self.select_metric(self.metric)
        self.render_tables()
        issues=(self.result or {}).get('issues',{})
        excluded_keys = ('conflicts_excluded', 'invalid_requests', 'invalid_records', 'unsafe_files',
                         'oversize_requests', 'read_errors', 'pending_files', 'incomplete_files',
                         'compressed_files_unsupported', 'compressed_read_errors', 'compressed_unavailable',
                         'continuation_stalled', 'source_unavailable', 'file_limit_reached')
        excluded=sum(issues.get(key, 0) for key in excluded_keys)
        detail=f"{data['priced']:,}/{data['requests']:,} 条可计价；{data['unpriced']:,} 条价格或用量未知。"
        if self.mode=='estimate':
            detail+='按本地价表重算的 Token 估算，不是订阅或供应商账单；不含工具、税费。'
            if data['assumed']:detail+=f" {data['assumed']} 条含 Standard/30m/地域等假设（双击查看）。"
        else:
            detail+='仅累加来源显式报告的 USD，不用估算补缺；未经账单对账。'
        if issues.get('duplicates'):detail+=f" 已去重 {issues['duplicates']} 条。"
        if issues.get('files_without_requests'):detail+=f" {issues['files_without_requests']} 个日志无逐请求记录，未以累计/快照代替。"
        if excluded:detail+=f' 索引有 {excluded} 项未纳入或待继续同步，范围可能不完整。'
        if issues.get('compressed_unavailable'):
            detail+=f" {issues['compressed_unavailable']} 个压缩归档无法读取：Python 3.11–3.13 需安装 backports.zstd 后重新同步。"
        if issues.get('compressed_read_errors'):
            detail+=f" {issues['compressed_read_errors']} 个压缩归档损坏或截断，未纳入统计。"
        if issues.get('files_seen') is not None:detail+=f" 本轮检查 {issues['files_seen']} 个日志 / 读取 {issues.get('bytes_read',0)/1048576:.1f} MiB。"
        if (self.result or {}).get('mapping_note'):detail+=' 项目映射受限。'
        if project_id and any(not r.app_project_id for r in self.requests):detail+=' 项目归属未确认的记录未纳入当前项目。'
        if cwd:detail+=' 当前项目按所选工作目录筛选未归属记录。'
        if (self.result or {}).get('synthetic'):detail='合成演示数据 · '+detail
        self.notice.set(detail)

    @property
    def mode(self):
        return 'estimate' if self.cost_mode.get()=='当前价表估算' else 'reported'

    def _render_kpis(self,s):
        self.kpis['cost'][0].configure(text=('部分估算' if s['unpriced'] else '估算成本') if self.mode=='estimate' else '来源金额')
        amounts={'cost':money(s['cost']), 'requests':f"{s['requests']:,}", 'tokens':short_number(s['tokens']),
                 'cache_hit':'—' if s['cache_hit'] is None else f"{s['cache_hit']:.1f}%"}
        notes={'cost':f"{s['priced']:,}/{s['requests']:,} 条已计价" + (' · 部分' if s['unpriced'] else ''),
               'requests':'请求记录，不是消息数', 'tokens':'输入 + 输出 · 含缓存',
               'cache_hit':f"缓存读取 / 已知输入 · {s['cache_known']}条"}
        for key,(_,value,note,_) in self.kpis.items():
            value.configure(text=amounts[key]); note.configure(text=notes[key])
        total=s['requests']
        for key,(value,note) in self.more_metric_values.items():
            count=s['cache_known'] if key in ('new_input','cache_read') else s['usage_known']
            amount=s.get(key)
            value.configure(text=('至少 '+f'{amount:,}' if key=='cache_write' and count and amount is not None
                                  else short_number(amount) if count else '—'))
            coverage=(f'无可用样本 · 0/{total}' if not count else
                      (f'部分 {count}/{total}' if count<total else f'{count}/{total} 条已知'))
            if key=='new_input' and count:
                coverage='按已记录拆分，写入缺失可能计入输入 · '+('部分 ' if count<total else '')+f'{count}/{total} 条缓存读取可用'
            if key=='cache_write' and count:
                coverage='可能缺失 · '+('部分 ' if count<total else '')+f'{count}/{total} 条用量可读'
            note.configure(text=coverage)

    def toggle_history(self):
        self.chart.history_expanded=not self.chart.history_expanded
        self.history_button.configure(text='收起历史' if self.chart.history_expanded else '更早历史')
        self.chart.draw()

    def select_metric(self,key):
        self.metric=key
        self.metric_choice.set(key)
        for k,(_,_,_,bar) in self.kpis.items():bar.configure(background=BLUE if k==key else CARD)
        title='用量热力图' if self.period.get()=='全部' else '使用趋势 · '+self.period.get()
        self.chart_title.configure(text=title)
        rows=getattr(self,'date_rows',[])
        today=datetime.now(timezone.utc).astimezone(fixed_timezone(self.tz.get())).date()
        show_history=False
        if self.period.get()=='全部' and rows:
            first=date.fromisoformat(rows[0][0])
            history_weeks=((today-timedelta(days=today.weekday()))-(first-timedelta(days=first.weekday()))).days//7+1
            show_history=history_weeks>53
        if show_history and not self.history_button.winfo_manager():
            self.history_button.pack(side='right',padx=3)
        elif not show_history and self.history_button.winfo_manager():
            self.history_button.pack_forget()
            self.chart.history_expanded=False
            self.history_button.configure(text='更早历史')
        self.chart.render(getattr(self,'date_rows',[]),key,getattr(self,'chart_period','day'))

    def select_day(self,day):
        self.period.set('自定义');self._apply_period()
        self.start.set(day);self.end.set(day);self.start_time.set('00:00');self.end_time.set('23:59');self.follow_now.set(False);self.page=0
        self.apply_filters();self.tables.select(self.tab_frames['request'])

    def _table_changed(self,_event=None):
        if self.result is not None:self.render_tables()

    def _quote(self,request):
        key=id(request)
        if key not in self.quote_cache:self.quote_cache[key]=quote(request,self.prices)
        return self.quote_cache[key]

    def render_tables(self):
        selected=self.tables.select()
        key=next((name for name,frame in self.tab_frames.items() if str(frame)==selected),None)
        if key is None:return
        tree=self.trees[key]
        if key=='request':
            tree.heading('cost',text='估算价格' if self.mode=='estimate' else '来源金额')
        tree.delete(*tree.get_children())
        objs=list(self.filtered) if key=='request' else group_requests(self.filtered,key,self.prices,self.mode,quote_cache=self.quote_cache)
        self.tables.tab(self.tab_frames[key],text=f'{self.labels[key]}  {len(objs):,}')
        column,desc=self.sort_keys.get(key,('time',True) if key=='request' else ('cost',True))
        def val(obj):
            if key=='request':
                if column=='cost':return self._quote(obj).amount if self.mode=='estimate' else Decimal(obj.reported_cost_usd) if obj.reported_cost_usd is not None else None
                if column=='time':return timestamp(obj.time)
                usage=obj.usage or {}
                return {'model':obj.model,'provider':obj.provider,'project':obj.project,
                        'input':usage.get('inputTokens'),'output':usage.get('outputTokens'),
                        'cached':usage.get('cacheReadTokens') if obj.cache_known else None,
                        'tokens':usage.get('totalTokens'),'status':obj.status}.get(column)
            ident,s=obj
            if column=='name':return str(ident)
            if column=='coverage':return s['priced']/max(1,s['requests'])
            return s.get(column)
        # Unknowns always last, regardless of sort direction.
        decorated=[(val(obj),obj) for obj in objs]
        known=[pair for pair in decorated if pair[0] is not None]
        unknown=[pair for pair in decorated if pair[0] is None]
        objs=[obj for _,obj in sorted(known,key=lambda pair:pair[0],reverse=desc)+unknown]
        if key=='request':objs=objs[self.page*self.page_size:(self.page+1)*self.page_size]
        mapping={}
        for i,obj in enumerate(objs):
            iid=f'{key}-{i}'
            mapping[iid]=obj
            if key=='request':
                r=obj; q=self._quote(r); u=r.usage or {}
                amount=q.amount if self.mode=='estimate' else Decimal(r.reported_cost_usd) if r.reported_cost_usd is not None else None
                values=(timestamp(r.time).astimezone(fixed_timezone(self.tz.get())).strftime('%m-%d %H:%M:%S'),r.model,
                        short_number(u.get('inputTokens')),short_number(u.get('outputTokens')),
                        short_number(u.get('cacheReadTokens') if r.cache_known else None),money(amount,4))
            else:
                ident,s=obj
                name=f'{ident[1]} · {ident[0]}' if key=='model' else ident[1] if key in ('project','session') else ident
                if key=='session':name+=f' · {ident[0][-8:]}'
                values=(name,f"{s['requests']:,}",money(s['cost'])+('*' if s['unpriced'] else ''),short_number(s['tokens']),
                        '—' if s['success'] is None else f"{s['success']:.1f}% ({s['status_known']})",
                        '—' if s['speed'] is None else f"≈{s['speed']:.1f} tok/s",
                        '—' if s['ttft'] is None else f"≈{s['ttft']/1000:.2f}s",f"{s['priced']}/{s['requests']}")
            tree.insert('', 'end',iid=iid,values=values)
        self.row_objects[key]=mapping
        total=len(self.filtered)
        self.page_label.configure(text=f'请求 {self.page+1 if total else 0} / {max(1,(total+99)//100) if total else 0} 页')

    def sort_table(self,key,column):
        previous=self.sort_keys.get(key)
        self.sort_keys[key]=(column,not previous[1] if previous and previous[0]==column else True)
        self.render_tables()

    def turn_page(self,delta):
        self.page=max(0,min(self.page+delta,max(0,(len(self.filtered)-1)//self.page_size)))
        self.tables.select(self.tab_frames['request']);self.render_tables()

    def drill(self,key):
        selected=self.trees[key].selection()
        if not selected:return
        obj=self.row_objects[key][selected[0]]
        if key=='request':self.request_detail(obj);return
        ident,_=obj
        if key=='model':self.provider.set(ident[0]);self.model.set(ident[1])
        elif key=='provider':self.provider.set(ident)
        elif key in ('project','session'):
            rows=[r for r in self.filtered if (r.thread_id==ident[0] if key=='session' else project_identity(r)==ident[0])]
            if rows:
                self.detail_text.set(f"{ident[1]}：{len(rows)} 条请求；可在顶部选择对应项目，再用当前项目/会话筛选。")
            return
        self.page=0;self.apply_filters();self.tables.select(self.tab_frames['request'])

    def request_detail(self,r):
        window=tk.Toplevel(self);window.title('请求计费明细');window.geometry('760x560');window.transient(self.app.root)
        set_window_icon(window)
        q=quote(r,self.prices);u=r.usage or {}
        lines=[f'模型：{r.model}    供应商：{r.provider}',f'项目：{r.project}',f'聊天：{r.title}  ({r.thread_id})',
               f'请求 ID：{r.response_id}',f'时间：{r.time}',f'来源：{r.origin}；模型归因：{r.model_source}',
               '',f'当前价表 Token 估算：{money(q.amount,8)}',f'来源报告金额：{money(Decimal(r.reported_cost_usd),8) if r.reported_cost_usd is not None else "未记录"}',
               '推理 Token 是输出子集，不会再次收费。']
        for label,key in (('非缓存输入','inputTokens'),('缓存读取','cacheReadTokens'),('缓存写入','cacheCreationTokens'),('输出（含推理）','outputTokens')):
            if key=='cacheCreationTokens' and r.cache_write_30m is not None:
                for lifetime, count in (('30m',r.cache_write_30m),('12h',r.cache_write_12h)):
                    name='缓存写入 '+lifetime
                    lines.append(f"{name}：{count} tokens  →  {money(q.components.get(name),8)}")
            else:
                lines.append(f"{label}：{u.get(key,'未记录')} tokens  →  {money(q.components.get(label),8)}")
        lines += [f"推理子集：{u.get('reasoningOutputTokens','未记录')}",f'单价版本：{q.rate_id or "未匹配"}',
                  '', '计费条件：'+('；'.join(q.notes) or '按记录中的条件'),
                  '成功、首字与速度均须有显式日志；不能用任务耗时代替请求耗时。',
                  '这里只估算 Token 费，不含工具、存储、税费、订阅费或供应商折扣。']
        ttk.Button(window,text='关闭',command=window.destroy).pack(side='bottom',pady=10)
        body=ttk.Frame(window);body.pack(fill='both',expand=True)
        box=tk.Text(body,wrap='word',relief='flat',padx=18,pady=18,background=CARD,font=('TkDefaultFont',10))
        bar=ttk.Scrollbar(body,orient='vertical',command=box.yview)
        bar.pack(side='right',fill='y');box.configure(yscrollcommand=bar.set)
        box.pack(fill='both',expand=True);box.insert('1.0','\n'.join(lines));box.configure(state='disabled')
        window.bind('<Escape>',lambda _:window.destroy());attach_motion_tree(window)

    def reset(self):
        self.period.set('近 7 天');self.provider.set('全部供应商');self.model.set('全部模型');self.scope.set('全部项目')
        self.start_time.set('00:00');self.end_time.set('23:59');self.follow_now.set(False)
        self._apply_period();self.page=0;self.apply_filters()

    def import_requests(self):
        if self.future is not None:return
        path=filedialog.askopenfilename(parent=self.app.root,title='导入标准请求数据（只保留数值和身份字段）',filetypes=[('请求文件','*.json *.jsonl')])
        if path:self.refresh(import_path=path)

    def export(self):
        if not self.filtered:
            self.detail_text.set('当前范围没有请求可导出。');return
        path=filedialog.asksaveasfilename(parent=self.app.root,title='导出筛选范围的全部请求',defaultextension='.csv',filetypes=[('CSV','*.csv')],initialfile='qingxu-usage.csv')
        if path:
            try:
                Path(path).write_text(export_csv(self.filtered,self.prices,self.mode),encoding='utf-8',newline='')
                self.detail_text.set(f'已导出当前筛选范围全部 {len(self.filtered):,} 条（不限当前分页）。')
            except OSError as exc:self.detail_text.set('导出失败：'+str(exc))

    def edit_prices(self):
        try:PriceEditor(self)
        except (ValueError,OSError) as exc:self.notice.set('价格设置未打开：'+str(exc))

    def _destroyed(self,event):
        if event.widget is not self:return
        self.closed=True;self.stop.set()
        if self.poll_job:
            try:self.after_cancel(self.poll_job)
            except tk.TclError:pass
        if self.today_job:
            try:self.after_cancel(self.today_job)
            except tk.TclError:pass
        for seq,ident in getattr(self,'_wheel_bindings',[]):
            try:self.app.root.unbind(seq,ident)
            except tk.TclError:pass
        self.executor.shutdown(wait=False,cancel_futures=True)


class PriceEditor(tk.Toplevel):
    def __init__(self,panel):
        expected=panel.price_store.load()
        super().__init__(panel)
        self.panel=panel
        self.title('单价设置 · USD / 百万 Tokens')
        self.geometry('880x740');self.minsize(720,650);self.transient(panel.app.root)
        set_window_icon(self)
        self.expected=expected
        self.data=json.loads(json.dumps(self.expected))
        self.selected=None
        self.vars={}
        self.message=tk.StringVar(self,value='仅影响本地费用估算；修改后会重算历史范围，旧价格表自动备份。')
        actions=ttk.Frame(self,padding=(16,12));actions.pack(side='bottom',fill='x')
        viewport=ttk.Frame(self);viewport.pack(fill='both',expand=True)
        self.form_canvas=tk.Canvas(viewport,background=BG,highlightthickness=0)
        scroll=ttk.Scrollbar(viewport,orient='vertical',command=self.form_canvas.yview)
        scroll.pack(side='right',fill='y');self.form_canvas.pack(side='left',fill='both',expand=True)
        self.form_canvas.configure(yscrollcommand=scroll.set)
        outer=ttk.Frame(self.form_canvas,padding=16)
        item=self.form_canvas.create_window(0,0,anchor='nw',window=outer)
        outer.bind('<Configure>',lambda _:self.form_canvas.configure(scrollregion=self.form_canvas.bbox('all')))
        self.form_canvas.bind('<Configure>',lambda e:self.form_canvas.itemconfigure(item,width=e.width))
        ttk.Label(outer,text='按真实供应商与完整模型 ID 配置价格',style='Section.TLabel').pack(anchor='w')
        ttk.Label(outer,text='内置为 2026-11-11 官网检索参考快照，不是实时更新价格。所有单价单位为 USD / 1M Tokens。',wraplength=800).pack(anchor='w',pady=(4,10))
        self.tree=ttk.Treeview(outer,columns=('provider','model','input','cached','output'),show='headings',height=4)
        for c,name in (('provider','供应商'),('model','模型 ID'),('input','非缓存输入'),('cached','缓存读取'),('output','输出')):
            self.tree.heading(c,text=name);self.tree.column(c,width=110 if c!='model' else 220)
        self.tree.pack(fill='x');self.tree.bind('<<TreeviewSelect>>',self.choose)
        form=ttk.Frame(outer);form.pack(fill='x',pady=12)
        fields=(('provider','供应商 ID'),('model','完整模型 ID'),('input','非缓存输入'),('cached','缓存读取'),
                ('write_30m','缓存写入 30m'),('write_12h','缓存写入 12h'),('output','输出（含推理）'),('long_threshold','长输入门槛（0=无）'),
                ('long_input','长输入/缓存倍率'),('long_output','长输出倍率'))
        for i,(key,label) in enumerate(fields):
            row,col=divmod(i,2)
            ttk.Label(form,text=label).grid(row=row,column=col*2,sticky='w',pady=5,padx=(0,8))
            v=tk.StringVar(self);self.vars[key]=v
            ttk.Entry(form,textvariable=v,width=24).grid(row=row,column=col*2+1,sticky='ew',pady=5,padx=(0,16))
        form.columnconfigure(1,weight=1);form.columnconfigure(3,weight=1)
        self.vars['tiers']=tk.StringVar(self,value='{"standard":"1"}')
        advanced=ttk.Frame(outer)
        self.show_advanced=tk.BooleanVar(self,value=False)
        toggle=ttk.Checkbutton(outer,text='高级：处理档位倍率（Standard / Fast / Flex / Batch）',variable=self.show_advanced,command=lambda:advanced.pack(fill='x',after=toggle) if self.show_advanced.get() else advanced.pack_forget())
        toggle.pack(anchor='w')
        ttk.Entry(advanced,textvariable=self.vars['tiers']).pack(fill='x',pady=5)
        self.allow=tk.BooleanVar(self,value=self.data['allow_assumptions'])
        ttk.Checkbutton(outer,text='允许缺失条件按 Standard / 30m / 全球基础价估算，并在明细标注假设',variable=self.allow).pack(anchor='w',pady=7)
        tools=ttk.Frame(outer);tools.pack(fill='x')
        ttk.Button(tools,text='新增模型',command=self.new).pack(side='left')
        ttk.Button(tools,text='加入/更新待保存列表',command=self.stage).pack(side='left',padx=8)
        ttk.Button(tools,text='删除所选',command=self.remove).pack(side='left')
        ttk.Button(tools,text='查看官方价格',command=lambda:webbrowser.open('https://developers.openai.com/api/docs/pricing')).pack(side='right')
        ttk.Label(outer,textvariable=self.message,wraplength=800,foreground=MUTED).pack(fill='x',pady=12)
        ttk.Button(actions,text='备份并保存单价',command=self.save,style='Primary.TButton').pack(side='right')
        ttk.Button(actions,text='取消',command=self.destroy).pack(side='right',padx=8)
        ttk.Button(actions,text='恢复内置列表',command=self.defaults).pack(side='left')
        self.rebuild();self.new();attach_motion_tree(self)
        self.bind('<Escape>',lambda _:self.destroy())

    def rebuild(self):
        self.tree.delete(*self.tree.get_children())
        for i,r in enumerate(self.data['rates']):
            self.tree.insert('', 'end',iid=str(i),values=tuple(r.get(k) for k in ('provider','model','input','cached','output')))

    def choose(self,_=None):
        chosen=self.tree.selection()
        if not chosen:return
        self.selected=int(chosen[0]);r=self.data['rates'][self.selected]
        for key,v in self.vars.items():v.set(json.dumps(r['tiers']) if key=='tiers' else str(r.get(key) or ''))
        self._baseline_form={k:v.get() for k,v in self.vars.items()}
        self.message.set('来源：'+r.get('source','用户设置')+'；核对日期：'+r.get('checked','未知'))

    def new(self):
        self.selected=None
        self._baseline_form={}
        for key,v in self.vars.items():v.set('')
        self.vars['provider'].set('openai');self.vars['long_threshold'].set('0')
        self.vars['long_input'].set('1');self.vars['long_output'].set('1');self.vars['tiers'].set('{"standard":"1"}')

    def stage(self):
        try:
            row={k:(v.get().strip() or None) for k,v in self.vars.items()}
            row['long_threshold']=int(row['long_threshold'] or '0')
            row['tiers']=json.loads(row['tiers'] or '{}')
            row['source']='用户配置';row['checked']=date.today().isoformat()
            temp=json.loads(json.dumps(self.data))
            if self.selected is None:temp['rates'].append(row)
            else:temp['rates'][self.selected]=row
            self.data=validate_prices(temp);self.rebuild();self.new()
            self.message.set('已更新待保存列表，尚未写入文件。点击“备份并保存单价”生效。')
            return True
        except (ValueError,TypeError,KeyError) as exc:self.message.set('单价未加入：'+str(exc));return False

    def remove(self):
        if self.selected is not None:
            del self.data['rates'][self.selected];self.rebuild();self.new()

    def defaults(self):
        if messagebox.askyesno('恢复内置参考价？','替换待保存列表；点击保存前不改文件。',parent=self):
            self.data=default_prices();self.allow.set(self.data['allow_assumptions']);self.rebuild();self.new()

    def save(self):
        # Unsaved form values must not silently vanish.
        if self.vars['model'].get().strip() and {k:v.get() for k,v in self.vars.items()}!=getattr(self,'_baseline_form',{}) and not self.stage():return
        try:
            self.data['allow_assumptions']=self.allow.get()
            self.panel.prices=self.panel.price_store.save(self.data,self.expected)
            self.panel.quote_cache.clear()
            self.panel.apply_filters()
            self.panel._refresh_today_cost()
            self.panel.sync_text.set('单价已备份、保存并读回；历史范围按新价表重算。')
            self.destroy()
        except (ValueError,OSError) as exc:self.message.set('价格未保存：'+str(exc))
