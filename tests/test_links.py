import unittest

from clipmind.links import extract_sources, extract_urls, normalize_url, source_id_from_url


class ExtractUrlsTests(unittest.TestCase):
    def test_normalizes_cache_keys_and_extracts_stable_source_ids(self) -> None:
        self.assertEqual(
            normalize_url("http://WWW.douyin.com/video/123/?x=tracking"),
            "https://douyin.com/video/123",
        )
        self.assertEqual(source_id_from_url("https://www.douyin.com/video/123"), "123")
        self.assertIsNone(source_id_from_url("https://v.douyin.com/AbC-1"))
        self.assertEqual(
            normalize_url("https://youtu.be/AbCd123?si=tracking"),
            "https://youtube.com/watch?v=AbCd123",
        )
        self.assertEqual(
            normalize_url("https://www.youtube.com/watch?v=AbCd123&feature=share"),
            "https://youtube.com/watch?v=AbCd123",
        )
        self.assertEqual(
            source_id_from_url("https://www.bilibili.com/video/BV1abc123"),
            "BV1abc123",
        )

    def test_extracts_a_short_link_from_share_text(self) -> None:
        text = (
            '4.66 g@b.nQ 09/22 :5pm ULJ:/ \u50bb*\u9762\u8bd5\u5b98\u4e3a\u4ec0\u4e48\u8001\u662f\u95ee\u6211\u5e95\u5c42\uff1f '
            'https://v.douyin.com/dTfIvetIbJw/ \u590d\u5236\u6b64\u94fe\u63a5\uff0c\u6253\u5f00Dou\u97f3\u641c\u7d22'
        )

        self.assertEqual(
            extract_urls(text),
            ["https://v.douyin.com/dTfIvetIbJw"],
        )

    def test_extracts_multiple_links_in_source_order(self) -> None:
        text = (
            '\u4efb\u610f\u5f00\u5934 https://v.douyin.com/zmiAljffS3I/ \u4e2d\u95f4\u6587\u5b57\uff0c'
            '\u518d\u6765 https://www.douyin.com/video/7461234567890123456?from=share '
            '\u6700\u540e https://www.iesdouyin.com/share/note/7461234567890123457\u3002'
        )

        self.assertEqual(
            extract_urls(text),
            [
                "https://v.douyin.com/zmiAljffS3I",
                "https://www.douyin.com/video/7461234567890123456?from=share",
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
        for text in ("", '\u666e\u901a\u6587\u5b57', None):
            with self.subTest(text=text):
                self.assertEqual(extract_urls(text), [])

    def test_extracts_youtube_bilibili_and_generic_urls(self) -> None:
        self.assertEqual(
            extract_urls(
                "watch https://youtu.be/AbC123, then "
                "https://www.bilibili.com/video/BV1abc and "
                "https://cdn.example.com/demo.mp4"
            ),
            [
                "https://youtu.be/AbC123",
                "https://www.bilibili.com/video/BV1abc",
                "https://cdn.example.com/demo.mp4",
            ],
        )

    def test_accepts_an_explicit_local_file(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tempdir:
            source = Path(tempdir) / "clip.mp4"
            source.write_bytes(b"media")
            self.assertEqual(extract_sources(str(source)), [str(source.resolve())])


if __name__ == "__main__":
    unittest.main()
