"""The acquisition boundary preserves still and motion slots together."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from clipmind import fetch
from clipmind.config import Settings
from clipmind.sources import douyin_browser, douyin_images, douyin_motion, douyin_public
from clipmind.sources.douyin import ADAPTER

SOURCE = 'https://www.douyin.com/note/7812345678901234567'


class LivePhotoFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_rung_contract_preserves_motion_and_static_page_order(self):
        resolved = douyin_browser.ResolvedMedia((), {'id':'7812345678901234567','title':'Synthetic photo post'},
            image_urls=(('https://p3.douyinpic.com/one.webp',),('https://p3.douyinpic.com/two.webp',)),
            image_motion_urls=(('https://v3-dy-o.zjcdn.com/one.mp4',),()))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            originals = (root/'one.webp',root/'two.webp')
            for path in originals:
                path.write_bytes(b'fixture-image')
            motion = root/'one.mp4'
            motion.write_bytes(b'fixture-motion')
            with patch.object(douyin_public,'resolve',new=AsyncMock(return_value=resolved)), patch.object(
                douyin_images,'download_images',new=AsyncMock(return_value=originals)
            ), patch.object(douyin_motion,'download_motions',new=AsyncMock(return_value=(motion,None))) as download, patch.object(
                douyin_browser,'download',new=AsyncMock(side_effect=AssertionError('image post is not one video'))
            ):
                item = await fetch.AcquisitionEngine().acquire(SOURCE,root/'job',ADAPTER,
                    config=Settings(cookie_sources=(),douyin_browser_enabled=False,max_upload_mb=2))
            self.assertEqual(item.image_paths,originals)
            self.assertEqual(item.image_motion_paths,(motion,None))
            self.assertEqual(download.await_args.args[0],resolved.image_motion_urls)
            self.assertEqual(download.await_args.kwargs['max_total_bytes'],2*1024*1024-2*len(b'fixture-image'))

    async def test_missing_motion_does_not_publish_a_still_only_success(self):
        resolved = douyin_browser.ResolvedMedia((), {'id':'7812345678901234567'},
            image_urls=(('https://p3.douyinpic.com/one.webp',),),
            image_motion_urls=(('https://v3-dy-o.zjcdn.com/one.mp4',),))
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            image=root/'one.webp'
            image.write_bytes(b'fixture')
            with patch.object(douyin_browser,'resolve',new=AsyncMock(return_value=resolved)), patch.object(
                douyin_images,'download_images',new=AsyncMock(return_value=(image,))
            ), patch.object(douyin_motion,'download_motions',new=AsyncMock(side_effect=douyin_images.ImagePostError('motion unavailable'))):
                acquired,failure=await fetch.DouyinBrowserRung().acquire(SOURCE,root/'job',Settings())
            self.assertIsNone(acquired)
            error=fetch.classify_failures([failure],adapter=ADAPTER,platform='douyin')
            self.assertEqual(error.code,'image_collection_fetch_failed')
            self.assertIn('动态片段',error.user_message)


if __name__ == '__main__':
    unittest.main()
