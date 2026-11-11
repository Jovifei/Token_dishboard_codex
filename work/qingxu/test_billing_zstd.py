"""Compressed Codex logs: real decoder, synthetic metadata, private temporary cache."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

try:
    from compression import zstd
except ImportError:
    try:
        from backports import zstd
    except ImportError:
        zstd = None

import billing_index
from billing_index import BillingIndex, MAX_LINE
from test_billing_index import NOW, ctx, event, meta, write


def lines(items, *, newline=True):
    payload = '\n'.join(json.dumps(item) for item in items)
    return (payload + ('\n' if newline else '')).encode('utf-8')


class CompressedFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / 'home'
        self.sessions = self.home / 'sessions'
        self.archived = self.home / 'archived_sessions'
        self.sessions.mkdir(parents=True)
        self.archived.mkdir()
        self.archive = self.archived / 'rollout-demo.jsonl.zst'
        self.index = BillingIndex(self.root / 'cache')

    def scan(self, **kwargs):
        return self.index.scan(self.home, **kwargs)


@unittest.skipIf(zstd is None, 'Python Zstandard decoder is unavailable')
class CompressedIndexTests(CompressedFixture):
    def store(self, items, *, newline=True):
        self.archive.write_bytes(zstd.compress(lines(items, newline=newline)))

    def test_plain_and_compressed_copy_deduplicate(self):
        items = [meta(), ctx(), event()]
        write(self.sessions / 'active.jsonl', items)
        self.store(items)
        result = self.scan()
        self.assertEqual([r.response_id for r in result['requests']], ['response1'])
        self.assertEqual(result['issues']['duplicates'], 1)
        self.assertEqual(result['requests'][0].model, 'gpt-6.1-sol')
        self.assertEqual(self.scan()['issues']['bytes_read'], 0)

    def test_conflicting_plain_and_compressed_copies_are_excluded(self):
        write(self.sessions / 'active.jsonl', [meta(), ctx(), event()])
        self.store([meta(), ctx(), event(model='other-model')])
        result = self.scan()
        self.assertEqual(result['requests'], [])
        self.assertEqual(result['issues']['conflicts_excluded'], 1)

    def test_concatenated_frames_preserve_context(self):
        self.archive.write_bytes(zstd.compress(lines([meta(), ctx()])) +
                                 zstd.compress(lines([event()])))
        result = self.scan()
        self.assertEqual(len(result['requests']), 1)
        self.assertEqual((result['requests'][0].model, result['requests'][0].model_source),
                         ('gpt-6.1-sol', 'turn_context'))

    def test_appended_frame_adds_new_request_without_duplicates(self):
        self.store([meta(), ctx(), event('before')])
        self.assertEqual([r.response_id for r in self.scan()['requests']], ['before'])
        with self.archive.open('ab') as stream:
            stream.write(zstd.compress(lines([event('after')])))
        result = self.scan()
        self.assertEqual({r.response_id for r in result['requests']}, {'before', 'after'})
        self.assertTrue(all(r.model == 'gpt-6.1-sol' for r in result['requests']))

    def test_complete_frame_with_no_final_newline(self):
        self.store([meta(), ctx(), event()], newline=False)
        result = self.scan()
        self.assertEqual([r.response_id for r in result['requests']], ['response1'])
        self.assertFalse(result['issues'].get('incomplete_files', 0))

    def test_small_budget_eventually_completes_and_preserves_turn_models(self):
        items = [meta(), ctx('gpt-6.1-sol', 'turn1')]
        items += [event('first-' + str(i), turn_id='turn1') for i in range(10)]
        items += [ctx('gpt-6-astra', 'turn2')]
        items += [event('second-' + str(i), turn_id='turn2') for i in range(10)]
        self.store(items)
        for attempt in range(100):
            result = self.scan(budget=128)
            if len(result['requests']) == 20 and not result['issues'].get('incomplete_files', 0):
                break
        else:
            self.fail('compressed file failed to complete across bounded scans')
        self.assertGreater(attempt, 0)
        self.assertFalse(result['issues'].get('pending_files', 0))
        self.assertFalse(result['issues'].get('compressed_read_errors', 0))
        for request in result['requests']:
            expected = 'gpt-6.1-sol' if request.response_id.startswith('first-') else 'gpt-6-astra'
            self.assertEqual(request.model, expected)

    def test_replaced_and_deleted_archive_removes_obsolete_events(self):
        self.store([meta(), ctx(), event('old')])
        self.assertEqual([r.response_id for r in self.scan()['requests']], ['old'])
        self.store([meta(), ctx(), event('new-replacement')])
        self.assertEqual([r.response_id for r in self.scan()['requests']], ['new-replacement'])
        self.archive.unlink()
        self.assertEqual(self.scan()['requests'], [])

    def test_archive_changed_during_read_is_retried_without_stale_events(self):
        self.store([meta(), ctx(), event('old')])
        consume = billing_index.consume
        changed = []
        def update_during_read(item, state, issues):
            result = consume(item, state, issues)
            if item.get('type') == 'token_usage_record' and not changed:
                with self.archive.open('ab') as stream:
                    stream.write(zstd.compress(lines([event('new')])))
                changed.append(True)
            return result
        with patch.object(billing_index, 'consume', side_effect=update_during_read):
            result = self.scan()
        self.assertGreater(result['issues'].get('read_errors', 0), 0)
        self.assertEqual(result['requests'], [])
        self.assertEqual({r.response_id for r in self.scan()['requests']}, {'old', 'new'})

    def test_damaged_archives_are_reported_and_cannot_retain_old_events(self):
        valid = zstd.compress(lines([meta(), ctx(), event('valid-frame')]))
        truncated = valid + zstd.compress(lines([event('truncated-frame')]))[:-3]
        for label, damaged in (('invalid', b'not a zstandard frame'),
                               ('truncated', truncated), ('garbage tail', valid + b'garbage')):
            with self.subTest(label=label):
                self.store([meta(), ctx(), event('old')])
                self.assertEqual([r.response_id for r in self.scan()['requests']], ['old'])
                self.archive.write_bytes(damaged)
                for _ in range(2):
                    result = self.scan()
                    self.assertGreater(result['issues'].get('compressed_read_errors', 0), 0)
                    self.assertEqual(result['requests'], [])

    def test_long_request_is_excluded_and_chat_body_never_enters_cache(self):
        marker = 'PRIVATE_BODY_MUST_NOT_ENTER_BILLING_CACHE'
        message = {'type': 'response_item', 'timestamp': NOW,
                   'payload': {'role': 'user', 'content': marker}}
        oversized = event('oversized')
        oversized['payload']['extra'] = 'x' * (MAX_LINE + 100)
        self.store([meta(), ctx(), message, oversized, event('after-large')])
        result = self.scan()
        self.assertEqual([r.response_id for r in result['requests']], ['after-large'])
        self.assertEqual(result['issues']['oversize_requests'], 1)
        for cache_file in (self.root / 'cache').rglob('*'):
            if cache_file.is_file():
                self.assertNotIn(marker.encode(), cache_file.read_bytes())

    def test_cancel_during_archive_can_retry_and_release_file(self):
        self.store([meta(), ctx()] + [event('r' + str(i)) for i in range(20)])
        stop = threading.Event()
        consume = billing_index.consume

        def consume_then_cancel(item, state, issues):
            result = consume(item, state, issues)
            if item.get('type') == 'token_usage_record':
                stop.set()
            return result

        with patch.object(billing_index, 'consume', side_effect=consume_then_cancel):
            with self.assertRaises(InterruptedError):
                self.scan(stop=stop)
        stop.clear()
        self.assertEqual(len(self.scan(stop=stop)['requests']), 20)
        self.archive.unlink()  # On Windows this also checks the decoder closed its file.
        self.assertEqual(self.scan()['requests'], [])

    def test_compressed_symlink_is_not_followed(self):
        self.store([meta(), ctx(), event()])
        link = self.sessions / 'linked.jsonl.zst'
        try:
            link.symlink_to(self.archive)
        except OSError:
            self.skipTest('symlink permissions unavailable')
        result = self.scan()
        self.assertGreater(result['issues'].get('unsafe_files', 0), 0)
        self.assertEqual(len(result['requests']), 1)


class MissingDecoderTests(CompressedFixture):
    def test_missing_decoder_reports_gap_and_keeps_plain_log_usable(self):
        write(self.sessions / 'active.jsonl', [meta(), ctx(), event()])
        self.archive.write_bytes(b'not read when decoder is unavailable')
        with patch.object(billing_index, 'zstd', None, create=True):
            result = self.scan()
        self.assertEqual([r.response_id for r in result['requests']], ['response1'])
        self.assertGreater(result['issues'].get('compressed_unavailable', 0), 0)


if __name__ == '__main__':
    unittest.main()
