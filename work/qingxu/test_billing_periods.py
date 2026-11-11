"""Time ranges and aggregation with synthetic request metadata."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import runpy
from pathlib import Path

import billing
from billing import daily, filter_requests, group_requests, period_bounds, period_buckets, summary
from test_billing import request


UTC = timezone.utc


class PeriodTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 12, 0, 30, tzinfo=UTC)
        self.prices = billing.default_prices()

    def test_bounds_local_midnight_and_calendar_days(self):
        start, end = period_bounds('今天', self.now, 'UTC+08:00')
        self.assertEqual(start, datetime(2026, 11, 11, 16, tzinfo=UTC))
        self.assertEqual(end, self.now)
        self.assertEqual(period_bounds('近 7 天', self.now, 'UTC+08:00')[0],
                         datetime(2026, 11, 5, 16, tzinfo=UTC))
        self.assertEqual(period_bounds('近 30 天', self.now, 'UTC+08:00')[0],
                         datetime(2026, 10, 13, 16, tzinfo=UTC))
        self.assertEqual(period_bounds('全部', self.now), (None, None))

    def test_24_hours_exact_half_open_boundaries(self):
        start, end = period_bounds('近 24 小时', self.now, 'UTC')
        self.assertEqual(end-start, timedelta(hours=24))
        rows = [request(response_id='before', time=(start-timedelta(microseconds=1)).isoformat()),
                request(response_id='first', time=start.isoformat()),
                request(response_id='last', time=(end-timedelta(microseconds=1)).isoformat()),
                request(response_id='after', time=end.isoformat())]
        selected = filter_requests(rows, start_at=start, end_at=end, tz_label='UTC')
        self.assertEqual([r.response_id for r in selected], ['first', 'last'])
        buckets, kind = period_buckets(selected, '近 24 小时', self.now, 'UTC', self.prices)
        self.assertEqual((len(buckets), kind), (24, 'hour'))
        self.assertEqual(buckets[0][1]['requests'], 1)
        self.assertEqual(buckets[-1][1]['requests'], 1)
        self.assertEqual(sum(s['requests'] for _,s in buckets), 2)

    def test_full_history_has_no_date_limit_and_uses_daily_bins(self):
        rows = [request(response_id='old', time='2001-01-01T00:00:00Z'),
                request(response_id='new', time='2026-11-11T23:59:59Z')]
        self.assertEqual(filter_requests(rows), rows)
        buckets, kind = period_buckets(rows, '全部', self.now, 'UTC', self.prices)
        self.assertEqual(kind, 'day')
        self.assertEqual([day for day,_ in buckets], ['2001-01-01', '2026-11-11'])
        with self.assertRaises(ValueError):
            filter_requests(rows, start=date(2001,1,1), end=date(2026,11,12))

    def test_seven_and_thirty_days_fill_empty_and_reconcile(self):
        rows = [request(response_id='a', time='2026-11-10T12:00:00Z'),
                request(response_id='b', time='2026-11-11T12:00:00Z')]
        for period, count in (('近 7 天', 7), ('近 30 天', 30)):
            start,end = period_bounds(period, self.now, 'UTC')
            selected = filter_requests(rows, start_at=start, end_at=end, tz_label='UTC')
            buckets, kind = period_buckets(selected, period, self.now, 'UTC', self.prices)
            self.assertEqual((len(buckets),kind),(count,'day'))
            total = summary(selected,self.prices)
            self.assertEqual(sum(s['requests'] for _,s in buckets),total['requests'])
            self.assertEqual(sum((s['cost'] or Decimal(0)) for _,s in buckets),total['cost'])
            self.assertEqual(sum((s['tokens'] or 0) for _,s in buckets),total['tokens'])
            self.assertTrue(any(not s['requests'] and s['cost'] is None for _,s in buckets))

    def test_unpriced_and_cache_known_remain_distinct(self):
        rows = [request(response_id='known',time='2026-11-11T12:00:00Z'),
                request(response_id='unknown',time='2026-11-11T13:00:00Z',model='unpriced-model')]
        buckets,_=period_buckets(rows,'近 7 天',self.now,'UTC',self.prices)
        stats=next(s for day,s in buckets if day=='2026-11-11')
        self.assertEqual((stats['requests'],stats['priced'],stats['unpriced']),(2,1,1))
        self.assertEqual(stats['cache_hit'],summary(rows,self.prices)['cache_hit'])
        self.assertIsNotNone(stats['cost'])

    def test_quote_cache_shared_by_aggregations(self):
        r=request()
        cache={}
        original=billing.quote
        with patch('billing.quote',wraps=original) as quoted:
            total=summary([r],self.prices,quote_cache=cache)
            grouped=group_requests([r],'model',self.prices,quote_cache=cache)
            per_day,_=daily([r],date(2026,11,11),date(2026,11,11),'UTC',self.prices,quote_cache=cache)
            per_period,_=period_buckets([r],'全部',self.now,'UTC',self.prices,quote_cache=cache)
        self.assertEqual(quoted.call_count,1)
        self.assertEqual(grouped[0][1]['cost'],total['cost'])
        self.assertEqual(per_day[0][1]['cost'],total['cost'])
        self.assertEqual(per_period[0][1]['cost'],total['cost'])

    def test_dst_repeated_hour_has_distinct_offset_keys(self):
        try:
            zone=ZoneInfo('America/New_York')
        except ZoneInfoNotFoundError:
            self.skipTest('timezone database unavailable')
        now=datetime(2026,11,1,8,0,tzinfo=UTC)
        rows=[request(response_id='first',time='2026-11-01T05:30:00Z'),
              request(response_id='second',time='2026-11-01T06:30:00Z')]
        with patch('billing.fixed_timezone',return_value=zone):
            buckets,kind=period_buckets(rows,'今天',now,'local',self.prices)
        occupied=[day for day,s in buckets if s['requests']]
        self.assertEqual(kind,'hour')
        self.assertEqual(len(occupied),2)
        self.assertTrue(occupied[0].endswith('-04:00'))
        self.assertTrue(occupied[1].endswith('-05:00'))

    def test_invalid_or_naive_time_ranges_rejected(self):
        with self.assertRaises(ValueError):period_bounds('近 7 天',datetime(2026,1,1))
        with self.assertRaises(ValueError):filter_requests([],start_at=datetime(2026,1,1))
        with self.assertRaises(ValueError):period_bounds('unknown',self.now)

    def test_demo_requests_remain_in_recent_week_just_after_midnight(self):
        fixture=runpy.run_path(str(Path(__file__).resolve().parents[2]/'tools/fixtures/billing_demo.py'))
        now=datetime(2026,11,12,0,5,tzinfo=timezone(timedelta(hours=8)))
        rows=[billing.Request.from_dict(value) for value in fixture['requests'](now)]
        start,end=period_bounds('近 7 天',now,'本机时区')
        self.assertEqual(len(filter_requests(rows,start_at=start,end_at=end)),84)


if __name__ == '__main__':
    unittest.main()
