"""Incremental local request ledger; Codex sources are read-only.

Only response-level token_usage_record. token_count is a context signal and can
be synthesized by compaction: it must not become a fabricated bill/request.
SQLite stores only whitelisted metadata/numbers, never prompts/auth/config.
"""
from __future__ import annotations
from collections import Counter
from contextlib import closing, nullcontext
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading

from billing import Request, timestamp, text
from chats import metadata_db, ProjectCatalog, title_expression, path_key, usage_metadata
from privacy import ensure_private
from sync import safe_path

try:
    from compression import zstd
except ImportError:
    try:
        from backports import zstd
    except ImportError:
        zstd = None

ZSTD_ERRORS = (EOFError, zstd.ZstdError) if zstd is not None else (EOFError,)

MAX_LINE = 1024 * 1024
MAX_IMPORT = 16 * 1024 * 1024
SCAN_BUDGET = 128 * 1024 * 1024
MAX_FILES = 30000


def thread_metadata(database):
    """Project/role only. Never apply the DB's *latest* model to past requests."""
    result = {}
    try:
        with metadata_db(database) as (conn, path):
            cols = {r[1] for r in conn.execute('PRAGMA table_info(threads)')}
            if not {'id','cwd'} <= cols:
                return {}, '元数据结构未识别，项目将使用日志目录'
            catalog = ProjectCatalog(conn, path)
            optional = lambda name: name if name in cols else 'NULL'
            fields = ','.join(('id',title_expression(cols),'cwd',optional('project_id'),optional('agent_role'),optional('source')))
            for ident, title, cwd, pid, role, source in conn.execute('SELECT '+fields+' FROM threads'):
                resolved, _ = catalog.membership(ident, cwd, pid)
                result[ident] = {'project': catalog.projects[resolved]['label'] if resolved else Path(str(cwd)).name or '未归类',
                                 'cwd': cwd or '', 'title': title or '未命名聊天',
                                 'app_project_id': str(resolved) if resolved is not None else '',
                                 'role': 'child' if role or 'subagent' in str(source).lower() else 'primary'}
    except (ValueError, OSError, sqlite3.Error, KeyError, TypeError) as exc:
        return {}, '项目映射不可用：'+str(exc)[:240]
    return result, ''


def consume(item, state, issues):
    """Consume one already-parsed rollout line; return at most one request."""
    if not isinstance(item, dict) or not isinstance(item.get('payload'), dict):
        issues['invalid_records'] += 1
        return None
    kind, p = item.get('type'), item['payload']
    if kind == 'session_meta':
        # Fork logs may contain inherited session_meta after their own header.
        if not state.get('thread_id'):
            state.update(thread_id=text(p.get('id')), provider=text(p.get('model_provider'), 'unknown'),
                         cwd=text(p.get('cwd'), limit=2048),
                         role='child' if p.get('parent_thread_id') or p.get('agent_role') or 'subagent' in str(p.get('source','')).lower() else 'fork' if p.get('forked_from_id') else 'primary')
        return None
    if kind == 'turn_context':
        ctx = {k: text(p[k]) for k in ('model', 'model_provider', 'service_tier', 'prompt_cache_retention', 'region') if isinstance(p.get(k), str)}
        tid = text(p.get('turn_id'))
        state['current'] = ctx
        state['current_turn'] = tid
        if tid:
            contexts = state.setdefault('contexts', {})
            contexts[tid] = ctx
            if len(contexts) > 1000:
                contexts.pop(next(iter(contexts)))
        return None
    if kind == 'event_msg' and p.get('type') == 'token_count':
        issues['context_snapshots_ignored'] += 1
        return None
    if kind != 'token_usage_record':
        return None
    if not state.get('thread_id') or p.get('thread_id') != state['thread_id']:
        issues['inherited_or_unbound'] += 1
        return None
    tid = text(p.get('turn_id'))
    ctx = state.get('contexts', {}).get(tid, {}) if tid else state.get('current', {})
    raw = {key: p[key] for key in ('response_id', 'thread_id', 'status', 'duration_ms', 'ttft_ms', 'tokens_per_second',
                                   'reported_cost_usd', 'usage') if key in p}
    # Explicit usage-record fields win, then matching turn context, then session.
    raw.update(time=p.get('timestamp') or item.get('timestamp'),
               model=p.get('model') or ctx.get('model') or 'unknown',
               provider=p.get('provider') or p.get('model_provider') or ctx.get('model_provider') or state.get('provider') or 'unknown',
               service_tier=p.get('service_tier') or ctx.get('service_tier') or '',
               retention=p.get('prompt_cache_retention') or ctx.get('prompt_cache_retention') or '',
               region=p.get('region') or ctx.get('region') or '',
               cwd=state.get('cwd', ''), project=Path(state.get('cwd') or '').name or '未归类',
               title='未命名聊天', role=state.get('role','unknown'), origin='codex',
               model_source='record' if p.get('model') else 'turn_context' if ctx.get('model') else 'unknown')
    try:
        if raw.get('usage') is None:
            raise ValueError('原生用量记录缺少 usage')
        return Request.from_dict(raw)
    except (ValueError, TypeError, KeyError):
        issues['invalid_requests'] += 1
        return None


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _anchor(stream, offset):
    stream.seek(max(0, offset-4096))
    return hashlib.sha256(stream.read(min(offset,4096))).hexdigest()


