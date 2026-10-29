"""Synthetic fixtures for the measured desktop note Pace state contract."""
import copy
import json
import sys
import types
import unittest
from unittest.mock import patch

from clipmind.sources import douyin_browser
from clipmind.sources.douyin_embedded import embedded_media_info
from tests.test_douyin_browser import _Clock, _Engine, _Page

WANTED = "7812345678901234567"
OTHER = "7812345678901234568"
PAGE = "https://www.douyin.com/note/" + WANTED
CDN = "https://p3-pc-sign.douyinpic.com/"


def props(identity=WANTED):
    return {
        "awemeId": identity, "statusCode": 0,
        "aweme": {"statusCode": 0, "detail": {
            "awemeId": identity, "awemeType": 68, "desc": "A visual lesson",
            "authorInfo": {"nickname": "Example"},
            "images": [{"urlList": [CDN + "one.webp", CDN + "one.jpeg"]},
                       {"urlList": [CDN + "two.webp"]}],
            "music": {"playUrl": "https://music.douyinvod.com/background.mp3"},
        }},
    }


def script(value, suffix=")"):
    payload = "7:" + json.dumps(value, ensure_ascii=True) + "\n"
    return "self.__pace_f.push(" + json.dumps([1, payload]) + suffix


def html(*records):
    return "<html>" + "".join(
        '<script nonce="" crossorigin="anonymous">' + script(record) + "</script>"
        for record in records
    ) + "</html>"


def element(value):
    return ["$", "$L9", None, value]


def chunks_html(parts, channel=1):
    return "<html>" + "".join(
        "<script>self.__pace_f.push(" + json.dumps([channel, part]) + ")</script>"
        for part in parts
    ) + "</html>"


