"""Transfer measurements reach the live job note without disclosing media URLs."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from clipmind import fetch
from clipmind.config import Settings
from clipmind.sources.douyin import ADAPTER
from clipmind.sources.douyin_browser import ResolvedMedia


class TransferStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_and_browser_transfers_report_bytes_before_completion(self):
        source = 'https://www.douyin.com/video/7812345678901234567'
        resolved = ResolvedMedia(
            ('https://video.douyinvod.com/media?token=private-test-value',),
            {'id':'7812345678901234567','title':'Synthetic podcast','duration':2},
        )
        for browser in (False, True):
            with self.subTest(browser=browser), tempfile.TemporaryDirectory() as temporary:
                notes = []
                seen_limits = []

                async def transfer(urls, destination, *, on_progress, max_bytes):
                    self.assertEqual(urls, resolved.urls)
                    seen_limits.append(max_bytes)
                    on_progress(0, None, 0)
                    on_progress(2_000_000, 5_000_000, 2)
                    self.assertIn('2.0 / 5.0 MB', notes[-1])
                    self.assertIn('1.0 MB/s', notes[-1])
                    self.assertFalse(destination.exists())
                    destination.write_bytes(b'synthetic media')
                    return destination

                public = AsyncMock(side_effect=fetch.douyin_browser.ResolveFailed('no public detail')) if browser else AsyncMock(return_value=resolved)
                with (
                    patch.object(fetch.douyin_public,'resolve',public),
                    patch.object(fetch.douyin_browser,'resolve',new=AsyncMock(return_value=resolved)),
                    patch.object(fetch.douyin_browser,'download',side_effect=transfer),
                ):
                    asset = await fetch.AcquisitionEngine().acquire(
                        source,Path(temporary)/'job',ADAPTER,notes.append,
                        config=Settings(cookie_sources=(),douyin_browser_enabled=browser,max_upload_mb=7),
                    )
                self.assertEqual(asset.source_id,'7812345678901234567')
                self.assertEqual(asset.media_path.read_bytes(),b'synthetic media')
                self.assertEqual(seen_limits,[7*1024*1024])
                self.assertTrue(any('等待数据' in note for note in notes))
                self.assertNotIn('private-test-value',' '.join(notes))
                self.assertNotIn('douyinvod.com',' '.join(notes))

    def test_unknown_length_has_no_invented_total_or_percentage(self):
        note = fetch._media_transfer_note(2_000_000,None,4)
        self.assertIn('2.0 MB',note)
        self.assertIn('0.5 MB/s',note)
        self.assertNotIn('%',note)
        self.assertNotIn(' / ',note)