class BillingIndex:
    def __init__(self, directory):
        self.directory=Path(directory)
        ensure_private(self.directory)
        self.db=self.directory/'requests.sqlite3'
        if self.db.is_symlink():
            raise ValueError('计费缓存不能是符号链接')
        with closing(self.connect()) as con:
            con.executescript('''
                CREATE TABLE IF NOT EXISTS files(
                  path TEXT PRIMARY KEY, root TEXT NOT NULL, size INTEGER, mtime INTEGER,
                  inode INTEGER, offset INTEGER, anchor TEXT, state TEXT, issues TEXT);
                CREATE TABLE IF NOT EXISTS events(
                  file TEXT NOT NULL, pos INTEGER NOT NULL, event_id TEXT NOT NULL,
                  body TEXT NOT NULL, PRIMARY KEY(file,pos));
                CREATE INDEX IF NOT EXISTS events_ids ON events(event_id);
                CREATE TABLE IF NOT EXISTS imports(
                  event_id TEXT PRIMARY KEY, body TEXT NOT NULL);
            ''')
            con.commit()

    def connect(self):
        return sqlite3.connect(self.db, timeout=5)

    def scan(self, home, database=None, *, stop: threading.Event | None = None, budget=SCAN_BUDGET,
             include_requests=True):
        home=Path(home).expanduser().absolute()
        root_key=str(home)
        issue=Counter()
        if not home.is_dir():
            raise ValueError('Codex 数据目录不存在；可先导入标准请求文件')
        if home.is_symlink():
            raise ValueError('请指定实际 Codex 目录而不是符号链接')
        paths=[]
        # rglob does not recurse directory symlinks on supported Python versions.
        for folder in (home/'sessions',home/'archived_sessions'):
            if not folder.exists():
                continue
            if folder.is_symlink():
                issue['unsafe_files']+=1
                continue
            for p in folder.rglob('*'):
                if stop and stop.is_set():
                    raise InterruptedError('已停止索引')
                if p.name.endswith('.jsonl.zst') or p.suffix=='.jsonl':
                    if p.is_symlink():
                        issue['unsafe_files']+=1
                        continue
                    paths.append(p)
                    if len(paths)>=MAX_FILES:
                        issue['file_limit_reached']+=1
                        break
            if issue['file_limit_reached']:
                break
        # Never call stat through an unverified path.
        safe=[]
        for p in paths:
            try:
                p=safe_path(p)
                if not any(p.is_relative_to(home/n) for n in ('sessions','archived_sessions')):
                    raise ValueError('日志路径越界')
                st=p.stat()
                if not p.is_file() or st.st_nlink != 1:
                    raise ValueError('非普通文件')
                safe.append((p,st))
            except (OSError,ValueError):
                issue['unsafe_files']+=1
        safe.sort(key=lambda item:item[1].st_mtime_ns, reverse=True)
        seen={str(p) for p,_ in safe}
        consumed=0
        refreshed=0
        budget_incomplete=False
        with closing(self.connect()) as con:
            for p,st in safe:
                if stop and stop.is_set():
                    raise InterruptedError('已停止索引')
                key=str(p)
                row=con.execute('SELECT size,mtime,inode,offset,anchor,state,issues FROM files WHERE path=?',(key,)).fetchone()
                compressed=p.name.endswith('.jsonl.zst')
                unchanged=bool(row and row[0]==st.st_size and row[1]==st.st_mtime_ns and row[2]==st.st_ino)
                cached_state=json.loads(row[5]) if row else {}
                if compressed and zstd is None:
                    issue['compressed_unavailable']+=1
                    if row and not unchanged:
                        con.execute('DELETE FROM events WHERE file=?',(key,))
                        con.execute('DELETE FROM files WHERE path=?',(key,))
                        con.commit()
                    continue
                if unchanged and (cached_state.get('_zstd_done') if compressed else row[3]==st.st_size):
                    continue
                if unchanged and not compressed and cached_state.get('_waiting_tail'):
                    issue['incomplete_files']+=1
                    continue
                if consumed>=budget:
                    issue['pending_files']+=1
                    continue
                try:
                    with p.open('rb') as raw:
                        opened=os.fstat(raw.fileno())
                        fingerprint=lambda value:(value.st_size,value.st_mtime_ns,value.st_ino)
                        if compressed and fingerprint(opened)!=fingerprint(st):
                            raise OSError('压缩日志已变化')
                        # Zstandard offsets are decoded bytes; unchanged archives replay to that point.
                        anchor=_anchor(raw,st.st_size) if compressed else None
                        append = (bool(unchanged and row[4]==anchor) if compressed else
                                  bool(row and row[2]==st.st_ino and st.st_size>=row[0]
                                       and (st.st_size>row[0] or st.st_mtime_ns==row[1])
                                       and _anchor(raw,row[3])==row[4]))
                        offset,state,local = (row[3],json.loads(row[5]),Counter(json.loads(row[6]))) if append else (0,{},Counter())
                        state.pop('_waiting_tail',None)
                        if not append:
                            con.execute('DELETE FROM events WHERE file=?',(key,))
                        raw.seek(0)
                        with (zstd.open(raw,'rb') if compressed else nullcontext(raw)) as stream:
                            stream.seek(offset)
                            done=False
                            while (compressed or stream.tell()<st.st_size) and consumed<budget:
                                if stop and stop.is_set():
                                    raise InterruptedError('已停止索引')
                                pos=stream.tell()
                                line=stream.readline(MAX_LINE if compressed else min(MAX_LINE,st.st_size-pos))
                                if not line:
                                    done=True
                                    state.pop('_draining_line',None)
                                    break
                                consumed+=len(line)
                                if state.get('_draining_line'):
                                    offset=stream.tell()
                                    if line.endswith(b'\n'):state.pop('_draining_line',None)
                                    continue
                                if not line.endswith(b'\n'):
                                    if len(line)>=MAX_LINE:
                                        is_request=b'"token_usage_record"' in line[:256]
                                        local['oversize_requests' if is_request else 'oversize_other_lines']+=1
                                        state['_draining_line']=True
                                        offset=stream.tell()
                                        continue
                                    if not compressed:
                                        # Do not commit an active, partially-written record.
                                        state['_waiting_tail']=True
                                        stream.seek(pos)
                                        break
                                offset=stream.tell()
                                # Only these metadata lines are parsed; message bodies are never cached.
                                if not any(t in line[:512] for t in (b'"session_meta"',b'"turn_context"',b'"token_usage_record"',b'"token_count"')):
                                    continue
                                try:
                                    item=json.loads(line)
                                    req=consume(item,state,local)
                                except (UnicodeDecodeError,ValueError,TypeError):
                                    local['invalid_records']+=1
                                    continue
                                if req is not None:
                                    con.execute('INSERT OR REPLACE INTO events VALUES(?,?,?,?)',(key,pos,req.identity,_json(req.as_dict())))
                            if compressed:
                                if done:state['_zstd_done']=True
                            else:
                                anchor=_anchor(stream,offset)
                        if compressed and (fingerprint(os.fstat(raw.fileno()))!=fingerprint(st)
                                           or fingerprint(safe_path(p).stat())!=fingerprint(st)):
                            raise OSError('压缩日志读取时已变化')
                        con.execute('INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?,?,?)',
                                    (key,root_key,st.st_size,st.st_mtime_ns,st.st_ino,offset,anchor,_json(state),_json(local)))
                        con.commit()
                        refreshed+=1
                        if (compressed and not done) or (not compressed and offset<st.st_size):
                            issue['incomplete_files']+=1
                            if consumed>=budget:budget_incomplete=True
                except InterruptedError:
                    con.rollback()
                    raise
                except ZSTD_ERRORS:
                    con.rollback()
                    if compressed:
                        con.execute('DELETE FROM events WHERE file=?',(key,))
                        con.execute('INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?,?,?)',
                                    (key,root_key,st.st_size,st.st_mtime_ns,st.st_ino,0,'',
                                     _json({'_zstd_done':True}),_json({'compressed_read_errors':1})))
                        con.commit()
                    else:
                        issue['read_errors']+=1
                except (OSError,ValueError):
                    con.rollback()
                    if compressed:
                        con.execute('DELETE FROM events WHERE file=?',(key,))
                        con.execute('DELETE FROM files WHERE path=?',(key,))
                        con.commit()
                    issue['read_errors']+=1
            # Only purge positively missing paths, never on a bounded/partial listing.
            if not issue['file_limit_reached'] and not issue['unsafe_files']:
                stale=[r[0] for r in con.execute('SELECT path FROM files WHERE root=?',(root_key,)) if r[0] not in seen]
                for key in stale:
                    con.execute('DELETE FROM events WHERE file=?',(key,))
                    con.execute('DELETE FROM files WHERE path=?',(key,))
                con.commit()
        issue['files_seen']=len(safe)
        issue['files_refreshed']=refreshed
        issue['bytes_read']=consumed
        issue['resume_required']=bool(budget_incomplete or issue['pending_files'])
        if not include_requests:
            return {'issues':dict(issue)}
        return self.load(root_key, database, issue)

    def load(self, root, database=None, issues=None):
        issue=Counter(issues or {})
        metadata,note=thread_metadata(database) if database else ({},'')
        with closing(self.connect()) as con:
            rows=con.execute('SELECT e.body FROM events e JOIN files f ON e.file=f.path WHERE f.root=? ORDER BY e.file,e.pos',(str(root),)).fetchall()
            rows+=con.execute('SELECT body FROM imports ORDER BY event_id').fetchall()
            for (stats,) in con.execute('SELECT issues FROM files WHERE root=?',(str(root),)):
                issue.update(json.loads(stats))
            contexts=con.execute('SELECT f.state FROM files f WHERE f.root=? AND NOT EXISTS(SELECT 1 FROM events e WHERE e.file=f.path)',(str(root),)).fetchall()
            issue['files_without_requests']=sum(bool(json.loads(r[0]).get('thread_id')) for r in contexts)
        requests = [Request.from_dict(json.loads(body), normalized=True) for (body,) in rows]
        children = {req.thread_id for req in requests if req.thread_id and
                    (req.role == 'child' or (req.role != 'fork' and
                     metadata.get(req.thread_id, {}).get('role') == 'child'))}
        parents = {}
        if database and children:
            try:
                parents = usage_metadata(database, [{'thread_id': identity, 'role': 'child'}
                                                     for identity in children])
            except (ValueError, OSError, sqlite3.Error, KeyError, TypeError) as exc:
                note = note or '子 Agent 项目映射不可用：' + str(exc)[:240]
        seen,conflicts={},set()
        for req in requests:
            meta=metadata.get(req.thread_id,{})
            if meta:
                req=replace(req, project=meta['project'], title=meta['title'], cwd=req.cwd or meta['cwd'],
                            app_project_id=meta['app_project_id'],
                            role=meta['role'] if req.role not in ('child','fork') else req.role)
            elif req.app_project_id:
                req=replace(req, app_project_id='')
            parent = parents.get(req.thread_id)
            if (req.role == 'child' and not req.app_project_id and parent
                    and parent.get('chat_id') != req.thread_id and parent.get('project_id')):
                req = replace(req, project=parent['project_name'], app_project_id=str(parent['project_id']))
            key=req.identity
            if key in seen:
                prev=seen[key]
                # Project labels and titles can change; only bill-affecting facts conflict.
                critical=('usage','provider','model','thread_id','time','service_tier','retention','region','cache_write_30m','cache_write_12h','cache_known')
                if any(getattr(req,k)!=getattr(prev,k) for k in critical):
                    conflicts.add(key)
                else:
                    issue['duplicates']+=1
                    fields=('status','duration_ms','ttft_ms','tokens_per_second','reported_cost_usd')
                    changes={}
                    for field in fields:
                        old,new=getattr(prev,field),getattr(req,field)
                        missing=(old is None or old=='unknown')
                        present=(new is not None and new!='unknown')
                        if missing and present:changes[field]=new
                        elif present and not missing and old!=new:conflicts.add(key)
                    if changes:seen[key]=replace(prev,**changes)
            else:
                seen[key]=req
        issue['conflicts_excluded']=len(conflicts)
        requests=sorted((v for k,v in seen.items() if k not in conflicts),key=lambda r:(r.time,r.response_id),reverse=True)
        return {'requests':requests,'issues':dict(issue),'mapping_note':note,
                'synced_at':datetime.now(timezone.utc).isoformat(),'source':str(root)}

    def import_file(self, path):
        """Validate the entire import before a transaction; never retain raw input."""
        p=Path(path)
        if not p.is_file() or p.is_symlink() or p.stat().st_size>MAX_IMPORT:
            raise ValueError('仅导入不超过 16 MiB 的普通 JSON / JSONL 文件')
        raw=p.read_text(encoding='utf-8-sig')
        if p.suffix.lower()=='.jsonl':
            items=[json.loads(line) for line in raw.splitlines() if line.strip()]
        else:
            data=json.loads(raw)
            if not isinstance(data,dict) or data.get('schema')!='qingxu.requests.v1':
                raise ValueError('JSON 需包含 schema=qingxu.requests.v1 和 requests 数组')
            items=data.get('requests')
        if not isinstance(items,list) or not items or len(items)>100_000:
            raise ValueError('导入需包含 1..100000 条请求')
        rows={}
        for i,item in enumerate(items,1):
            try:
                req=Request.from_dict(item)
                serialized=_json(req.as_dict())
                if req.identity in rows and rows[req.identity]!=serialized:
                    raise ValueError('导入内部请求 ID 冲突')
                rows[req.identity]=serialized
            except (ValueError,KeyError,TypeError) as exc:
                raise ValueError(f'第 {i} 条请求无效：{exc}') from exc
        inserted=0
        with closing(self.connect()) as con:
            try:
                con.execute('BEGIN IMMEDIATE')
                for identity,body in rows.items():
                    old=con.execute('SELECT body FROM imports WHERE event_id=?',(identity,)).fetchone()
                    if old:
                        if old[0]!=body:
                            raise ValueError('已导入请求的内容冲突：'+identity.split('\x1f')[-1])
                        continue
                    con.execute('INSERT INTO imports VALUES(?,?)',(identity,body))
                    inserted+=1
                con.commit()
            except Exception:
                con.rollback()
                raise
        return inserted
