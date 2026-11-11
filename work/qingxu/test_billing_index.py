from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from billing_index import BillingIndex,consume
from billing import Request

THREAD='12345678-1111-4111-8111-111111111111'
NOW='2026-11-11T01:00:00Z'

def meta():return dict(type='session_meta',timestamp=NOW,payload=dict(id=THREAD,cwd='/demo',model_provider='openai'))
def ctx(model='gpt-6.1-sol',turn='turn1'):
    return dict(type='turn_context',timestamp=NOW,payload=dict(turn_id=turn,model=model))
def event(identity='response1',**kwargs):
    payload=dict(response_id=identity,thread_id=THREAD,turn_id='turn1',usage=dict(input_tokens=1000,cached_input_tokens=700,output_tokens=10,total_tokens=1010))
    payload.update(kwargs)
    return dict(type='token_usage_record',timestamp=NOW,payload=payload)

def write(p,items,mode='w'):
    with p.open(mode,encoding='utf-8') as f:
        for item in items:f.write(json.dumps(item)+'\n')

class IndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.home=self.root/'home';(self.home/'sessions').mkdir(parents=True)
        self.log=self.home/'sessions'/'rollout-demo.jsonl';self.index=BillingIndex(self.root/'cache')
        write(self.log,[meta(),ctx(),event()])
    def scan(self):return self.index.scan(self.home)
    def test_request_record_and_context_model(self):
        result=self.scan();r=result['requests'][0]
        self.assertEqual(r.model,'gpt-6.1-sol');self.assertEqual(r.model_source,'turn_context')
    def test_no_snapshot_counted_as_request(self):
        write(self.log,[dict(type='event_msg',timestamp=NOW,payload=dict(type='token_count',info={'total_token_usage':{'total_tokens':9999999}}))],'a')
        r=self.scan();self.assertEqual(len(r['requests']),1);self.assertEqual(r['issues']['context_snapshots_ignored'],1)
    def test_duplicates_in_same_and_archived_copy(self):
        write(self.log,[event()],'a');(self.home/'archived_sessions').mkdir()
        (self.home/'archived_sessions'/'copy.jsonl').write_bytes(self.log.read_bytes())
        r=self.scan();self.assertEqual(len(r['requests']),1);self.assertEqual(r['issues']['duplicates'],3)
    def test_conflicting_request_removed_not_last_wins(self):
        write(self.log,[event(model='other')],'a')
        r=self.scan();self.assertFalse(r['requests']);self.assertEqual(r['issues']['conflicts_excluded'],1)
    def test_inherited_request_not_charged_to_child(self):
        write(self.log,[event('inherited',thread_id='parent')],'a')
        r=self.scan();self.assertEqual(len(r['requests']),1);self.assertEqual(r['issues']['inherited_or_unbound'],1)
    def test_inherited_meta_does_not_replace_owner(self):
        write(self.log,[dict(type='session_meta',timestamp=NOW,payload=dict(id='parent',model_provider='wrong')),event('r2')],'a')
        r=self.scan();self.assertEqual(len(r['requests']),2);self.assertEqual({x.provider for x in r['requests']},{'openai'})
    def test_model_switch_uses_matching_turn(self):
        write(self.log,[ctx('gpt-6-astra','turn2'),event('r2',turn_id='turn2'),event('r3',turn_id='turn1')],'a')
        data={r.response_id:r.model for r in self.scan()['requests']}
        self.assertEqual(data['r2'],'gpt-6-astra');self.assertEqual(data['r3'],'gpt-6.1-sol')
    def test_unknown_turn_model_not_just_latest_model(self):
        write(self.log,[event('r2',turn_id='not-seen')],'a')
        self.assertEqual(next(r for r in self.scan()['requests'] if r.response_id=='r2').model,'unknown')
    def test_append_incremental_no_rescan(self):
        first=self.scan();second=self.scan();self.assertEqual(second['issues']['bytes_read'],0)
        addition=json.dumps(event('r2'))+'\n'
        with self.log.open('a', encoding='utf-8', newline='') as f:f.write(addition)
        third=self.scan();self.assertEqual(len(third['requests']),2)
        self.assertEqual(third['issues']['bytes_read'],len(addition.encode()))
    def test_partial_line_waits_for_newline(self):
        self.scan();line=json.dumps(event('r2'))
        with self.log.open('a') as f:f.write(line[:-5])
        self.assertEqual(len(self.scan()['requests']),1)
        waiting=self.scan()
        self.assertEqual(waiting['issues']['bytes_read'],0)
        self.assertGreater(waiting['issues'].get('incomplete_files',0),0)
        with self.log.open('a') as f:f.write(line[-5:]+'\n')
        self.assertEqual(len(self.scan()['requests']),2)
    def test_rewrite_rescans_removes_deleted_requests(self):
        self.scan();write(self.log,[meta(),ctx(),event('new')])
        self.assertEqual([r.response_id for r in self.scan()['requests']],['new'])
    def test_deleted_file_not_left_in_totals(self):
        self.scan();self.log.unlink();self.assertEqual(len(self.scan()['requests']),0)
    def test_sensitive_message_body_not_saved(self):
        write(self.log,[dict(type='response_item',timestamp=NOW,payload={'role':'user','content':'PRIVATE_SECRET_NEVER_STORE'})],'a')
        self.scan();self.assertNotIn(b'PRIVATE_SECRET_NEVER_STORE',self.index.db.read_bytes())
    def test_symlink_not_followed(self):
        link=self.home/'sessions'/'link.jsonl'
        try:link.symlink_to(self.log)
        except OSError:self.skipTest('symlink permissions unavailable')
        self.assertEqual(self.scan()['issues']['unsafe_files'],1)
    def test_budget_reports_incomplete(self):
        r=self.index.scan(self.home,budget=100)
        self.assertGreater(r['issues'].get('incomplete_files',0),0)
        self.assertEqual(len(self.scan()['requests']),1)
    def test_cancel(self):
        import threading
        e=threading.Event();e.set()
        with self.assertRaises(InterruptedError):self.index.scan(self.home,stop=e)
    def imported(self,rid='import1',**extra):
        return dict(response_id=rid,time=NOW,provider='proxy',model='demo',thread_id=THREAD,
                    usage=dict(input_tokens=100,cached_input_tokens=60,output_tokens=20,total_tokens=120),**extra)
    def test_import_idempotence(self):
        p=self.root/'requests.json';p.write_text(json.dumps({'schema':'qingxu.requests.v1','requests':[self.imported()]}))
        self.assertEqual(self.index.import_file(p),1);self.assertEqual(self.index.import_file(p),0)
        self.assertEqual(len(self.scan()['requests']),2)

    def test_cached_records_resolve_current_project_identity_without_rescan(self):
        self.scan()
        database = self.home/'state.sqlite'
        with closing(sqlite3.connect(database)) as conn:
            conn.execute('CREATE TABLE threads (id TEXT, name TEXT, cwd TEXT, project_id TEXT, source TEXT)')
            conn.execute('CREATE TABLE projects (id TEXT, name TEXT)')
            conn.execute('INSERT INTO projects VALUES (?, ?)', ('project-a', '同名项目'))
            conn.execute('INSERT INTO threads VALUES (?, ?, ?, ?, ?)', (THREAD, '聊天', '/demo', 'project-a', 'user'))
            conn.commit()
        result = self.index.scan(self.home, database)
        self.assertEqual(result['issues']['bytes_read'], 0)
        self.assertEqual(result['requests'][0].app_project_id, 'project-a')
        with closing(sqlite3.connect(database)) as conn:
            conn.execute('UPDATE projects SET name=? WHERE id=?', ('新名称', 'project-a'))
            conn.commit()
        self.assertEqual(self.index.scan(self.home, database)['requests'][0].project, '新名称')

    def test_child_project_uses_only_unique_trusted_parent_without_rescan(self):
        children = ('child', 'other-project', 'multiple-parents', 'agent-parent', 'missing-child')
        for identity in children:
            header = meta()
            header['payload'].update(id=identity, agent_role='worker')
            write(self.home/'sessions'/f'{identity}.jsonl',
                  [header, ctx(), event('request-'+identity, thread_id=identity)])
        imported = self.imported('imported-child')
        imported.update(thread_id='child', role='child')
        input_file = self.root/'imported-child.json'
        input_file.write_text(json.dumps({'schema': 'qingxu.requests.v1', 'requests': [imported]}),
                              encoding='utf-8')
        self.assertEqual(self.index.import_file(input_file), 1)
        before = self.index.scan(self.home)
        self.assertTrue(all(not r.app_project_id for r in before['requests']))

        database = self.home/'state.sqlite'
        with closing(sqlite3.connect(database)) as conn:
            conn.executescript('''
                CREATE TABLE projects(id TEXT, name TEXT);
                CREATE TABLE threads(id TEXT, name TEXT, cwd TEXT, project_id TEXT,
                                     source TEXT, agent_role TEXT, thread_source TEXT);
                CREATE TABLE thread_spawn_edges(child_thread_id TEXT, parent_thread_id TEXT);
            ''')
            conn.executemany('INSERT INTO projects VALUES (?, ?)',
                             [('project-a', '甲项目'), ('project-b', '乙项目')])
            conn.executemany('INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?)', [
                ('parent-a', '父聊甲', '/parent-a', 'project-a', 'cli', None, 'user'),
                ('parent-b', '父聊乙', '/parent-b', 'project-b', 'cli', None, 'user'),
                ('worker-parent', '非人类父线程', '/worker', 'project-a', 'subagent', 'worker', None),
                ('child', '子聊', '/worktree', None, 'subagent', 'worker', None),
                ('other-project', '明确归属乙', '/other', 'project-b', 'subagent', 'worker', None),
                ('multiple-parents', '多父线程', '/shared', None, 'subagent', 'worker', None),
                ('agent-parent', '非人类父边', '/unassigned', None, 'subagent', 'worker', None),
            ])
            conn.executemany('INSERT INTO thread_spawn_edges VALUES (?, ?)', [
                ('child', 'parent-a'), ('other-project', 'parent-a'),
                ('multiple-parents', 'parent-a'), ('multiple-parents', 'parent-b'),
                ('agent-parent', 'worker-parent'), ('missing-child', 'parent-a'),
            ])
            conn.commit()

        result = self.index.scan(self.home, database)
        self.assertEqual(result['issues']['bytes_read'], 0)
        found = {r.response_id: r for r in result['requests']}
        self.assertEqual((found['request-child'].app_project_id, found['request-child'].project), ('project-a', '甲项目'))
        self.assertEqual((found['request-child'].title, found['request-child'].role, found['request-child'].cwd),
                         ('子聊', 'child', '/demo'))
        self.assertEqual(found['request-other-project'].app_project_id, 'project-b')
        self.assertEqual(found['request-multiple-parents'].app_project_id, '')
        self.assertEqual(found['request-agent-parent'].app_project_id, '')
        self.assertEqual(found['request-missing-child'].app_project_id, 'project-a')
        self.assertTrue(all(r.app_project_id == 'project-a' for r in result['requests']
                            if r.thread_id == 'child'))
        self.assertEqual(self.index.import_file(input_file), 0)

        isolated = self.root/'other-home'
        isolated.mkdir()
        isolated_index = BillingIndex(self.root/'other-cache')
        self.assertFalse(isolated_index.scan(isolated, database)['requests'])

    def test_import_validation_atomic(self):
        p=self.root/'requests.json';p.write_text(json.dumps({'schema':'qingxu.requests.v1','requests':[self.imported(),{'response_id':'broken'}]}))
        with self.assertRaises(ValueError):self.index.import_file(p)
        self.assertEqual(len(self.scan()['requests']),1)
    def test_import_conflict_rolls_back_all(self):
        p=self.root/'requests.json';p.write_text(json.dumps({'schema':'qingxu.requests.v1','requests':[self.imported()]}));self.index.import_file(p)
        changed=self.imported();changed['model']='different'
        p.write_text(json.dumps({'schema':'qingxu.requests.v1','requests':[self.imported('new'),changed]}))
        with self.assertRaises(ValueError):self.index.import_file(p)
        self.assertEqual(len(self.scan()['requests']),2)
    def test_empty_import_not_success(self):
        p=self.root/'requests.json';p.write_text('{"schema":"qingxu.requests.v1","requests":[]}')
        with self.assertRaises(ValueError):self.index.import_file(p)
    def test_source_scope_is_separate(self):
        self.scan();other=self.root/'other';other.mkdir()
        self.assertFalse(self.index.scan(other)['requests'])
    def test_oversize_record_excluded(self):
        huge=event('huge');huge['payload']['extra']='x'*1100000
        write(self.log,[huge],'a')
        r=self.scan();self.assertEqual(len(r['requests']),1);self.assertEqual(r['issues']['oversize_requests'],1)

    def test_matching_duplicate_can_enrich_reported_status(self):
        write(self.log,[event(status='success',reported_cost_usd='0.25',duration_ms=1000)],'a')
        result=self.scan();self.assertEqual(len(result['requests']),1)
        self.assertEqual(result['requests'][0].status,'success')
        self.assertEqual(result['requests'][0].reported_cost_usd,'0.25')

    def test_conflicting_duplicate_times_and_status_are_excluded(self):
        item=event();item['timestamp']='2026-11-11T02:00:00Z'
        write(self.log,[item],'a');self.assertEqual(self.scan()['issues']['conflicts_excluded'],1)
        write(self.log,[meta(),ctx(),event(status='success'),event(status='error')])
        self.assertFalse(self.scan()['requests'])

    def test_oversized_body_drains_across_budget_then_indexes_next_request(self):
        from billing_index import MAX_LINE
        self.scan()
        with self.log.open('a') as f:
            f.write('x'*(MAX_LINE*2+50)+'\n')
            f.write(json.dumps(event('after-large'))+'\n')
        first=self.index.scan(self.home,budget=MAX_LINE)
        self.assertEqual(len(first['requests']),1)
        second=self.scan();self.assertEqual(len(second['requests']),2)
        self.assertEqual(second['issues']['oversize_other_lines'],1)


if __name__=='__main__':unittest.main()
