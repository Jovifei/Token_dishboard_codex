"""Clearly synthetic request data for UI review; never used in normal startup."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path


def requests(now=None, cwd='/demo/qingxu', second_cwd='/demo/photography'):
    now=now or datetime.now(timezone.utc)
    rows=[]
    for day in range(7):
        for m,model in enumerate(('gpt-6.1-sol','gpt-6.1-terra','gpt-6-astra')):
            for i in range(4):
                stamp=(now-timedelta(days=6-day)).replace(hour=1+i,minute=5*m,second=0,microsecond=0)
                amount=18000+(day+1)*(m+1)*12000+(i+1)*1500
                cached=int(amount*(.94 if m==0 else .87))
                written=int(amount*.02)
                output=600+i*140+m*80
                rows.append(dict(response_id=f'demo-{day}-{m}-{i}',time=stamp.isoformat(),provider='openai',model=model,
                    thread_id='demo-ui-main' if m!=1 else 'demo-ui-child',project='轻续 · 示例工程' if m!=1 else '逐星 · 示例工程',
                    cwd=cwd if m!=1 else second_cwd,title='计费功能与页面验收' if m!=1 else '摄影地点筛选器',role='primary' if m!=1 else 'child',
                    service_tier='standard',retention='30m',region='global',status='success' if not(day==4 and i==1) else 'error',
                    duration_ms=25000+i*2000,ttft_ms=700+i*80+m*40,tokens_per_second=22+i*2+m,
                    usage=dict(input_tokens=amount,cached_input_tokens=cached,cache_write_input_tokens=written,
                               output_tokens=output,reasoning_output_tokens=output//3,total_tokens=amount+output)))
    return rows


def create_import(path, now=None):
    Path(path).write_text(json.dumps({'schema':'qingxu.requests.v1','synthetic':True,'requests':requests(now)},ensure_ascii=False,indent=2),encoding='utf-8')
