import unittest

from clipmind.links import extract_urls, normalize_url, source_id_from_url


class ExtractUrlsTests(unittest.TestCase):
    def test_normalizes_cache_keys_and_extracts_stable_source_ids(self) -> None:
        self.assertEqual(
            normalize_url("http://WWW.douyin.com/video/123/?x=tracking"),
            "https://douyin.com/video/123",
        )
        self.assertEqual(source_id_from_url("https://www.douyin.com/video/123"), "123")
        self.assertIsNone(source_id_from_url("https://v.douyin.com/AbC-1"))

    def test_extracts_a_short_link_from_share_text(self) -> None:
        text = (
            "4.66 g@b.nQ 09/22 :5pm ULJ:/ 傻*面试官为什么老是问我底层？ "
            "https://v.douyin.com/dTfIvetIbJw/ 复制此链接，打开Dou音搜索"
        )

        self.assertEqual(
            extract_urls(text),
            ["https://v.douyin.com/dTfIvetIbJw"],
        )

    def test_extracts_multiple_links_in_source_order(self) -> None:
        text = (
            "任意开头 https://v.douyin.com/zmiAljffS3I/ 中间文字，"
            "再来 https://www.douyin.com/video/7461234567890123456?from=share "
            "最后 https://www.iesdouyin.com/share/note/7461234567890123457。"
        )

        self.assertEqual(
            extract_urls(text),
            [
                "https://v.douyin.com/zmiAljffS3I",
                "https://www.douyin.com/video/7461234567890123456",
                "https://www.iesdouyin.com/share/note/7461234567890123457",
            ],
        )

    def test_deduplicates_repeated_links(self) -> None:
        text = (
            "https://v.douyin.com/QolP-jHN_Es/ some text "
            "https://v.douyin.com/QolP-jHN_Es"
        )

        self.assertEqual(
            extract_urls(text),
            ["https://v.douyin.com/QolP-jHN_Es"],
        )

    def test_invalid_or_empty_text_has_no_links(self) -> None:
        for text in ("", "普通文字", "https://example.com/video/123", None):
            with self.subTest(text=text):
                self.assertEqual(extract_urls(text), [])


if __name__ == "__main__":
    unittest.main()
