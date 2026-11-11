"""Native Tk billing screen inspired by the supplied usage-dashboard reference."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import webbrowser

from billing import (Request, PriceStore, default_prices, validate_prices, summary, quote, project_identity,
                     filter_requests, group_requests, daily, money, short_number, export_csv, fixed_timezone, timestamp)
from billing_index import BillingIndex
from ui_motion import attach_motion_tree, reduced_motion
from ui_brand import set_window_icon

BG, CARD, INK, MUTED, BLUE = '#F5F5F7', '#FFFFFF', '#1D1D1F', '#6E6E73', '#007AFF'
TEAL, LINE, ORANGE = '#0895B3', '#E4E7ED', '#A26010'
PERIODS = ('近 7 天', '今天', '近 30 天', '本月', '自定义')


class DailyChart(tk.Canvas):
    """Zero-baseline chart. Unknown amounts are hatched markers, never zero bars."""
    def __init__(self, parent, on_day):
        super().__init__(parent, height=188, background=CARD, highlightthickness=0, takefocus=True)
        self.on_day=on_day
        self.rows=[]
        self.metric='cost'
        self.period='day'
        self.hitboxes=[]
        self.hover=None
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
        w,h=max(360,self.winfo_width()),max(150,self.winfo_height())
        left,right,top,bottom=56,w-16,24,h-34
        values=[float(r[self.metric]) if r[self.metric] is not None else None for _,r in self.rows]
        peak=max([v for v in values if v is not None]+[1.])
        if self.metric=='cache_hit':
            peak=100
        peak=peak*1.12 if self.metric!='cache_hit' else peak
        for frac in (0,.5,1):
            y=bottom-(bottom-top)*frac
            self.create_line(left,y,right,y,fill=LINE)
            v=peak*frac
            label=money(Decimal(str(v)),2) if self.metric=='cost' else f'{v:.0f}%' if self.metric=='cache_hit' else short_number(round(v))
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
                self.create_rectangle(x-half,y,x+half,bottom,outline='',fill=TEAL)
                if self.metric=='cost' and stats['unpriced']:
                    self.create_text(x,max(top,y-9),text='*',fill=ORANGE)
            if i%stride==0 or i==n-1:
                self.create_text(x,bottom+17,text=day[5:].replace('-','/'),fill=MUTED,font=('TkDefaultFont',9))
            self.hitboxes.append((x-step/2,x+step/2,day,stats))
        if not any(r['requests'] for _,r in self.rows):
            self.create_text((left+right)/2,(top+bottom)/2,text='此范围尚无已记录请求（不代表未使用）',fill=MUTED)

    def _hide_tip(self):
        self.delete('tip')
        self.hover=None
        self.configure(cursor='')

    def motion(self,event):
        target=next((r for r in self.hitboxes if r[0]<=event.x<r[1]),None)
        if target==self.hover:
            return
        self._hide_tip()
        if not target:
            return
        self.hover=target
        self.configure(cursor='hand2' if self.period=='day' else '')
        _,_,day,s=target
        current = (money(s['cost'],4) if self.metric=='cost' else
                   ('—' if s['cache_hit'] is None else f"{s['cache_hit']:.2f}%") if self.metric=='cache_hit' else
                   short_number(s[self.metric]))
        unit = {'cost':'成本','tokens':'Tokens','requests':'请求数','cache_hit':'缓存命中率'}[self.metric]
        msg=f"{day}  ·  {unit} {current}  ·  {s['requests']} 条请求" + (f" / {s['unpriced']} 条未计价" if s['unpriced'] else '')
        x=max(8,min(event.x-120,self.winfo_width()-430))
        self.create_rectangle(x,2,x+420,24,fill='#EAF3FF',outline='',tags='tip')
        self.create_text(x+8,13,text=msg,anchor='w',fill=INK,font=('TkDefaultFont',9),tags='tip')

    def click(self,event):
        target=next((r for r in self.hitboxes if r[0]<=event.x<r[1]),None)
        if target and self.period=='day':
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
        self.poll_job=None
        self.closed=False
        self.source=None
        self.active=False
        self.page=0
        self.page_size=100
        self.sort_keys={}
        self.row_objects={}
        self.metric='cost'
        self.period=tk.StringVar(self,value='近 7 天')
        self.provider=tk.StringVar(self,value='全部供应商')
        self.model=tk.StringVar(self,value='全部模型')
        self.scope=tk.StringVar(self,value='全部项目')
        self.tz=tk.StringVar(self,value='本机时区')
        self.cost_mode=tk.StringVar(self,value='当前价表估算')
        today=date.today()
        self.start=tk.StringVar(self,value=(today-timedelta(days=6)).isoformat())
        self.end=tk.StringVar(self,value=today.isoformat())
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

    def _build(self):
        filters=ttk.Frame(self,padding=(2,7,2,8))
        filters.pack(fill='x')
        self.filters=filters
        self._filters_in_canvas=False
        selectors=ttk.Frame(filters)
        selectors.pack(fill='x')
        selector_controls=[]
        for var,values,width in ((self.period,PERIODS,8),
                         (self.scope,('全部项目','当前项目','当前会话'),9),
                         (self.provider,('全部供应商',),12),(self.model,('全部模型',),16)):
            box=ttk.Combobox(selectors,textvariable=var,values=values,width=width,state='readonly')
            selector_controls.append(box)
            box.bind('<<ComboboxSelected>>',lambda e,v=var:self.changed(v))
            if var is self.provider:self.provider_box=box
            if var is self.model:self.model_box=box
        self.refresh_button=ttk.Button(selectors,text='同步用量',width=8,command=self.refresh,style='Primary.TButton')
        selector_controls.append(self.refresh_button)
        dates=ttk.Frame(filters)
        dates.pack(fill='x',pady=(5,0))
        date_range=ttk.Frame(dates)
        self.start_entry=ttk.Entry(date_range,textvariable=self.start,width=10)
        self.start_entry.pack(side='left')
        ttk.Label(date_range,text=' 至 ').pack(side='left')
        self.end_entry=ttk.Entry(date_range,textvariable=self.end,width=10)
        self.end_entry.pack(side='left')
        zone=ttk.Combobox(dates,textvariable=self.tz,values=('本机时区','UTC+08:00','UTC+09:00','UTC'),state='readonly',width=9)
        zone.bind('<<ComboboxSelected>>',lambda _:self.changed(self.period))
        mode=ttk.Combobox(dates,textvariable=self.cost_mode,values=('当前价表估算','来源报告金额'),state='readonly',width=12)
        mode.bind('<<ComboboxSelected>>',lambda _:self.apply_filters())
        apply_button=ttk.Button(dates,text='应用',command=self.apply_filters)
        price_button=ttk.Button(dates,text='单价设置',width=8,command=self.edit_prices)
        date_controls=(date_range,zone,mode,apply_button,price_button)
        for frame,controls in ((selectors,selector_controls),(dates,date_controls)):
            for column,control in enumerate(controls):
                control.grid(row=0,column=column,sticky='w',padx=(0,7),pady=3)
            frame.bind('<Configure>',lambda event,items=controls:self._layout_filters(event,items))
        for entry in (self.start_entry,self.end_entry):
            entry.bind('<Return>',lambda _:self.apply_filters())
        # A vertical canvas gives 980x680/DPI users all four KPIs and the full table.
        viewport=ttk.Frame(self)
        viewport.pack(fill='both',expand=True)
        self.viewport=viewport
        self.canvas=tk.Canvas(viewport,background=BG,highlightthickness=0)
        scroll=ttk.Scrollbar(viewport,orient='vertical',command=self.canvas.yview)
        scroll.pack(side='right',fill='y')
        self.canvas.pack(side='left',fill='both',expand=True)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.body=ttk.Frame(self.canvas,style='App.TFrame',padding=(2,0,8,6))
        window=self.canvas.create_window(0,0,anchor='nw',window=self.body)
        self.body.bind('<Configure>',lambda _:self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>',lambda e:self.canvas.itemconfigure(window,width=e.width))
        hero=ttk.Frame(self.body,style='Card.TFrame',padding=(16,12))
        hero.pack(fill='x')
        self.hero=hero
        self.kpis={}
        labels=(('cost','估算成本 · USD'),('requests','已记录请求'),('tokens','已记录 Tokens'),('cache_hit','缓存命中率'))
        for i,(key,caption) in enumerate(labels):
            cell=ttk.Frame(hero,style='Card.TFrame',padding=(2,4))
            cell.grid(row=0,column=i,sticky='nsew',padx=(0,8))
            cell.configure(takefocus=True)
            cell.bind('<Return>',lambda _,k=key:self.select_metric(k))
            cell.bind('<space>',lambda _,k=key:self.select_metric(k))
            hero.columnconfigure(i,weight=1,uniform='kpi')
            title=ttk.Label(cell,text=caption,style='CardMuted.TLabel')
            title.pack(anchor='w')
            value=ttk.Label(cell,text='—',style='Card.TLabel',font=(getattr(self.app,'font_family','TkDefaultFont'),22,'bold'))
            value.pack(anchor='w',pady=(6,3))
            note=ttk.Label(cell,text='尚无数据',style='CardMuted.TLabel',wraplength=185)
            note.pack(anchor='w')
            cell.bind('<Configure>',lambda e,n=note:n.configure(wraplength=max(80,e.width-6)))
            bar=tk.Frame(cell,height=2,background=BLUE if key==self.metric else CARD)
            bar.pack(fill='x',pady=(9,0))
            for w in (cell,title,value,note,bar):
                w.bind('<Button-1>',lambda _,k=key:self.select_metric(k))
                w.configure(cursor='hand2')
            self.kpis[key]=(title,value,note,bar)
        self.chart_title=ttk.Label(hero,text='成本 · 按天',style='Card.TLabel')
        self.chart_title.grid(row=1,column=0,columnspan=4,sticky='w',pady=(16,2))
        self.chart=DailyChart(hero,self.select_day)
        self.chart.grid(row=2,column=0,columnspan=4,sticky='ew')
        self.caption=ttk.Label(self.body,textvariable=self.notice,style='Muted.TLabel',wraplength=900,justify='left')
        self.caption.pack(fill='x',pady=(9,12))
        self.body.bind('<Configure>',lambda e:self.caption.configure(wraplength=max(300,e.width-18)),add='+')
        self.tables=ttk.Notebook(self.body)
        self.tables.pack(fill='both',expand=True)
        self.trees={}
        self.tab_frames={}
        self.labels={'request':'请求日志','provider':'供应商','model':'模型','project':'项目','session':'会话'}
        columns={'name':('名称',200),'requests':('请求数',72),'cost':('成本 USD',102),'tokens':('Tokens',98),
                 'success':('成功率¹',86),'speed':('速度²',88),'ttft':('首字²',86),'coverage':('计价覆盖',92)}
        for key in self.labels:
            pane=ttk.Frame(self.tables,style='Card.TFrame',padding=(0,6,0,0))
            self.tables.add(pane,text=self.labels[key])
            self.tab_frames[key]=pane
            cols=(('time',('时间',144)),('model',('模型',170)),('provider',('供应商',100)),('project',('项目',135)),
                  ('tokens',('Tokens',105)),('cost',('成本 USD',102)),('status',('状态',84))) if key=='request' else tuple(columns.items())
            tree=ttk.Treeview(pane,columns=[c for c,_ in cols],show='headings',height=7,selectmode='browse')
            for col,(caption,width) in cols:
                tree.heading(col,text=caption,command=lambda k=key,c=col:self.sort_table(k,c))
                tree.column(col,width=width,minwidth=65,stretch=col in ('name','project','model'))
            ys=ttk.Scrollbar(pane,orient='vertical',command=tree.yview)
            xs=ttk.Scrollbar(pane,orient='horizontal',command=tree.xview)
            tree.configure(yscrollcommand=ys.set,xscrollcommand=xs.set)
            ys.pack(side='right',fill='y')
            xs.pack(side='bottom',fill='x')
            tree.pack(fill='both',expand=True)
            tree.bind('<Double-1>',lambda _,k=key:self.drill(k))
            tree.bind('<Return>',lambda _,k=key:self.drill(k))
            self.trees[key]=tree
        self.tables.select(self.tab_frames['model'])
        footer=ttk.Frame(self.body)
        footer.pack(fill='x',pady=(7,0))
        ttk.Button(footer,text='导入请求…',command=self.import_requests).pack(side='left')
        ttk.Button(footer,text='导出当前范围 CSV…',command=self.export).pack(side='left',padx=6)
        ttk.Button(footer,text='重置筛选',command=self.reset).pack(side='left')
        ttk.Button(footer,text='下一页',command=lambda:self.turn_page(1)).pack(side='right')
        self.page_label=ttk.Label(footer,text='请求 0 / 0',style='Muted.TLabel')
        self.page_label.pack(side='right',padx=8)
        ttk.Button(footer,text='上一页',command=lambda:self.turn_page(-1)).pack(side='right')
        ttk.Label(self.body,textvariable=self.detail_text,style='Muted.TLabel',wraplength=850).pack(fill='x',pady=(6,0))
        bottom=ttk.Frame(self,padding=(2,5))
        bottom.pack(side='bottom',fill='x',before=viewport)
        ttk.Label(bottom,textvariable=self.sync_text,style='Muted.TLabel').pack(side='left')
        ttk.Label(bottom,text='¹ 仅已知状态  ² 仅已记录耗时样本',style='Muted.TLabel').pack(side='right')
        self._wheel_bindings=[]
        for seq in ('<MouseWheel>','<Button-4>','<Button-5>'):
            self._wheel_bindings.append((seq,self.app.root.bind(seq,self._wheel,add='+')))
        for control in (*selector_controls,self.start_entry,self.end_entry,zone,mode,apply_button,price_button):
            control.bind('<FocusIn>',self._show_filter_focus,add='+')
        self.bind('<Configure>',self._fit_filters,add='+')
        filters.bind('<Configure>',self._fit_filters,add='+')
        attach_motion_tree(self)

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
        for index,control in enumerate(controls):
            control.grid(row=index//columns,column=index%columns,sticky='w',padx=(0,7),pady=3)

    def _fit_filters(self,event):
        if event.widget not in (self,self.filters):
            return
        reserve=60 if self._filters_in_canvas else 32
        needs_scroll=self.winfo_height()<self.filters.winfo_reqheight()+reserve
        if needs_scroll==self._filters_in_canvas:
            return
        self.filters.pack_forget()
        if needs_scroll:
            self.filters.pack(in_=self.body,fill='x',before=self.hero)
        else:
            self.filters.pack(in_=self,fill='x',before=self.viewport)
        self._filters_in_canvas=needs_scroll

    def _show_filter_focus(self,event):
        if not self._filters_in_canvas:
            return
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

    def _index_for(self,home):
        import hashlib
        return BillingIndex(self.directory/('source-'+hashlib.sha256(home.encode()).hexdigest()[:16]))

    def refresh(self,import_path=None):
        if self.closed or self.future is not None:
            return
        home,db=self._source()
        self.stop=threading.Event()
        stop=self.stop
        self.refresh_button.configure(text='同步中…',state='disabled')
        self.sync_text.set('正在增量读取本机数值记录…')
        def work():
            index=self._index_for(home)
            imported=index.import_file(import_path) if import_path else None
            try:
                result=index.scan(home,db,stop=stop)
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
        if not self.future.done():
            self.poll_job=self.after(80,self._poll)
            return
        future,self.future=self.future,None
        self.refresh_button.configure(text='同步用量',state='normal')
        if self.stop.is_set() or self.source!=self._source()[0]:
            self.sync_text.set('已丢弃旧数据来源的读取结果，请重新同步')
            return
        try:
            self.set_result(future.result())
            self.sync_text.set('最后同步 '+datetime.now().strftime('%H:%M:%S')+' · 手动增量刷新')
        except Exception as exc:
            self.sync_text.set('同步失败；显示的仅为上次结果')
            self.notice.set('同步失败：'+str(exc)+'。不会用 0 代替未知费用。')

    def set_result(self,result):
        self.result=result
        self.requests=list(result['requests'])
        providers=sorted({r.provider for r in self.requests})
        models=sorted({r.model for r in self.requests})
        self.provider_box.configure(values=('全部供应商',*providers))
        self.model_box.configure(values=('全部模型',*models))
        if self.provider.get() not in ('全部供应商',*providers):self.provider.set('全部供应商')
        if self.model.get() not in ('全部模型',*models):self.model.set('全部模型')
        self.page=0
        self.apply_filters()

    def _apply_period(self):
        now=datetime.now(timezone.utc).astimezone(fixed_timezone(self.tz.get())).date()
        kind=self.period.get()
        if kind!='自定义':
            start=now if kind=='今天' else now-timedelta(days=29 if kind=='近 30 天' else 6) if kind!='本月' else now.replace(day=1)
            self.start.set(start.isoformat()); self.end.set(now.isoformat())
        state='normal' if kind=='自定义' else 'readonly'
        self.start_entry.configure(state=state);self.end_entry.configure(state=state)

    def changed(self,var):
        if var is self.period:self._apply_period()
        self.page=0
        self.apply_filters()

    def apply_filters(self):
        try:
            start,end=date.fromisoformat(self.start.get()),date.fromisoformat(self.end.get())
            cwd=thread=project_id=''
            if self.scope.get()!='全部项目':
                record=getattr(self.app,'chat',None)
                if not record:raise ValueError('先在顶部选择项目和聊天，或使用“全部项目”')
                if self.scope.get()=='当前项目':
                    project_id=str(record.get('app_project_id') or '')
                    if not project_id:cwd=record.get('path','')
                else:thread=record.get('chat_id','')
                if not (cwd or thread or project_id):raise ValueError('当前选择缺少可核对的项目/会话身份')
            self.filtered=filter_requests(self.requests,start=start,end=end,tz_label=self.tz.get(),
                    provider='' if self.provider.get()=='全部供应商' else self.provider.get(),
                    model='' if self.model.get()=='全部模型' else self.model.get(),cwd=cwd,thread_id=thread,
                    app_project_id=project_id,unassigned_only=bool(cwd))
            mode=self.mode
            data=summary(self.filtered,self.prices,mode=mode)
            self.stats=data
            self.date_rows,self.chart_period=daily(self.filtered,start,end,self.tz.get(),self.prices,mode=mode)
        except (ValueError,TypeError) as exc:
            self.notice.set('筛选未应用：'+str(exc));return
        self.page=min(self.page,max(0,(len(self.filtered)-1)//self.page_size))
        self._render_kpis(data)
        self.select_metric(self.metric)
        self.render_tables()
        issues=(self.result or {}).get('issues',{})
        excluded_keys = ('conflicts_excluded', 'invalid_requests', 'invalid_records', 'unsafe_files',
                         'oversize_requests', 'read_errors', 'pending_files', 'incomplete_files',
                         'compressed_files_unsupported', 'source_unavailable', 'file_limit_reached')
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
        self.kpis['cost'][0].configure(text=('部分估算 · USD' if s['unpriced'] else '估算成本 · USD') if self.mode=='estimate' else '已报告成本 · USD')
        amounts={'cost':money(s['cost']), 'requests':f"{s['requests']:,}", 'tokens':short_number(s['tokens']),
                 'cache_hit':'—' if s['cache_hit'] is None else f"{s['cache_hit']:.1f}%"}
        notes={'cost':f"{s['priced']:,}/{s['requests']:,} 条已计价" + (' · 部分' if s['unpriced'] else ''),
               'requests':'请求记录，不是消息数', 'tokens':'输入 + 输出 · 含缓存',
               'cache_hit':f"缓存读取 / 已知输入 · {s['cache_known']}条"}
        for key,(_,value,note,_) in self.kpis.items():
            value.configure(text=amounts[key]); note.configure(text=notes[key])

    def select_metric(self,key):
        self.metric=key
        for k,(_,_,_,bar) in self.kpis.items():bar.configure(background=BLUE if k==key else CARD)
        labels={'cost':'成本','requests':'请求数','tokens':'Tokens','cache_hit':'缓存命中率'}
        units={'day':'天','week':'周','month':'月'}
        self.chart_title.configure(text=labels[key]+' · 按'+units.get(getattr(self,'chart_period','day'),'天')+('  （点击日期下钻）' if getattr(self,'chart_period','day')=='day' else '  （聚合视图）'))
        self.chart.render(getattr(self,'date_rows',[]),key,getattr(self,'chart_period','day'))

    def select_day(self,day):
        self.period.set('自定义');self._apply_period()
        self.start.set(day);self.end.set(day);self.page=0
        self.apply_filters();self.tables.select(self.tab_frames['request'])

    def render_tables(self):
        self.row_objects={}
        for key,tree in self.trees.items():
            tree.delete(*tree.get_children())
            objs=list(self.filtered) if key=='request' else group_requests(self.filtered,key,self.prices,self.mode)
            self.tables.tab(self.tab_frames[key],text=f'{self.labels[key]}  {len(objs):,}')
            column,desc=self.sort_keys.get(key,('time',True) if key=='request' else ('cost',True))
            def val(obj):
                if key=='request':
                    q=quote(obj,self.prices)
                    return {'time':obj.time,'model':obj.model,'provider':obj.provider,'project':obj.project,
                            'tokens':(obj.usage or {}).get('totalTokens'),
                            'cost':q.amount if self.mode=='estimate' else Decimal(obj.reported_cost_usd) if obj.reported_cost_usd is not None else None,
                            'status':obj.status}.get(column)
                ident,s=obj
                if column=='name':return str(ident)
                if column=='coverage':return s['priced']/max(1,s['requests'])
                return s.get(column)
            # Unknowns always last, regardless of sort direction.
            known=[obj for obj in objs if val(obj) is not None];unknown=[obj for obj in objs if val(obj) is None]
            objs=sorted(known,key=val,reverse=desc)+unknown
            if key=='request':objs=objs[self.page*self.page_size:(self.page+1)*self.page_size]
            mapping={}
            for i,obj in enumerate(objs):
                iid=f'{key}-{i}'
                mapping[iid]=obj
                if key=='request':
                    r=obj; q=quote(r,self.prices)
                    amount=q.amount if self.mode=='estimate' else Decimal(r.reported_cost_usd) if r.reported_cost_usd is not None else None
                    values=(timestamp(r.time).astimezone(fixed_timezone(self.tz.get())).strftime('%m-%d %H:%M:%S'),r.model,r.provider,r.project,
                            short_number((r.usage or {}).get('totalTokens')),money(amount,4),{'unknown':'未记录','success':'成功','error':'失败','cancelled':'已取消'}[r.status])
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
            self.panel.apply_filters()
            self.panel.sync_text.set('单价已备份、保存并读回；历史范围按新价表重算。')
            self.destroy()
        except (ValueError,OSError) as exc:self.message.set('价格未保存：'+str(exc))
