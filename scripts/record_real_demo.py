#!/usr/bin/env python3
"""Record actual search-to-evidence UI actions against a prepared local demo.

Start ClipMind with CLIPMIND_OUT pointing to the real-talk library first.
Requires Playwright and Chrome; never opens a user profile or another website.
"""
import argparse
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


async def record(args):
    args.output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(channel="chrome", headless=True)
        context = await browser.new_context(
            viewport={"width": 1440, "height": 1000}, device_scale_factor=1,
            record_video_dir=str(args.output), record_video_size={"width": 1440, "height": 1000})
        page = await context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.goto(args.url)
        await page.get_by_role("button", name="Library", exact=True).click()
        await page.locator("#library [data-open]").first.wait_for()
        await page.wait_for_timeout(1000)
        await page.screenshot(path=args.output / "01-library.png")
        await page.locator("#library-search").press_sequentially(args.query, delay=100)
        hit = page.locator(".search-hit").first
        await hit.wait_for()
        hit_text = await hit.inner_text()
        timestamp = await hit.get_attribute("data-at")
        await page.wait_for_timeout(1800)
        await page.screenshot(path=args.output / "02-search.png")
        await hit.click()
        await page.locator("#frame-dialog[open]").wait_for()
        await page.locator("#frame-dialog-image").evaluate("image => image.decode()")
        await page.wait_for_timeout(2200)
        await page.screenshot(path=args.output / "03-evidence.png")
        evidence = await page.locator("#frame-dialog").inner_text()
        await page.get_by_role("button", name="View in transcript", exact=True).click()
        await page.wait_for_timeout(1800)
        await page.screenshot(path=args.output / "04-transcript.png")
        if errors:
            raise RuntimeError(f"Browser errors: {errors}")
        video = page.video
        await context.close()
        await video.save_as(args.output / "search-to-frame.webm")
        await browser.close()
        (args.output / "verification.json").write_text(json.dumps({
            "url": args.url, "query": args.query, "hit": hit_text,
            "timestamp": timestamp, "evidence_dialog": evidence,
            "page_errors": errors, "capture": "actual local UI; no mocked responses"}, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8439")
    parser.add_argument("--query", default="a string that is moved")
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(record(parser.parse_args()))
