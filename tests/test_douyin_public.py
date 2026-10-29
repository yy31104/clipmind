import unittest
from unittest.mock import patch

from clipmind.sources import douyin_public
from clipmind.sources.douyin_browser import ResolveFailed
from clipmind.fetch import AcquisitionEngine
from clipmind.config import Settings

WANTED = "7679083379178180841"
SOURCE = "https://www.douyin.com/video/" + WANTED

def entry(identity, url="https://cdn.douyinvod.com/video.mp4"):
    return {"aweme_id":identity,"desc":"正确内容","video":{"duration":73167,"play_addr":{"url_list":[url]}}}

class PublicFeedTests(unittest.TestCase):
    def test_recommendations_are_never_used_in_place_of_the_requested_video(self):
        payload={"aweme_list":[entry("999"),entry(WANTED)]}
        media=douyin_public.media_info(payload,SOURCE,WANTED)
        self.assertEqual(media.info["id"],WANTED)
        self.assertEqual(media.info["duration"],73.167)
        with self.assertRaises(ResolveFailed):
            douyin_public.media_info({"aweme_list":[entry("999")]},SOURCE,WANTED)

    def test_ambiguous_identity_and_untrusted_media_are_rejected(self):
        with self.assertRaises(ResolveFailed):
            douyin_public.media_info({"aweme_list":[entry(WANTED),entry(WANTED)]},SOURCE,WANTED)
        with self.assertRaises(ResolveFailed):
            douyin_public.media_info({"aweme_list":[entry(WANTED,"http://127.0.0.1/video")]},SOURCE,WANTED)

    def test_transport_errors_do_not_leak_urls(self):
        with patch.object(douyin_public.urllib.request,"urlopen",side_effect=OSError("https://private.invalid/?token=secret")):
            with self.assertRaises(ResolveFailed) as raised:
                douyin_public._resolve(SOURCE)
        self.assertNotIn("secret",str(raised.exception))

    def test_public_source_precedes_cookie_and_browser_rungs(self):
        names=[r.describe() for r in AcquisitionEngine().strategies("adapter:douyin",Settings())]
        self.assertEqual(names[0],"public feed")
        self.assertEqual(names[-1],"browser session")

if __name__ == "__main__": unittest.main()