class EmbeddedMetadataTests(unittest.TestCase):
    def test_exact_target_original_pages_are_read_from_named_pace_state(self):
        data = props()
        before = copy.deepcopy(data)
        media = embedded_media_info(html(element(data)), PAGE, WANTED)
        self.assertEqual(media.info["id"], WANTED)
        self.assertEqual(media.image_urls, ((CDN + "one.webp", CDN + "one.jpeg"), (CDN + "two.webp",)))
        self.assertEqual(media.info["_clipmind_image_audio"], "not_acquired")
        self.assertEqual(media.info["_clipmind_metadata_source"], "embedded page state")
        self.assertEqual(data, before)

    def test_recommendations_and_loader_route_id_do_not_supply_item_identity(self):
        for data in (props(OTHER), {"awemeId": WANTED},
                     {"awemeId": WANTED, "aweme": {"statusCode": 0, "detail": {"images": []}}}):
            with self.subTest(data=data), self.assertRaises(douyin_browser.ResolveFailed):
                embedded_media_info(html(element(data)), PAGE, WANTED)

    def test_outer_route_and_inner_item_id_must_both_match(self):
        for outer, inner in ((OTHER, WANTED), (WANTED, OTHER)):
            data = props(outer)
            data["aweme"]["detail"]["awemeId"] = inner
            with self.subTest(outer=outer), self.assertRaises(douyin_browser.ResolveFailed):
                embedded_media_info(html(element(data)), PAGE, WANTED)

    def test_repeated_target_entries_are_ambiguous(self):
        with self.assertRaises(douyin_browser.ResolveFailed):
            embedded_media_info(html(element(props()), element(props())), PAGE, WANTED)

    def test_official_individual_page_and_explicit_id_are_required(self):
        for page in ("http://www.douyin.com/note/" + WANTED,
                     "https://www.douyin.com.evil.invalid/note/" + WANTED,
                     "https://user:secret@www.douyin.com/note/" + WANTED,
                     "https://www.douyin.com/jingxuan?modal_id=" + WANTED,
                     "https://www.douyin.com/note/" + OTHER):
            with self.subTest(page=page), self.assertRaises(douyin_browser.ResolveFailed):
                embedded_media_info(html(element(props())), page, WANTED)

    def test_only_named_json_transport_is_accepted_never_arbitrary_javascript(self):
        data = element(props())
        for body in (
            "<script>window.anything=" + json.dumps(data) + "</script>",
            '<script id="RENDER_DATA" type="application/json">' + json.dumps(data) + "</script>",
            '<script src="https://cdn.invalid/a">' + script(data) + "</script>",
            "<script>" + script(data) + ";fetch('https://evil.invalid')</script>",
            "<script>self.__pace_f.push([1,alert('bad')])</script>",
        ):
            with self.subTest(body=body), self.assertRaises(douyin_browser.ResolveFailed):
                embedded_media_info(body, PAGE, WANTED)

    def test_json_inside_comments_related_items_and_arbitrary_dicts_is_not_scanned(self):
        for data in ({"aweme": props()},
                     element({"relatedVideo": element(props())}),
                     element({"comments": [props()]})):
            with self.subTest(data=data), self.assertRaises(douyin_browser.ResolveFailed):
                embedded_media_info(html(data), PAGE, WANTED)

    def test_real_react_children_arrays_can_contain_the_requested_props(self):
        media = embedded_media_info(html(element({"children": [element({"other": True}), element(props())]})), PAGE, WANTED)
        self.assertEqual(len(media.image_urls), 2)

    def test_one_flight_record_split_across_pace_pushes_keeps_all_pages(self):
        row = "7:" + json.dumps(element(props())) + "\n"
        # Split the row header and the JSON string/body, not just between rows.
        parts = [row[:1], row[1:39], row[39:len(row) // 2], row[len(row) // 2:]]
        media = embedded_media_info(chunks_html(parts), PAGE, WANTED)
        self.assertEqual(media.image_urls, ((CDN + "one.webp", CDN + "one.jpeg"), (CDN + "two.webp",)))

    def test_multiple_flight_rows_in_one_push_are_independently_parsed(self):
        rows = "6:" + json.dumps(element(props(OTHER))) + "\n7:" + json.dumps(element(props())) + "\n"
        media = embedded_media_info(chunks_html([rows]), PAGE, WANTED)
        self.assertEqual(media.info["id"], WANTED)

    def test_percent_encoded_config_chunk_is_not_concatenated_into_next_flight_row(self):
        row = "7:" + json.dumps(element(props())) + "\n"
        media = embedded_media_info(chunks_html(["%7B%22app%22%3A%7B%7D%7D", row]), PAGE, WANTED)
        self.assertEqual(len(media.image_urls), 2)

    def test_unrelated_javascript_and_other_channels_never_complete_a_fragment(self):
        row = "7:" + json.dumps(element(props())) + "\n"
        first, rest = row[:len(row) // 2], row[len(row) // 2:]
        cases = [
            chunks_html([first]).replace("</html>", "<script>" + rest + "</script></html>"),
            chunks_html([first]).replace("</html>", chunks_html([rest], channel=2) + "</html>"),
            "<script>self.__pace_f.push(" + json.dumps([1, first]) + ");dangerous()</script>"
                + chunks_html([rest]),
        ]
        for body in cases:
            with self.subTest(body=body), self.assertRaises(douyin_browser.ResolveFailed):
                embedded_media_info(body, PAGE, WANTED)

    def test_chunk_reassembly_has_an_explicit_transport_count_limit(self):
        row = "7:" + json.dumps(element(props())) + "\n"
        with self.assertRaisesRegex(douyin_browser.ResolveFailed, "transport limit"):
            embedded_media_info(chunks_html([""] * 256 + [row]), PAGE, WANTED)

    def test_nonzero_status_never_becomes_an_accessible_image_post(self):
        for inner in (False, True):
            data = props()
            (data["aweme"] if inner else data)["statusCode"] = 403
            with self.subTest(inner=inner), self.assertRaises(douyin_browser.ResolveFailed):
                embedded_media_info(html(element(data)), PAGE, WANTED)

    def test_untrusted_page_urls_and_malformed_motion_are_not_silently_dropped(self):
        for bad in ("url", "motion"):
            data = props()
            page = data["aweme"]["detail"]["images"][1]
            if bad == "url":
                page["urlList"] = ["https://127.0.0.1/private"]
            else:
                page["video"] = {"playAddr": "https://cdn.douyinvod.com/motion.mp4"}
            with self.subTest(bad=bad), self.assertRaises(douyin_browser.ResolveFailed):
                embedded_media_info(html(element(data)), PAGE, WANTED)

    def test_oversized_or_deeply_nested_state_is_bounded(self):
        with self.assertRaises(douyin_browser.ResolveFailed):
            embedded_media_info(" " * (2 * 1024 * 1024 + 1), PAGE, WANTED)
        nested = element(props())
        for _ in range(45):
            nested = [nested]
        with self.assertRaises(douyin_browser.ResolveFailed):
            embedded_media_info(html(nested), PAGE, WANTED)


class _EmbeddedPage(_Page):
    async def content(self):
        return html(element(props()))


class EmbeddedBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_note_without_detail_network_response_resolves_from_embedded_state(self):
        page = _EmbeddedPage(PAGE, [])
        module = types.ModuleType("playwright.async_api")
        module.async_playwright = lambda: _Engine(page)
        with patch.dict(sys.modules, {"playwright": types.ModuleType("playwright"),
                                     "playwright.async_api": module}), \
             patch.object(douyin_browser, "time", _Clock()):
            media = await douyin_browser.resolve(PAGE, timeout=10)
        self.assertEqual(media.info["id"], WANTED)
        self.assertEqual(len(media.image_urls), 2)

    async def test_a_transient_navigation_during_state_read_keeps_waiting(self):
        page = _EmbeddedPage(PAGE, [])
        reads = 0
        async def content():
            nonlocal reads
            reads += 1
            if reads == 1:
                raise RuntimeError("navigation still in progress")
            return html(element(props()))
        page.content = content
        module = types.ModuleType("playwright.async_api")
        module.async_playwright = lambda: _Engine(page)
        with patch.dict(sys.modules, {"playwright": types.ModuleType("playwright"),
                                     "playwright.async_api": module}), \
             patch.object(douyin_browser, "time", _Clock()):
            media = await douyin_browser.resolve(PAGE, timeout=20)
        self.assertEqual(reads, 2)
        self.assertEqual(len(media.image_urls), 2)

    async def test_exact_embedded_target_image_count_limit_is_not_swallowed_as_no_detail(self):
        from clipmind.sources.douyin_images import ImagePostBudgetExceeded
        data = props()
        data["aweme"]["detail"]["images"] = [{"urlList": [CDN + "page.webp"]}] * 1001
        body = html(element(data))
        with self.assertRaises(ImagePostBudgetExceeded):
            embedded_media_info(body, PAGE, WANTED)
        page = _EmbeddedPage(PAGE, [])
        async def content():
            return body
        page.content = content
        module = types.ModuleType("playwright.async_api")
        module.async_playwright = lambda: _Engine(page)
        with patch.dict(sys.modules, {"playwright": types.ModuleType("playwright"),
                                     "playwright.async_api": module}), \
             patch.object(douyin_browser, "time", _Clock()):
            with self.assertRaises(ImagePostBudgetExceeded):
                await douyin_browser.resolve(PAGE, timeout=10)


if __name__ == "__main__":
    unittest.main()
