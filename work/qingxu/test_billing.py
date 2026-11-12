"""Money/denominator/date tests use explicit synthetic requests, never a live bill."""
from datetime import date
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest

from billing import *


def request(**updates):
    raw=dict(response_id='response-1',time='2026-11-11T01:00:00+00:00',provider='openai',model='gpt-6.1-sol',
        thread_id='t1',project='演示项目',cwd='/project',title='演示会话',role='primary',
        usage=dict(input_tokens=10000,cached_input_tokens=8000,cache_write_input_tokens=1000,
                   output_tokens=1000,reasoning_output_tokens=700,total_tokens=11000),
        service_tier='standard',retention='30m',region='global')
    raw.update(updates)
    return Request.from_dict(raw)


class BillingMathTests(unittest.TestCase):
    def setUp(self):self.prices=validate_prices(default_prices())

    def test_project_identity_groups_worktrees_and_keeps_unrelated_names_separate(self):
        first = replace(request(), app_project_id='p1')
        worktree = replace(first, response_id='r2', cwd='/other/worktree', project='项目改名')
        same_name = replace(first, response_id='r3', app_project_id='p2')
        unassigned = replace(first, response_id='r4', app_project_id='')
        rows = [first, worktree, same_name, unassigned]
        period = dict(start=date(2026,11,1), end=date(2026,11,30), tz_label='UTC')
        self.assertEqual(len(filter_requests(rows, **period, app_project_id='p1')), 2)
        self.assertEqual(filter_requests(rows, **period, cwd=first.cwd, unassigned_only=True), [unassigned])
        groups = group_requests(rows, 'project', self.prices)
        self.assertEqual(sorted(s['requests'] for _, s in groups), [1, 1, 2])

    def test_old_serialized_requests_stay_compatible(self):
        original = request()
        legacy = original.as_dict()
        self.assertNotIn('app_project_id', legacy)
        self.assertEqual(Request.from_dict(legacy, normalized=True), original)
        self.assertEqual(request(app_project_id='untrusted').app_project_id, '')

    def test_unknown_project_without_directory_is_not_named_after_first_import(self):
        first = replace(request(), cwd='.', project='项目甲')
        second = replace(first, response_id='r2', cwd='', project='项目乙')
        third = replace(first, response_id='r3', cwd='relative/path', project='项目丙')
        groups = group_requests([first, second, third], 'project', self.prices)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0][0][1], '未归类')
        self.assertEqual(groups[0][1]['requests'], 3)
    def test_cost_components_and_reasoning_not_double_billed(self):
        r=request();q=quote(r,self.prices)
        self.assertEqual(q.amount,Decimal('0.0153'))
        self.assertEqual(q.components['输出（含推理）'],Decimal('0.01'))
        self.assertEqual(sum(q.components.values()),q.amount)
    def test_cache_write_may_be_excluded_from_raw_input(self):
        raw=dict(input_tokens=9000,cached_input_tokens=8000,cache_write_input_tokens=1000,output_tokens=1000,total_tokens=11000)
        self.assertEqual(quote(request(usage=raw),self.prices).amount,Decimal('0.0153'))
    def test_api_usage_nested_shape(self):
        u=canonical_usage(dict(input_tokens=10,output_tokens=5,total_tokens=15,input_tokens_details={'cached_tokens':8},output_tokens_details={'reasoning_tokens':3}))
        self.assertEqual(u['inputTokens'],2);self.assertEqual(u['reasoningOutputTokens'],3)
    def test_chat_completion_shape(self):
        u=canonical_usage(dict(prompt_tokens=10,completion_tokens=5,total_tokens=15,prompt_tokens_details={'cached_tokens':6}))
        self.assertEqual(u['cacheReadTokens'],6)
    def test_invalid_usage_not_clamped(self):
        for u in ({'input_tokens':1,'output_tokens':2,'total_tokens':999},
                  {'input_tokens':1,'output_tokens':2,'reasoning_output_tokens':3,'total_tokens':3},
                  {'input_tokens':True,'output_tokens':2,'total_tokens':3},
                  {'input_tokens':1,'cached_input_tokens':8,'output_tokens':2,'total_tokens':3}):
            with self.subTest(u=u),self.assertRaises(ValueError):canonical_usage(u)
    def test_unknown_provider_not_matched_by_model_name(self):
        self.assertIsNone(quote(request(provider='proxy'),self.prices).amount)
    def test_unknown_model_not_silently_zero(self):
        q=quote(request(model='gpt-6.1-sol-custom'),self.prices)
        self.assertIsNone(q.amount)
    def test_zero_is_a_real_zero(self):
        r=request(usage=dict(input_tokens=0,output_tokens=0,cached_input_tokens=0,total_tokens=0))
        self.assertEqual(quote(r,self.prices).amount,0)
        self.assertIsNone(summary([r],self.prices)['cache_hit'])
    def test_long_context_applies_to_entire_request(self):
        r=request(usage=dict(input_tokens=300000,cached_input_tokens=200000,cache_write_input_tokens=50000,output_tokens=1000,total_tokens=301000))
        self.assertEqual(quote(r,self.prices).amount,Decimal('0.505'))
    def test_long_threshold_excludes_output(self):
        r=request(usage=dict(input_tokens=272000,cached_input_tokens=0,output_tokens=10000,total_tokens=282000))
        self.assertEqual(quote(r,self.prices).amount,Decimal('0.644'))
    def test_tier_and_retention_multipliers(self):
        q=quote(request(service_tier='fast',retention='12h'),self.prices)
        self.assertEqual(q.amount,Decimal('0.0356'))
    def test_regional_price(self):
        self.assertEqual(quote(request(region='regional'),self.prices).amount,Decimal('0.01683'))
    def test_strict_missing_conditions(self):
        p=self.prices|{'allow_assumptions':False}
        self.assertIsNone(quote(request(service_tier=''),p).amount)
        self.assertIsNotNone(quote(request(),p).amount)
    def test_assumptions_are_disclosed(self):
        q=quote(request(service_tier='',region='',retention=''),self.prices)
        self.assertEqual(len(q.notes),3)
    def test_unknown_tier_does_not_become_standard(self):
        self.assertIsNone(quote(request(service_tier='mystery'),self.prices).amount)
    def test_unsupported_retention_not_priced_as_zero(self):
        self.assertIsNone(quote(request(retention='24h'),self.prices).amount)
    def test_missing_write_price_only_matters_when_used(self):
        p=self.prices;p['rates'][1]['write_30m']=None
        self.assertIsNone(quote(request(),p).amount)
        self.assertIsNotNone(quote(request(usage=dict(input_tokens=100,output_tokens=10,total_tokens=110)),p).amount)
    def test_partial_totals_keep_missing_coverage(self):
        s=summary([request(),request(response_id='r2',model='missing')],self.prices)
        self.assertEqual(s['priced'],1);self.assertEqual(s['unpriced'],1)
        self.assertEqual(s['cost'],Decimal('0.0153'))
    def test_all_missing_is_unknown_not_zero(self):
        self.assertIsNone(summary([request(model='missing')],self.prices)['cost'])
        self.assertIsNone(summary([],self.prices)['tokens'])
    def test_cache_is_weighted_by_input_not_request_percent(self):
        r2=request(response_id='r2',usage=dict(input_tokens=1000,cached_input_tokens=0,output_tokens=10000,total_tokens=11000))
        s=summary([request(),r2],self.prices)
        self.assertAlmostEqual(s['cache_hit'],8000/11000*100)
    def test_missing_cache_split_excluded_from_cache_ratio(self):
        r=request(usage=dict(input_tokens=100,output_tokens=50,total_tokens=150))
        self.assertFalse(r.cache_known)
        self.assertIsNone(summary([r],self.prices)['cache_hit'])
        self.assertIn('缓存拆分缺失',quote(r,self.prices).notes[0])

    def test_usage_component_totals_distinguish_unknown_zero_and_partial(self):
        known=request()
        unknown=request(response_id='unknown',usage=dict(input_tokens=100,output_tokens=50,total_tokens=150))
        unknown_write=request(response_id='unknown-write',usage=dict(input_tokens=100,cache_write_input_tokens=30,output_tokens=50,total_tokens=150))
        missing=request(response_id='missing',usage=None)
        zero=request(response_id='zero',usage=dict(input_tokens=0,cached_input_tokens=0,output_tokens=0,total_tokens=0))
        self.assertFalse(unknown_write.cache_known)
        self.assertEqual(unknown_write.usage['cacheCreationTokens'],30)
        cases=(('empty',[],(None,None,None,None),(0,0)),
               ('missing',[missing],(None,None,None,None),(0,0)),
               ('unknown cache',[unknown],(None,50,0,None),(1,0)),
               ('unknown cache with writes',[unknown_write],(None,50,30,None),(1,0)),
               ('known zero',[zero],(0,0,0,0),(1,1)),
               ('known split',[known],(1000,1000,1000,8000),(1,1)),
               ('mixed',[known,unknown_write,missing,zero],(1000,1050,1030,8000),(3,2)))
        for name,rows,expected,coverage in cases:
            with self.subTest(name=name):
                result=summary(rows,self.prices)
                self.assertEqual(tuple(result[k] for k in ('new_input','output','cache_write','cache_read')),expected)
                self.assertEqual((result['usage_known'],result['cache_known']),coverage)
        mixed=summary([known,unknown_write,missing,zero],self.prices)
        self.assertEqual((mixed['input'],mixed['cached'],mixed['cache_hit']),(10000,8000,80.0))
    def test_no_status_or_timings_inferred(self):
        s=summary([request()],self.prices)
        self.assertIsNone(s['success']);self.assertIsNone(s['speed']);self.assertIsNone(s['ttft'])
    def test_success_denominator_is_known_attempts(self):
        s=summary([request(status='success'),request(response_id='2',status='error'),request(response_id='3')],self.prices)
        self.assertEqual(s['success'],50);self.assertEqual(s['status_known'],2)
    def test_reported_cost_does_not_fallback_to_estimate(self):
        s=summary([request(reported_cost_usd='0.5'),request(response_id='2')],self.prices,mode='reported')
        self.assertEqual(s['cost'],Decimal('.5'));self.assertEqual(s['unpriced'],1)
    def test_nan_infinite_negative_money_rejected(self):
        for x in ('NaN','inf','-1',True):
            with self.subTest(x=x),self.assertRaises(ValueError):decimal(x)
    def test_naive_timestamp_rejected(self):
        with self.assertRaises(ValueError):request(time='2026-11-11T01:00:00')
    def test_timing_consistency(self):
        with self.assertRaises(ValueError):request(ttft_ms=500,duration_ms=100)
    def test_exact_ids_per_provider_in_groups(self):
        groups=group_requests([request(),request(provider='proxy')],'model',self.prices)
        self.assertEqual(len(groups),2)
    def test_csv_formula_escape_and_full_precision(self):
        csv=export_csv([request(project='=SUM(A1)')],self.prices)
        self.assertIn("'=SUM(A1)",csv);self.assertIn('0.0153',csv)
    def test_sum_without_per_request_rounding(self):
        rows=[request(response_id=str(i),usage=dict(input_tokens=1,cached_input_tokens=0,output_tokens=0,total_tokens=1)) for i in range(100)]
        self.assertEqual(summary(rows,self.prices)['cost'],Decimal('.0002'))
    def test_timezone_at_date_boundary(self):
        r=request(time='2026-11-10T16:30:00Z')
        for zone,count in [('UTC',0),('UTC+08:00',1),('UTC+09:00',1)]:
            self.assertEqual(len(filter_requests([r],start=date(2026,11,11),end=date(2026,11,11),tz_label=zone)),count)
    def test_filter_all_dimensions(self):
        r=request()
        self.assertEqual(len(filter_requests([r],start=date(2026,11,1),end=date(2026,11,30),provider='openai',model=r.model,cwd=r.cwd,thread_id='t1')),1)
        self.assertFalse(filter_requests([r],start=date(2026,11,1),end=date(2026,11,30),provider='x'))
    def test_invalid_date_range(self):
        with self.assertRaises(ValueError):filter_requests([],start=date(2026,11,12),end=date(2026,11,11))
    def test_daily_reconciles_with_total(self):
        rows=[request(),request(response_id='2',time='2026-11-09T00:00:00Z')]
        days,period=daily(rows,date(2026,11,5),date(2026,11,11),'UTC',self.prices)
        self.assertEqual(len(days),7);self.assertEqual(period,'day')
        self.assertEqual(sum((s['cost'] or Decimal(0)) for _,s in days),summary(rows,self.prices)['cost'])
    def test_no_usage_is_not_zero(self):
        r=request(usage=None,status='error')
        self.assertIsNone(quote(r,self.prices).amount);self.assertEqual(summary([r],self.prices)['requests'],1)
    def test_currency_contract(self):
        with self.assertRaises(ValueError):validate_prices(self.prices|{'currency':'CNY'})
    def test_price_save_readback_backup_and_conflict(self):
        with tempfile.TemporaryDirectory() as d:
            store=PriceStore(Path(d));initial=store.load();changed=json.loads(json.dumps(initial))
            changed['rates'][0]['input']='11'
            store.save(changed,initial)
            self.assertEqual(store.load()['rates'][0]['input'],'11')
            changed2=json.loads(json.dumps(changed));changed2['rates'][0]['input']='12'
            store.save(changed2,changed)
            self.assertEqual(len(list(Path(d).glob('prices-*.json'))),1)
            with self.assertRaises(ValueError):store.save(changed,initial)
    def test_duplicate_prices_rejected(self):
        self.prices['rates'].append(self.prices['rates'][0])
        with self.assertRaises(ValueError):validate_prices(self.prices)

    def test_mixed_cache_lifetimes_split_before_price(self):
        raw = dict(input_tokens=12000, output_tokens=1000, total_tokens=13000,
                   input_tokens_details={'cached_tokens':4000, 'cache_write_tokens':7000,
                       'cache_write_tokens_details':{'ttl_30m_tokens':3000, 'ttl_12h_tokens':4000}})
        r = request(usage=raw, retention='')
        q = quote(r, self.prices | {'allow_assumptions':False})
        self.assertEqual(q.amount, Decimal('0.0399'))
        self.assertEqual(q.components['缓存写入 30m'], Decimal('.0075'))
        self.assertEqual(q.components['缓存写入 12h'], Decimal('.02'))
        self.assertNotIn('缓存写入', q.components)
        self.assertEqual(Request.from_dict(r.as_dict(), normalized=True), r)

    def test_bad_cache_lifetime_splits_rejected(self):
        for split in ({'ttl_30m_tokens':1}, {'ttl_30m_tokens':-1,'ttl_12h_tokens':1},
                      {'ttl_30m_tokens':1,'ttl_12h_tokens':True}):
            with self.subTest(split=split), self.assertRaises(ValueError):
                request(usage=dict(input_tokens=10,output_tokens=0,total_tokens=10,
                    input_tokens_details={'cache_write_tokens_details':split}))
        with self.assertRaises(ValueError):
            request(usage=dict(input_tokens=10,output_tokens=0,total_tokens=10,
                input_tokens_details={'cache_write_tokens':2,'cache_write_tokens_details':{'ttl_30m_tokens':2,'ttl_12h_tokens':1}}))

    def test_reported_amount_cannot_claim_another_currency(self):
        with self.assertRaises(ValueError):request(reported_cost_usd='5',currency='CNY')

    def test_oversized_usage_rejected(self):
        with self.assertRaises(ValueError):
            canonical_usage(dict(input_tokens=2**64,output_tokens=0,total_tokens=2**64))

    def test_long_context_surcharge_is_not_missing_data_assumption(self):
        r=request(usage=dict(input_tokens=300000,cached_input_tokens=250000,output_tokens=0,total_tokens=300000))
        self.assertEqual(summary([r],self.prices)['assumed'],0)


if __name__=='__main__':unittest.main()
