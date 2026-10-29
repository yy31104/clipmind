"""The official mobile page's iteminfo response is not a detail response.

These are offline wiring checks, not evidence of live metadata availability.
"""
import sys
import types
import unittest
from unittest.mock import patch

from clipmind.sources import douyin_browser
from tests.test_douyin_browser import (
    OTHER, PAGE, WANTED, _Clock, _Engine, _Page, _Response, payload,
)


ITEMINFO = "https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids=" + WANTED


class MobileMetadataTests(unittest.TestCase):
    def test_iteminfo_carries_its_own_identity_and_duration(self):
        details = douyin_browser._metadata_entries(ITEMINFO, {
            "item_list": [payload(OTHER)["aweme_detail"], payload()["aweme_detail"]],
        })
        self.assertEqual([row["aweme_id"] for row in details], [OTHER, WANTED])
        resolved = douyin_browser._media_info({"aweme_detail": details[1]}, PAGE, WANTED)
        self.assertEqual(resolved.info["duration"], 604.647)

    def test_ambiguous_or_absent_entry_identity_is_not_taken_from_url(self):
        detail = payload()["aweme_detail"]
        for rows in ([detail, detail], [{"video": detail["video"]}], [], [None]):
            with self.subTest(rows=rows):
                self.assertEqual(douyin_browser._metadata_entries(ITEMINFO, {"item_list": rows}), ())

    def test_only_official_https_metadata_paths_are_parsed(self):
        valid = {"item_list": [payload()["aweme_detail"]]}
        for url in (
            "https://www.douyin.com.evil.invalid/web/api/v2/aweme/iteminfo/",
            "https://www.douyin.com@evil.invalid/web/api/v2/aweme/iteminfo/",
            "http://www.iesdouyin.com/web/api/v2/aweme/iteminfo/",
            "https://www.douyin.com/other?next=aweme/detail",
            "https://www.douyin.com/aweme/v1/web/aweme/feed/",
        ):
            with self.subTest(url=url):
                self.assertFalse(douyin_browser._is_metadata_response(url))
                self.assertEqual(douyin_browser._metadata_entries(url, valid), ())

    def test_wrong_shapes_and_loader_item_id_do_not_supply_video_metadata(self):
        for invalid in (None, [], {"item_list": {}}, {"itemId": WANTED}, {"aweme_detail": payload()["aweme_detail"]}):
            with self.subTest(invalid=invalid):
                self.assertEqual(douyin_browser._metadata_entries(ITEMINFO, invalid), ())


class _IteminfoPage(_Page):
    async def goto(self, url, **kwargs):
        self._visits += 1
        if self._visits == 1:
            self.url = douyin_browser.WARMUP_URL
            return
        self.url = self._landing
        for body in self._bodies:
            response = types.SimpleNamespace(url=ITEMINFO, status=200)
            async def json(body=body):
                return body
            response.json = json
            for handler in self._handlers:
                await handler(response)


class _PartialThenCompletePage(_Page):
    """A partial iteminfo arrives before the awaited playable detail."""
    def __init__(self, complete_id):
        super().__init__(PAGE, [])
        self.complete_id = complete_id
        self.partial_sent = False
        self.full_sent = False

    async def goto(self, url, **kwargs):
        self._visits += 1
        if self._visits == 1:
            self.url = douyin_browser.WARMUP_URL
            return
        self.url = PAGE
        response = _Response({"item_list": [{"aweme_id": WANTED, "desc": "Partial"}]})
        response.url = ITEMINFO
        for handler in self._handlers:
            await handler(response)
        self.partial_sent = True

    async def wait_for_timeout(self, milliseconds):
        if self.partial_sent and not self.full_sent:
            self.full_sent = True
            for handler in self._handlers:
                await handler(_Response(payload(self.complete_id)))


class MobileResponseWiringTests(unittest.IsolatedAsyncioTestCase):
    async def resolve(self, entries, landing=PAGE):
        page = _IteminfoPage(landing, [{"item_list": entries}])
        module = types.ModuleType("playwright.async_api")
        module.async_playwright = lambda: _Engine(page)
        with patch.dict(sys.modules, {
            "playwright": types.ModuleType("playwright"),
            "playwright.async_api": module,
        }), patch.object(douyin_browser, "time", _Clock()):
            return await douyin_browser.resolve(PAGE, timeout=10)

    async def test_actual_listener_uses_exact_target_from_iteminfo(self):
        media = await self.resolve([payload(OTHER)["aweme_detail"], payload()["aweme_detail"]])
        self.assertEqual(media.info["id"], WANTED)
        self.assertEqual(media.info["title"], "量化之王")

    async def test_actual_listener_refuses_recommendation_and_duplicate_target(self):
        for rows in ([payload(OTHER)["aweme_detail"]], [payload()["aweme_detail"]] * 2):
            with self.subTest(rows=rows):
                with self.assertRaises(douyin_browser.ResolveFailed):
                    await self.resolve(rows)

    async def test_redirect_cannot_change_target_for_iteminfo_either(self):
        with self.assertRaises(douyin_browser.ResolveFailed):
            await self.resolve([payload(OTHER)["aweme_detail"]],
                               landing="https://www.douyin.com/video/" + OTHER)

    async def resolve_partial_then_complete(self, complete_id):
        page = _PartialThenCompletePage(complete_id)
        module = types.ModuleType("playwright.async_api")
        module.async_playwright = lambda: _Engine(page)
        with patch.dict(sys.modules, {
            "playwright": types.ModuleType("playwright"),
            "playwright.async_api": module,
        }), patch.object(douyin_browser, "time", _Clock()):
            return await douyin_browser.resolve(PAGE, timeout=10), page

    async def test_partial_iteminfo_waits_for_playable_detail_with_same_id(self):
        media, page = await self.resolve_partial_then_complete(WANTED)
        self.assertTrue(page.full_sent)
        self.assertEqual(media.info["id"], WANTED)
        self.assertTrue(media.urls)

    async def test_partial_target_does_not_let_later_recommendation_satisfy_it(self):
        with self.assertRaises(douyin_browser.ResolveFailed):
            await self.resolve_partial_then_complete(OTHER)


if __name__ == "__main__":
    unittest.main()
