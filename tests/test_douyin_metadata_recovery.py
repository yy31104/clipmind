"""Synthetic, offline regressions for the two observed acquisition failures."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from clipmind import fetch, links
from clipmind.config import Settings
from clipmind.jobs import Job, JobStore
from clipmind.sources.douyin import ADAPTER
from clipmind.sources import douyin_public
from clipmind.sources.douyin_browser import ResolveFailed

WANTED = '1234567890123456789'
SOURCE = 'https://www.douyin.com/video/' + WANTED

def entry(identity):
    return {'aweme_id': identity, 'desc': 'Synthetic example', 'video': {'duration': 120000, 'play_addr': {'url_list': ['https://cdn.douyinvod.com/synthetic.mp4']}}}

def missing_failure():
    try:
        douyin_public.media_info({'aweme_list': [entry('999')]}, SOURCE, WANTED)
    except ResolveFailed as exc:
        return exc
    raise AssertionError('A recommendation must not satisfy the requested source.')

class PublicMetadataFailureTests(unittest.TestCase):
    def test_missing_target_records_counts_without_substitution(self):
        exc = missing_failure()
        self.assertEqual(getattr(exc, 'details', None), {'reason': 'target_missing', 'entry_count': 1, 'exact_id_matches': 0})

    def test_duplicate_target_is_distinct_from_missing_target(self):
        with self.assertRaises(ResolveFailed) as raised:
            douyin_public.media_info({'aweme_list': [entry(WANTED), entry(WANTED)]}, SOURCE, WANTED)
        self.assertEqual(getattr(raised.exception, 'details', None), {'reason': 'target_ambiguous', 'entry_count': 2, 'exact_id_matches': 2})

    def test_missing_entries_is_not_a_deleted_source_verdict(self):
        with self.assertRaises(ResolveFailed) as raised:
            douyin_public.media_info({'status_code': 0}, SOURCE, WANTED)
        self.assertEqual(getattr(raised.exception, 'details', None), {'reason': 'entries_missing'})

    def test_exact_identity_and_allowed_media_contract_are_preserved(self):
        media = douyin_public.media_info({'aweme_list': [entry('999'), entry(WANTED)]}, SOURCE, WANTED)
        self.assertEqual(media.info['id'], WANTED)
        self.assertEqual(media.info['duration'], 120)
        bad = entry(WANTED)
        bad['video']['play_addr']['url_list'] = ['http://127.0.0.1/not-allowed']
        with self.assertRaises(ResolveFailed):
            douyin_public.media_info({'aweme_list': [bad]}, SOURCE, WANTED)

    def test_public_diagnostic_is_classified_without_cookie_evidence(self):
        exc = missing_failure()
        failure = fetch.AttemptFailure('douyin-public', 'public feed', str(exc))
        object.__setattr__(failure, 'details', getattr(exc, 'details', None))
        error = fetch.classify_failures([failure], adapter=ADAPTER, platform='douyin')
        self.assertEqual(error.code, 'source_metadata_unavailable')
        self.assertEqual(getattr(error, 'details', None), {'metadata_attempts': [{'strategy': 'douyin-public', 'reason': 'target_missing', 'entry_count': 1, 'exact_id_matches': 0}]})

    def test_only_fixed_codes_and_integer_counts_are_exported(self):
        failure = fetch.AttemptFailure('douyin-public', 'public feed', 'private.invalid?token=secret')
        object.__setattr__(failure, 'details', {'reason': 'target_missing', 'entry_count': 8, 'exact_id_matches': 0, 'raw_payload': 'token=secret', 'url': 'https://private.invalid/?token=secret'})
        error = fetch.classify_failures([failure], adapter=ADAPTER, platform='douyin')
        self.assertEqual(error.code, 'source_metadata_unavailable')
        public = str(error) + error.action + json.dumps(getattr(error, 'details', None))
        self.assertNotIn('secret', public)
        self.assertNotIn('private.invalid', public)
        self.assertEqual(error.details['metadata_attempts'][0]['entry_count'], 8)

    def test_private_failure_still_has_priority_and_no_feed_detail_export(self):
        failure = fetch.AttemptFailure('douyin-public', 'public feed', 'no target')
        object.__setattr__(failure, 'details', {'reason': 'target_missing'})
        error = fetch.classify_failures([failure, fetch.AttemptFailure('-', 'no cookies', 'private video')], adapter=ADAPTER, platform='douyin')
        self.assertEqual(error.code, 'private_video')
        self.assertIsNone(getattr(error, 'details', None))

    def test_unrecognized_or_malformed_details_are_not_exported(self):
        for detail in ({'reason': ['not-a-code']}, {'reason': 'secret'}, {'reason': 'target_missing', 'entry_count': True, 'exact_id_matches': -1}):
            failure = fetch.AttemptFailure('douyin-public', 'public feed', 'unrecognized')
            object.__setattr__(failure, 'details', detail)
            error = fetch.classify_failures([failure, fetch.AttemptFailure('-', 'no cookies', 'Fresh cookies are needed')], adapter=ADAPTER, platform='douyin')
            if detail.get('reason') == 'target_missing':
                self.assertEqual(error.details, {'metadata_attempts': [{'strategy': 'douyin-public', 'reason': 'target_missing'}]})
            else:
                self.assertIsNone(getattr(error, 'details', None))

class MetadataPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_ladder_preserves_failure_and_never_downloads_recommendation(self):
        config = Settings(cookie_sources=('-',), cookie_file='', douyin_browser_enabled=False)
        with tempfile.TemporaryDirectory() as temp, patch.object(douyin_public, 'resolve', new=AsyncMock(side_effect=missing_failure())), patch.object(fetch.douyin_browser, 'download', new=AsyncMock()) as download, patch.object(fetch.CookieRung, 'acquire', new=AsyncMock(return_value=(None, fetch.AttemptFailure('-', 'no cookies', 'Fresh cookies are needed')))):
            with self.assertRaises(fetch.FetchError) as raised:
                await fetch.AcquisitionEngine().acquire(SOURCE, Path(temp), ADAPTER, config=config)
            download.assert_not_awaited()
            self.assertEqual(raised.exception.code, 'source_metadata_unavailable')
            self.assertEqual(getattr(raised.exception, 'details', {}).get('metadata_attempts', [{}])[0].get('reason'), 'target_missing')

    async def test_job_storage_retains_safe_reason_counts_after_restart_read(self):
        exc = missing_failure()
        failure = fetch.AttemptFailure('douyin-public', 'public feed', str(exc))
        object.__setattr__(failure, 'details', getattr(exc, 'details', None))
        error = fetch.classify_failures([failure], adapter=ADAPTER, platform='douyin')
        with tempfile.TemporaryDirectory() as temp, patch('clipmind.jobs.process', new=AsyncMock(side_effect=error)):
            store = JobStore(Path(temp), config=Settings(), providers=object())
            job = Job(id='synthetic-job', url=SOURCE, title='Synthetic example')
            store.jobs[job.id] = job
            await store._run(job)
            saved = store.storage.load()[0]
            self.assertEqual(saved['status'], 'error')
            self.assertEqual(saved['error_code'], 'source_metadata_unavailable')
            self.assertEqual((saved.get('error_details') or {}).get('metadata_attempts', [{}])[0].get('reason'), 'target_missing')
            self.assertFalse((Path(temp)/job.id/'manifest.json').exists())

class PastedBatchTitleTests(unittest.TestCase):
    def test_bare_batch_links_do_not_use_another_source_as_title(self):
        first, second = 'https://www.douyin.com/video/111', 'https://www.douyin.com/video/222'
        for separator in ('\n', ' '):
            text = first + separator + second
            self.assertIsNone(links.guess_title(text, first))
            self.assertIsNone(links.guess_title(text, second))

    def test_each_source_keeps_its_own_preceding_share_title(self):
        first, second = 'https://www.douyin.com/video/111', 'https://www.douyin.com/video/222'
        text = 'First title ' + first + '\nSecond title ' + second
        self.assertEqual(links.guess_title(text, first), 'First title')
        self.assertEqual(links.guess_title(text, second), 'Second title')

if __name__ == '__main__':
    unittest.main()
