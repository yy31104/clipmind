import unittest

from clipmind.fetch import AttemptFailure, classify_failures
from clipmind.links import normalize_url, source_id_from_url
from clipmind.sources.douyin import ADAPTER, modal_video_id


class DouyinEntryTests(unittest.TestCase):
    def test_user_modal_preserves_video_identity(self):
        for tab in ('record', 'favorite_collection'):
            source = f'https://www.douyin.com/user/self?from_tab_name=main&modal_id=7682348302453082787&showTab={tab}'
            self.assertEqual(normalize_url(source), 'https://douyin.com/video/7682348302453082787')
            self.assertEqual(source_id_from_url(source), '7682348302453082787')

    def test_modal_is_not_guessed(self):
        for source in ('https://other.example/user/self?modal_id=123',
                       'https://www.douyin.com/user/self?modal_id=123&modal_id=456',
                       'https://www.douyin.com/user/self?modal_id=bad'):
            self.assertIsNone(modal_video_id(source))

    def test_cookie_message_does_not_diagnose_cookie_expiry(self):
        failures = [AttemptFailure(s, s, 'ERROR: Fresh cookies (not necessarily logged in) are needed') for s in ('chrome', '-')]
        error = classify_failures(failures, adapter=ADAPTER, platform='douyin')
        self.assertEqual(error.code, 'source_metadata_unavailable')
        self.assertNotIn('refresh', error.action)
        self.assertNotIn('rejected the browser cookies', str(error))
