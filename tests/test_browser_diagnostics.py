"""Safe browser phases survive the ladder and durable job error records."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

from clipmind import fetch
from clipmind.config import Settings
from clipmind.jobs import Job, JobStore
from clipmind.sources import douyin_browser
from clipmind.sources.douyin import ADAPTER
from tests.test_douyin_browser import _Page, _Engine


SOURCE = 'https://www.douyin.com/note/7812345678901234567'


def missing_public():
    return fetch.AttemptFailure('douyin-public', 'public feed', 'no requested entry',
                                {'reason':'target_missing', 'entry_count':6, 'exact_id_matches':0})


class BrowserDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    def classify(self, detail):
        return fetch.classify_failures([missing_public(),
            fetch.AttemptFailure('douyin-browser','browser session','secret URL and cookie diagnostic',detail)],
            adapter=ADAPTER,platform='douyin')

    async def test_metadata_failure_keeps_both_attempts_but_not_raw_diagnostics(self):
        error = self.classify({'reason':'metadata_unavailable','stage':'metadata',
                              'metadata_responses':0,'embedded_attempts':12,'last_http_status':403,
                              'url':'https://private.invalid/?token=secret','raw_payload':'cookie=secret'})
        self.assertEqual(error.code,'source_metadata_unavailable')
        self.assertIn('Chrome',error.user_message)
        self.assertEqual(error.details['metadata_attempts'][0]['entry_count'],6)
        self.assertEqual(error.details['browser_attempts'][0]['last_http_status'],403)
        self.assertNotIn('secret',error.user_message+error.action+json.dumps(error.details))

    async def test_download_failure_is_not_overridden_by_previous_missing_metadata(self):
        error = self.classify({'reason':'media_download_failed','stage':'download'})
        self.assertEqual(error.code,'source_media_unavailable')
        self.assertTrue(error.details['source_resolved'])
        self.assertIn('下载',error.user_message)
        self.assertNotIn('未返回请求',error.user_message)

    async def test_browser_launch_failure_has_an_installation_action(self):
        error = self.classify({'reason':'browser_unavailable','stage':'launch'})
        self.assertIn('无法启动',error.user_message)
        self.assertIn('Playwright',error.action)

    async def test_unrecognized_detail_fields_are_not_exported(self):
        for detail in ({'reason':['bad'],'stage':'metadata'},
                       {'reason':'metadata_unavailable','stage':'secret'},
                       {'reason':'metadata_unavailable','stage':'metadata','last_http_status':True,
                        'embedded_attempts':-1,'metadata_responses':'secret'}):
            with self.subTest(detail=detail):
                error = self.classify(detail)
                public = json.dumps(getattr(error,'details',{}))
                self.assertNotIn('secret',public)
                if isinstance(detail.get('reason'),str) and detail.get('stage') == 'metadata':
                    self.assertEqual(error.details['browser_attempts'],[
                        {'strategy':'douyin-browser','reason':'metadata_unavailable','stage':'metadata'}])

    async def test_navigation_timeout_is_sanitized_and_browser_is_closed(self):
        page = _Page(SOURCE, [])
        page.goto = AsyncMock(side_effect=TimeoutError('https://private.invalid?cookie=secret'))
        module = types.ModuleType('playwright.async_api')
        engine = _Engine(page)
        browser = await engine.chromium.launch()
        engine.chromium.launch = AsyncMock(return_value=browser)
        module.async_playwright = lambda: engine
        with patch.dict(sys.modules, {'playwright':types.ModuleType('playwright'),'playwright.async_api':module}):
            with self.assertRaises(douyin_browser.ResolveFailed) as raised:
                await douyin_browser.resolve(SOURCE,timeout=10)
        self.assertTrue(browser.closed)
        self.assertEqual(raised.exception.details['reason'],'navigation_timeout')
        self.assertEqual(raised.exception.details['stage'],'warmup')
        self.assertNotIn('secret',str(raised.exception)+json.dumps(raised.exception.details))

    async def test_cancellation_is_not_relabelled_as_browser_failure(self):
        page = _Page(SOURCE, [])
        page.goto = AsyncMock(side_effect=asyncio.CancelledError())
        module = types.ModuleType('playwright.async_api')
        module.async_playwright = lambda: _Engine(page)
        with patch.dict(sys.modules, {'playwright':types.ModuleType('playwright'),'playwright.async_api':module}):
            with self.assertRaises(asyncio.CancelledError):
                await douyin_browser.resolve(SOURCE,timeout=10)

    async def test_rung_and_job_storage_preserve_safe_browser_phase(self):
        error = douyin_browser._resolve_failure('no target metadata','metadata_unavailable','metadata',
                                               stats={'metadata_responses':0,'embedded_attempts':12})
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            douyin_browser,'resolve',new=AsyncMock(side_effect=error)
        ):
            acquired,failure = await fetch.DouyinBrowserRung().acquire(SOURCE,Path(temporary),Settings())
            self.assertIsNone(acquired)
            self.assertEqual(failure.details['stage'],'metadata')
            public = fetch.classify_failures([missing_public(),failure],adapter=ADAPTER,platform='douyin')
            with patch('clipmind.jobs.process',new=AsyncMock(side_effect=public)):
                store = JobStore(Path(temporary)/'library',config=Settings(),providers=object())
                job = Job(id='synthetic',url=SOURCE,title='Synthetic diagnostic')
                store.jobs[job.id] = job
                await store._run(job)
            persisted = store.storage.load()[0]
            self.assertEqual(persisted['error_details']['browser_attempts'][0]['stage'],'metadata')
            self.assertEqual(persisted['status'],'error')


if __name__ == '__main__':
    unittest.main()
