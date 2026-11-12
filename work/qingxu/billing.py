"""Auditable request-level USD estimates. Never a subscription bill.

No network, model calls, credentials or chat contents. Rates are versioned local
snapshots; Decimal is retained until display/export. Reasoning is part of output.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import csv
import hashlib
import io
import json
import math
import ntpath
import os
from pathlib import Path
import posixpath
import tempfile
from typing import Any

from context_usage import normalize
from chats import path_key
from privacy import ensure_private

MILLION = Decimal(1_000_000)
RATE_FIELDS = ('input', 'cached', 'write_30m', 'write_12h', 'output')
SOURCE_URL = 'https://developers.openai.com/api/docs/pricing'
RATE_CHECKED = '2026-11-11'


def decimal(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError('金额/倍率必须是有限非负数')
    try:
        result = Decimal(str(value))
    except (ValueError, InvalidOperation):
        raise ValueError('金额/倍率必须是有限非负数') from None
    if not result.is_finite() or result < 0 or result > Decimal('1e12'):
        raise ValueError('金额/倍率必须是有限非负数且不超过 1e12')
    return result


def timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError('时间需为带时区的 ISO 8601 字符串')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('时间需为带时区的 ISO 8601 字符串') from None
    if result.tzinfo is None:
        raise ValueError('时间缺少时区')
    return result.astimezone(timezone.utc)


def text(value: Any, default: str = '', limit: int = 240) -> str:
    return value[:limit].strip() if isinstance(value, str) else default


def optional_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('耗时/速度需为有限非负数，未知请留空')
    return float(value)


def cache_write_split(usage: dict) -> tuple[int, int] | None:
    """Responses reports disjoint TTL components; never charge aggregate again."""
    detail = usage.get('input_tokens_details') or usage.get('prompt_tokens_details') or {}
    split = usage.get('cache_write_tokens_details') or (detail.get('cache_write_tokens_details') if isinstance(detail, dict) else None)
    if split is None:
        return None
    if not isinstance(split, dict):
        raise ValueError('缓存写入分项需为对象')
    values = (split.get('ttl_30m_tokens'), split.get('ttl_12h_tokens'))
    if any(type(v) is not int or v < 0 or v > 2**63-1 for v in values):
        raise ValueError('缓存写入分项必须含两个非负整数')
    aggregate = usage.get('cache_write_input_tokens', detail.get('cache_write_tokens') if isinstance(detail, dict) else None)
    if aggregate is not None and aggregate != sum(values):
        raise ValueError('缓存写入分项与总量不一致')
    return values


def canonical_usage(raw: dict) -> dict:
    """Accept Codex or Responses/Chat-Completions shapes, not cumulative sums."""
    if not isinstance(raw, dict):
        raise ValueError('usage 必须为对象')
    if 'input_tokens' not in raw and 'prompt_tokens' in raw:
        raw = dict(raw, input_tokens=raw['prompt_tokens'], output_tokens=raw.get('completion_tokens'))
    raw = dict(raw)
    detail = raw.get('input_tokens_details') or raw.get('prompt_tokens_details') or {}
    output_detail = raw.get('output_tokens_details') or raw.get('completion_tokens_details') or {}
    if not isinstance(detail, dict) or not isinstance(output_detail, dict):
        raise ValueError('usage details 必须为对象')
    raw.setdefault('cached_input_tokens', detail.get('cached_tokens', 0))
    split = cache_write_split(raw)
    raw.setdefault('cache_write_input_tokens', sum(split) if split is not None else detail.get('cache_write_tokens', 0))
    raw.setdefault('reasoning_output_tokens', output_detail.get('reasoning_tokens', 0))
    normalized = normalize(raw)
    if any(v > 2**63-1 for v in normalized.values()):
        raise ValueError('单请求 Token 超出 64 位计量范围')
    return normalized


@dataclass(frozen=True)
class Request:
    response_id: str
    time: str
    provider: str
    model: str
    thread_id: str
    project: str
    cwd: str
    title: str
    role: str
    usage: dict | None
    status: str = 'unknown'
    service_tier: str = ''
    retention: str = ''
    region: str = ''
    duration_ms: float | None = None
    ttft_ms: float | None = None
    tokens_per_second: float | None = None
    reported_cost_usd: str | None = None
    origin: str = 'codex'
    model_source: str = 'record'
    cache_known: bool = True
    cache_write_30m: int | None = None
    cache_write_12h: int | None = None
    app_project_id: str = ''

    @property
    def identity(self) -> str:
        # Response IDs are shared by active/archive/fork copies. Provider is a
        # namespace; usage from different providers must never be merged.
        return self.provider + '\x1f' + self.response_id

    def as_dict(self) -> dict:
        result = dict(self.__dict__)
        # Preserve the serialized form of existing imports and cache entries.
        if not self.app_project_id:
            result.pop('app_project_id')
        return result

    @classmethod
    def from_dict(cls, raw: dict, *, normalized: bool = False) -> 'Request':
        if not isinstance(raw, dict):
            raise ValueError('每条请求必须为对象')
        rid = text(raw.get('response_id'), limit=300)
        if not rid or any(ord(c) < 32 for c in rid):
            raise ValueError('缺少合法 response_id；不得用聊天累计值伪造请求')
        when = timestamp(raw.get('time') or raw.get('timestamp')).isoformat()
        usage = raw.get('usage')
        cache_known = bool(raw.get('cache_known', True)) if normalized else bool(isinstance(usage, dict) and ('cached_input_tokens' in usage or 'cached_tokens' in (usage.get('input_tokens_details') or usage.get('prompt_tokens_details') or {})))
        if usage is not None:
            if normalized:
                from usage import counts
                usage = counts(usage)
            else:
                usage = canonical_usage(usage)
        split = None
        if usage is not None:
            if normalized and (raw.get('cache_write_30m') is not None or raw.get('cache_write_12h') is not None):
                split = (raw.get('cache_write_30m'), raw.get('cache_write_12h'))
                if any(type(v) is not int or v < 0 for v in split) or sum(split) != usage['cacheCreationTokens']:
                    raise ValueError('缓存写入分项与总量不一致')
            elif not normalized:
                split = cache_write_split(raw['usage'])
        status = text(raw.get('status'), 'unknown').lower()
        status = {'completed': 'success', 'ok': 'success', 'failed': 'error', 'incomplete': 'error'}.get(status, status)
        if status not in ('success', 'error', 'cancelled', 'unknown'):
            status = 'unknown'
        report = raw.get('reported_cost_usd')
        if report is not None and raw.get('currency', 'USD') != 'USD':
            raise ValueError('reported_cost_usd 仅接受 USD，不自动换汇')
        duration, ttft = optional_number(raw.get('duration_ms')), optional_number(raw.get('ttft_ms'))
        if duration is not None and ttft is not None and ttft > duration:
            raise ValueError('首字耗时不能大于完整请求耗时')
        return cls(rid, when, text(raw.get('provider'), 'unknown') or 'unknown',
                   text(raw.get('model'), 'unknown') or 'unknown', text(raw.get('thread_id')),
                   text(raw.get('project'), '未归类') or '未归类', text(raw.get('cwd'), limit=2048),
                   text(raw.get('title'), '未命名聊天'), text(raw.get('role'), 'unknown'), usage,
                   status, text(raw.get('service_tier')), text(raw.get('retention')), text(raw.get('region')),
                   duration, ttft, optional_number(raw.get('tokens_per_second')),
                   str(decimal(report)) if report is not None else None,
                   text(raw.get('origin'), 'import'), text(raw.get('model_source'), 'record'), cache_known,
                   split[0] if split else None, split[1] if split else None,
                   text(raw.get('app_project_id')) if normalized else '')


def default_prices() -> dict:
    # Retrieved official snapshots, NOT live prices or asserted historical rates.
    rows = []
    for model, vals in (
        ('gpt-6-astra', ('10', '1', '12.50', '25', '50')),
        ('gpt-6.1-sol', ('2', '0.10', '2.50', '5', '10')),
        ('gpt-6.1-terra', ('1', '0.05', '1.25', '2.50', '5')),
        ('gpt-6-luna', ('0.10', '0.01', '0.125', '0.25', '0.50')),
    ):
        rows.append({'provider': 'openai', 'model': model, **dict(zip(RATE_FIELDS, vals)),
                     'long_threshold': 272000, 'long_input': '2', 'long_output': '1.5',
                     'source': SOURCE_URL, 'checked': RATE_CHECKED,
                     'tiers': {'standard': '1', 'fast': '2', 'flex': '0.5', 'batch': '0.5',
                               **({'ultrafast': '6'} if model in ('gpt-6-astra', 'gpt-6.1-sol') else {})}})
    return {'schema': 1, 'currency': 'USD', 'allow_assumptions': True, 'rates': rows,
            'notice': '官网检索参考快照；按当前价表重算，不是历史账单。未含工具、税费、订阅费用。'}


def validate_prices(data: dict) -> dict:
    if not isinstance(data, dict) or data.get('schema') != 1 or data.get('currency') != 'USD':
        raise ValueError('仅支持 schema=1、USD / 百万 tokens 的价格表')
    if type(data.get('allow_assumptions')) is not bool or not isinstance(data.get('rates'), list):
        raise ValueError('价格表结构错误')
    clean = {'schema': 1, 'currency': 'USD', 'allow_assumptions': data['allow_assumptions'],
             'notice': text(data.get('notice'), limit=500), 'rates': []}
    seen = set()
    if len(data['rates']) > 500:
        raise ValueError('价格表最多 500 个供应商/模型组合')
    for row in data['rates']:
        if not isinstance(row, dict):
            raise ValueError('价格项目必须为对象')
        provider, model = text(row.get('provider')), text(row.get('model'))
        if not provider or not model or (provider, model) in seen:
            raise ValueError('供应商/模型不能为空或重复；模型按完整 ID 匹配')
        seen.add((provider, model))
        threshold = row.get('long_threshold', 0)
        if type(threshold) is not int or threshold < 0 or threshold > 100_000_000:
            raise ValueError('长上下文门槛必须为非负整数，0 表示不分档')
        tiers = row.get('tiers', {'standard': '1'})
        if not isinstance(tiers, dict) or 'standard' not in tiers or len(tiers) > 20:
            raise ValueError('tiers 必须含 standard')
        rate = {'provider': provider, 'model': model, 'long_threshold': threshold,
                'long_input': str(decimal(row.get('long_input', 1))),
                'long_output': str(decimal(row.get('long_output', 1))),
                'tiers': {text(k): str(decimal(v)) for k, v in tiers.items()},
                'source': text(row.get('source'), '用户设置', 1000), 'checked': text(row.get('checked'))}
        for field in RATE_FIELDS:
            rate[field] = str(decimal(row[field])) if row.get(field) is not None else None
        clean['rates'].append(rate)
    return clean


class PriceStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.path = self.directory / 'prices.json'

    def load(self) -> dict:
        if not self.path.exists():
            return default_prices()
        if self.path.is_symlink() or self.path.stat().st_size > 512_000:
            raise ValueError('价格文件类型或体积不受支持')
        return validate_prices(json.loads(self.path.read_text(encoding='utf-8')))

    @staticmethod
    def fingerprint(data: dict) -> str:
        return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def save(self, data: dict, expected: dict) -> dict:
        from sync import locks
        ensure_private(self.directory)
        with locks([self.path]):
            return self._save_locked(data, expected)

    def _save_locked(self, data: dict, expected: dict) -> dict:
        clean = validate_prices(data)
        if self.fingerprint(self.load()) != self.fingerprint(expected):
            raise ValueError('价格表被其他窗口修改，请重新打开后再保存')
        ensure_private(self.directory)
        if self.path.exists():
            backup = self.directory / ('prices-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '.json')
            with backup.open('xb') as stream:
                stream.write(self.path.read_bytes())
        fd, name = tempfile.mkstemp(prefix='.prices-', dir=self.directory)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(clean, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        result = self.load()
        if result != clean:
            raise ValueError('价格表读回不一致')
        return result


@dataclass(frozen=True)
class Quote:
    amount: Decimal | None
    components: dict
    notes: tuple[str, ...]
    rate_id: str = ''


def quote(req: Request, prices: dict) -> Quote:
    rate = next((r for r in prices['rates'] if r['provider'] == req.provider and r['model'] == req.model), None)
    if not rate:
        return Quote(None, {}, ('未配置此供应商/模型价格',))
    if req.usage is None:
        return Quote(None, {}, ('请求未报告 Token 用量',))
    u = req.usage
    notes = []
    if not req.cache_known:
        notes.append('缓存拆分缺失：按非缓存输入估算')
    tier = req.service_tier.lower()
    tier = {'default': 'standard', 'priority': 'fast'}.get(tier, tier)
    if not tier:
        notes.append('处理档位缺失：按 Standard 估算')
        tier = 'standard'
    if tier not in rate['tiers']:
        return Quote(None, {}, ('未知处理档位：' + tier,))
    retention = req.retention
    split_known = req.cache_write_30m is not None and req.cache_write_12h is not None
    if u['cacheCreationTokens'] and not split_known and not retention:
        notes.append('缓存保留期缺失：按 30m 估算')
        retention = '30m'
    if u['cacheCreationTokens'] and not split_known and retention not in ('30m', '12h'):
        return Quote(None, {}, ('不支持的缓存写入保留期：' + retention,))
    if not req.region:
        notes.append('地域缺失：按全球基础价估算')
    elif req.region not in ('global', 'regional', 'fedramp'):
        return Quote(None, {}, ('未知地域费率：' + req.region,))
    if notes and not prices['allow_assumptions']:
        return Quote(None, {}, tuple(notes) + ('严格模式不代填计费条件',))
    input_total = u['inputTokens'] + u['cacheReadTokens'] + u['cacheCreationTokens']
    long = bool(rate.get('long_threshold') and input_total > rate['long_threshold'])
    factor = decimal(rate['tiers'][tier])
    if req.region in ('regional', 'fedramp'):
        # Explicit data only. Custom provider rates must already include uplifts.
        if req.provider != 'openai':
            return Quote(None, {}, ('自定义供应商地域费率需单独配置为模型价格',))
        factor *= Decimal('1.10')
        notes.append('已计入地域 10% 溢价')
    if long:
        notes.append('按全请求长上下文档计价')
    parts = {}
    for label, key, price_key in (
        ('非缓存输入', 'inputTokens', 'input'), ('缓存读取', 'cacheReadTokens', 'cached'),
        ('缓存写入', 'cacheCreationTokens', 'write_12h' if retention == '12h' else 'write_30m'),
        ('输出（含推理）', 'outputTokens', 'output'),
    ):
        tokens = u[key]
        if key == 'cacheCreationTokens' and split_known:
            multiplier = decimal(rate['long_input']) if long else Decimal(1)
            for lifetime, count in (('30m', req.cache_write_30m), ('12h', req.cache_write_12h)):
                key_price = 'write_' + lifetime
                if count and rate.get(key_price) is None:
                    return Quote(None, {}, ('价格缺失：' + key_price,))
                parts['缓存写入 ' + lifetime] = Decimal(count) * decimal(rate.get(key_price) or '0') * factor * multiplier / MILLION
            continue
        if tokens and rate.get(price_key) is None:
            return Quote(None, {}, ('价格缺失：' + price_key,))
        multiplier = decimal(rate['long_output'] if key == 'outputTokens' else rate['long_input']) if long else Decimal(1)
        parts[label] = Decimal(tokens) * decimal(rate.get(price_key) or '0') * factor * multiplier / MILLION
    return Quote(sum(parts.values(), Decimal(0)), parts, tuple(notes), PriceStore.fingerprint(rate)[:12])


def fixed_timezone(label: str):
    if label == '本机时区':
        # Converts each timestamp via astimezone() below for OS DST rules.
        return None
    if label in ('UTC', 'UTC+00:00'):
        return timezone.utc
    import re
    found = re.fullmatch(r'UTC([+-])(\d{2}):(\d{2})', label)
    if not found:
        raise ValueError('选择本机时区、UTC 或 UTC±HH:MM')
    hours, mins = int(found[2]), int(found[3])
    if hours > 14 or mins > 59 or (hours == 14 and mins):
        raise ValueError('时区偏移超出范围')
    return timezone(timedelta(minutes=(hours*60+mins)*(1 if found[1]=='+' else -1)))


def local_date(req: Request, tz_label: str) -> date:
    return timestamp(req.time).astimezone(fixed_timezone(tz_label)).date()


def period_bounds(kind, now=None, tz_label='本机时区'):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('当前时间必须带时区')
    now = now.astimezone(timezone.utc)
    if kind == '全部':
        return None, None
    if kind == '近 24 小时':
        return now - timedelta(hours=24), now
    offsets = {'今天': 0, '近 7 天': 6, '近 30 天': 29}
    if kind not in offsets:
        raise ValueError('未识别的时间范围')
    tz = fixed_timezone(tz_label)
    first = now.astimezone(tz).date() - timedelta(days=offsets[kind])
    # For the OS local timezone, astimezone() resolves historical DST at midnight.
    midnight = datetime.combine(first, datetime.min.time(), tzinfo=tz)
    return midnight.astimezone(timezone.utc), now


def filter_requests(rows, *, start: date | None = None, end: date | None = None,
                    start_at: datetime | None = None, end_at: datetime | None = None,
                    tz_label='本机时区', provider='', model='', cwd='', thread_id='',
                    app_project_id='', unassigned_only=False):
    if start is not None and end is not None and (start > end or (end-start).days > 3660):
        raise ValueError('日期范围需按先后排列且不超过十年')
    for boundary in (start_at, end_at):
        if boundary is not None and (boundary.tzinfo is None or boundary.utcoffset() is None):
            raise ValueError('时间范围必须带时区')
    if start_at is not None and end_at is not None and start_at > end_at:
        raise ValueError('时间范围需按先后排列')
    tz = fixed_timezone(tz_label)
    selected = []
    for r in rows:
        t = timestamp(r.time)
        day = t.astimezone(tz).date() if start is not None or end is not None else None
        if ((start is None or day >= start) and (end is None or day <= end)
                and (start_at is None or t >= start_at) and (end_at is None or t < end_at)
                and (not provider or r.provider == provider) and (not model or r.model == model)
                and (not cwd or path_key(r.cwd) == path_key(cwd)) and (not thread_id or r.thread_id == thread_id)
                and (not app_project_id or r.app_project_id == app_project_id)
                and (not unassigned_only or not r.app_project_id)):
            selected.append(r)
    return selected


def summary(rows: list[Request], prices: dict, *, mode='estimate', quote_cache=None) -> dict:
    if quote_cache is None:
        quotes = [quote(r, prices) for r in rows]
    else:
        quotes = []
        for r in rows:
            key = id(r)
            if key not in quote_cache:
                quote_cache[key] = quote(r, prices)
            quotes.append(quote_cache[key])
    costs = [q.amount for q in quotes] if mode == 'estimate' else [decimal(r.reported_cost_usd) if r.reported_cost_usd is not None else None for r in rows]
    usages = [r.usage for r in rows if r.usage is not None]
    known = [v for v in costs if v is not None]
    cache_usages = [r.usage for r in rows if r.usage is not None and r.cache_known]
    total_input = sum(u['inputTokens']+u['cacheReadTokens']+u['cacheCreationTokens'] for u in cache_usages)
    cached = sum(u['cacheReadTokens'] for u in cache_usages)
    statuses = [r.status for r in rows if r.status != 'unknown']
    speed = [r.tokens_per_second for r in rows if r.tokens_per_second is not None]
    ttft = [r.ttft_ms for r in rows if r.ttft_ms is not None]
    # Missing cache-write fields normalize to zero, so their sum is a lower bound.
    return {'requests': len(rows), 'cost': sum(known, Decimal(0)) if known else None,
            'priced': len(known), 'unpriced': len(rows)-len(known),
            'tokens': sum(u['totalTokens'] for u in usages) if usages else None,
            'input': total_input, 'cached': cached, 'usage_known': len(usages),
            'new_input': sum(u['inputTokens'] for u in cache_usages) if cache_usages else None,
            'output': sum(u['outputTokens'] for u in usages) if usages else None,
            'cache_write': sum(u['cacheCreationTokens'] for u in usages) if usages else None,
            'cache_read': cached if cache_usages else None,
            'cache_hit': 100*cached/total_input if total_input else None, 'cache_known': len(cache_usages),
            'success': 100*statuses.count('success')/len(statuses) if statuses else None,
            'status_known': len(statuses), 'speed': sum(speed)/len(speed) if speed else None,
            'speed_known': len(speed), 'ttft': sum(ttft)/len(ttft) if ttft else None,
            'ttft_known': len(ttft), 'assumed': sum(q.amount is not None and any('缺失' in n for n in q.notes) for q in quotes),
            'quotes': quotes, 'costs': costs}


def project_identity(req):
    if req.app_project_id:
        return 'project:' + req.app_project_id
    if req.cwd and (ntpath.isabs(req.cwd) or posixpath.isabs(req.cwd)):
        return 'cwd:' + path_key(req.cwd)
    return 'unassigned'


def group_requests(rows, key, prices, mode='estimate', quote_cache=None):
    groups = defaultdict(list)
    for r in rows:
        if key == 'model':
            identity = (r.provider, r.model)
        elif key == 'project':
            identity = project_identity(r)
        elif key == 'session':
            identity = (r.thread_id, r.title)
        else:
            identity = r.provider
        groups[identity].append(r)
    result = []
    for identity, items in groups.items():
        if key == 'project':
            first = items[0]
            label = first.project if identity != 'unassigned' else '未归类'
            identity = (identity, label)
        result.append((identity, summary(items, prices, mode=mode, quote_cache=quote_cache)))
    return result


def daily(rows, start, end, tz_label, prices, mode='estimate', quote_cache=None):
    groups = defaultdict(list)
    tz = fixed_timezone(tz_label)
    for r in rows:
        groups[timestamp(r.time).astimezone(tz).date()].append(r)
    # Keep the final axis readable; aggregate to week/month for longer windows.
    period = 'day' if (end-start).days < 62 else 'week' if (end-start).days < 370 else 'month'
    bins = defaultdict(list)
    d = start
    while d <= end:
        key = d if period == 'day' else d-timedelta(days=d.weekday()) if period == 'week' else d.replace(day=1)
        bins[key].extend(groups[d])
        d += timedelta(days=1)
    return [(key.isoformat(), summary(items, prices, mode=mode, quote_cache=quote_cache)) for key, items in sorted(bins.items())], period


def period_buckets(rows, kind, now, tz_label, prices, mode='estimate', quote_cache=None):
    """Aggregate pre-filtered requests; hour keys retain their local UTC offset."""
    start, end = period_bounds(kind, now, tz_label)
    tz = fixed_timezone(tz_label)
    if kind in ('今天', '近 24 小时'):
        bins = defaultdict(list)
        for r in rows:
            seconds = (timestamp(r.time) - start).total_seconds()
            if seconds >= 0:
                bins[int(seconds // 3600)].append(r)
        count = max(0, math.ceil((end - start).total_seconds() / 3600))
        result = []
        for i in range(count):
            key = (start + timedelta(hours=i)).astimezone(tz).isoformat()
            result.append((key, summary(bins[i], prices, mode=mode, quote_cache=quote_cache)))
        return result, 'hour'
    bins = defaultdict(list)
    for r in rows:
        bins[local_date(r, tz_label)].append(r)
    if kind != '全部':
        first = start.astimezone(tz).date()
        last = end.astimezone(tz).date()
        while first <= last:
            bins[first]
            first += timedelta(days=1)
    return [(day.isoformat(), summary(items, prices, mode=mode, quote_cache=quote_cache))
            for day, items in sorted(bins.items())], 'day'


def money(value, decimals=2):
    return '—' if value is None else f'${value:,.{decimals}f}'


def short_number(value):
    if value is None:
        return '—'
    for divisor, unit in ((1e9,'B'), (1e6,'M'), (1e3,'K')):
        if value >= divisor:
            return f'{value/divisor:,.2f}'.rstrip('0').rstrip('.')+unit
    return f'{value:,}'


def export_csv(rows, prices, mode='estimate') -> str:
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(['UTC时间','供应商','模型','项目','聊天ID','请求ID','角色','非缓存输入','缓存读取','缓存写入','输出含推理','推理子集','总Tokens','写入30m子集','写入12h子集','USD金额','金额口径','状态','速度tok_s','首字ms','单价版本','估算说明'])
    def safe(value):
        val = str(value) if value is not None else ''
        return "'"+val if val[:1] in ('=','+','-','@','\t','\r') else val
    for r in rows:
        q=quote(r,prices)
        u=r.usage or {}
        amount=q.amount if mode=='estimate' else r.reported_cost_usd
        writer.writerow([safe(v) for v in [r.time,r.provider,r.model,r.project,r.thread_id,r.response_id,r.role,
            *[u.get(k) for k in ('inputTokens','cacheReadTokens','cacheCreationTokens','outputTokens','reasoningOutputTokens','totalTokens')],
            r.cache_write_30m, r.cache_write_12h, str(amount) if amount is not None else '', '当前价格表估算' if mode=='estimate' else '来源报告USD（未对账）',
            r.status,r.tokens_per_second,r.ttft_ms,q.rate_id,'；'.join(q.notes)]])
    return '\ufeff'+output.getvalue()
