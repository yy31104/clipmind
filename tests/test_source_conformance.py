from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from urllib.parse import parse_qsl, urlencode, urlsplit

from clipmind import evidence
from clipmind.asr import Segment, Transcript
from clipmind.sources import MediaAsset
from clipmind.storage import JobStorage
from clipmind.sources import SourceAdapter
from clipmind.sources.conformance import IdentityCase, NormalizationCase, assert_adapter_conformance
from clipmind.sources.direct import ADAPTER as DIRECT
from clipmind.sources.douyin import ADAPTER as DOUYIN
from clipmind.sources.youtube import ADAPTER as YOUTUBE


SOURCE = "https://video.example/watch?lesson=7&p=1&utm_source=share"
OTHER_PART = "https://video.example/watch?lesson=7&p=2&utm_source=share"
INFO = {"id": "lesson-7-part-1", "title": "Synthetic lesson", "uploader": "Fixture", "duration": 12.5}


class LessonAdapter(SourceAdapter):
    def canonicalize_source(self, source: str) -> str:
        query = dict(parse_qsl(urlsplit(source).query))
        return "https://video.example/watch?" + urlencode({
            "lesson": query["lesson"], "p": query.get("p", "1"),
        })

    def source_id(self, source: str) -> str:
        query = dict(parse_qsl(urlsplit(source).query))
        return f"lesson-{query['lesson']}-part-{query.get('p', '1')}"


def legacy_plugin(**overrides):
    adapter = SourceAdapter(name="fixture", platform="example", domains=("video.example",))
    return SimpleNamespace(**{
        "name": adapter.name, "platform": adapter.platform,
        "local": False, "generic": False,
        "matches": adapter.matches, "normalize_info": adapter.normalize_info,
        **overrides,
    })


def check(adapter, **overrides) -> None:
    assert_adapter_conformance(adapter, **{
        "matching_sources": [SOURCE, SOURCE.replace("video.example", "cdn.video.example")],
        "nonmatching_sources": [
            "https://notvideo.example/watch", "https://video.example.evil.invalid/watch",
            "https://video.example@evil.invalid/watch", "ftp://video.example/watch",
        ],
        "normalization_cases": [NormalizationCase(SOURCE, INFO, {**INFO, "webpage_url": SOURCE})],
        **overrides,
    })


class SourceConformanceTests(unittest.TestCase):
    def test_explicit_identity_cases_cover_tracking_and_identity_queries(self) -> None:
        adapter = LessonAdapter(name="fixture", platform="example", domains=("video.example",))
        canonical = "https://video.example/watch?lesson=7&p=1"
        check(adapter, identity_cases=[
            IdentityCase(SOURCE, canonical, "lesson-7-part-1"),
            IdentityCase(SOURCE.replace("utm_source=share", "utm_source=other"), canonical, "lesson-7-part-1"),
            IdentityCase(OTHER_PART, canonical.replace("p=1", "p=2"), "lesson-7-part-2"),
        ], distinct_sources=[(SOURCE, OTHER_PART)])

    def test_identity_cases_reject_tracking_retention_and_wrong_ids(self) -> None:
        canonical = "https://video.example/watch?lesson=7&p=1"
        for hooks, message in (
            ({"canonicalize_source": lambda source: source}, "unexpected canonical source"),
            ({"canonicalize_source": lambda source: canonical, "source_id": lambda source: "wrong-id"}, "unexpected source_id"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(AssertionError, message):
                check(legacy_plugin(**hooks), identity_cases=[IdentityCase(SOURCE, canonical, "lesson-7-part-1")])

    def test_identity_cases_use_optional_hook_fallbacks_and_authoritative_none(self) -> None:
        source = "https://video.example/video/123?p=1&utm_source=share"
        canonical = "https://video.example/video/123?p=1"
        check(legacy_plugin(), identity_cases=[IdentityCase(source, canonical, "123")])
        check(legacy_plugin(source_id=lambda source: None), identity_cases=[IdentityCase(source, canonical, None)])
        with self.assertRaisesRegex(AssertionError, "identity case 0 must match"):
            check(legacy_plugin(), identity_cases=[IdentityCase("https://elsewhere.example", canonical, None)])

    def test_part_distinction_cannot_be_masked_by_tracking(self) -> None:
        def drop_part(source):
            values = parse_qsl(urlsplit(source).query)
            return "https://video.example/watch?" + urlencode([(key, value) for key, value in values if key != "p"])

        with self.assertRaisesRegex(AssertionError, "share canonical source"):
            check(legacy_plugin(canonicalize_source=drop_part, source_id=lambda source: None),
                  distinct_sources=[(SOURCE, OTHER_PART)])

    def test_privacy_cases_accept_sanitized_metadata_without_changing_fixture(self) -> None:
        secret = "synthetic-secret-marker"
        info = {**INFO, "http_headers": {"Authorization": secret}, "webpage_url": SOURCE + "&token=" + secret}
        original = deepcopy(info)

        def normalize(source, supplied):
            clean = {key: value for key, value in supplied.items() if key not in {"http_headers", "webpage_url"}}
            return legacy_plugin().normalize_info(source, clean)

        check(legacy_plugin(normalize_info=normalize), normalization_cases=[
            NormalizationCase(SOURCE, info, {**INFO, "webpage_url": SOURCE}, forbidden_text=(secret,)),
        ])
        self.assertEqual(info, original)

    def test_privacy_cases_reject_nested_and_exportable_secret_values(self) -> None:
        secret = "synthetic-secret-marker"
        for extra in (
            {"http_headers": {"Authorization": secret}},
            {"chapters": [{"url": "https://video.example/?token=" + secret}]},
            {"webpage_url": SOURCE + "&token=" + secret},
            {secret: "a secret in a dictionary key"},
        ):
            with self.subTest(field=next(iter(extra))):
                with self.assertRaisesRegex(AssertionError, "retained forbidden text") as caught:
                    check(legacy_plugin(), normalization_cases=[
                        NormalizationCase(SOURCE, {**INFO, **extra}, forbidden_text=(secret,)),
                    ])
                self.assertNotIn(secret, str(caught.exception))

    def test_invalid_privacy_sentinels_are_not_vacuous(self) -> None:
        for secret in ("", 123, None):
            with self.subTest(secret=secret), self.assertRaisesRegex(AssertionError, "non-empty strings"):
                check(legacy_plugin(), normalization_cases=[NormalizationCase(SOURCE, INFO, forbidden_text=(secret,))])

    def test_real_pack_writer_accepts_plugin_platform_without_exporting_raw_headers(self) -> None:
        secret = "synthetic-header-marker"
        normalized = legacy_plugin().normalize_info(SOURCE, {**INFO, "http_headers": {"Authorization": secret}})
        with tempfile.TemporaryDirectory() as temporary:
            storage = JobStorage(Path(temporary))
            root = storage.workdir("plugin-pack")
            record = {"id": "plugin-pack", "url": SOURCE, "status": "running"}
            storage.save("plugin-pack", record)
            (root / "visual_states" / "all").mkdir(parents=True)
            (root / "visual_states" / "preview").mkdir()
            item = MediaAsset(root / "unused.mp4", normalized)
            evidence.write_pack(root, item, Transcript([Segment(0, 1, "Synthetic speech")]),
                                [], [], [], candidate_frame_count=0)
            storage.save("plugin-pack", {**record, "status": "done"})
            manifest = evidence.load_complete_pack(root)
            self.assertEqual(manifest["source"]["platform"], "example")
            source = json.loads((root / "source.json").read_text(encoding="utf-8"))
            self.assertEqual(source["url"], SOURCE)
            self.assertNotIn("http_headers", source)
            for artifact in ("source.json", "evidence.md", "transcript.md", "manifest.json"):
                self.assertNotIn(secret, (root / artifact).read_text(encoding="utf-8"))

    def test_third_party_adapter_preserves_multipart_identity(self) -> None:
        adapter = LessonAdapter(name="fixture", platform="example", domains=("video.example",))
        check(adapter, distinct_sources=[(SOURCE, OTHER_PART)])

    def test_legacy_protocol_needs_no_optional_hooks(self) -> None:
        plugin = legacy_plugin()
        self.assertFalse(hasattr(plugin, "canonicalize_source"))
        self.assertFalse(hasattr(plugin, "source_id"))
        check(plugin, distinct_sources=[(SOURCE, OTHER_PART)])

    def test_identity_hooks_remain_independently_optional(self) -> None:
        for hooks in (
            {"canonicalize_source": lambda source: source},
            {"source_id": lambda source: None},
        ):
            with self.subTest(hooks=list(hooks)):
                check(legacy_plugin(**hooks), distinct_sources=[(SOURCE, OTHER_PART)])

    def test_none_source_id_is_authoritative_in_distinct_cases(self) -> None:
        # The legacy parser would return the same numeric ID for both parts.
        check(legacy_plugin(source_id=lambda source: None), distinct_sources=[(
            "https://video.example/video/123?p=1",
            "https://video.example/video/123?p=2",
        )])

    def test_builtin_url_adapters_with_synthetic_cases(self) -> None:
        cases = (
            (YOUTUBE, "https://youtu.be/Fixture123?si=share", "https://notyoutube.com/watch?v=Fixture123"),
            (DOUYIN, "https://douyin.com/video/123?share_source=fixture", "https://notdouyin.com/video/123"),
            (DIRECT, "https://example.org/watch?p=7&utm_source=share", "ftp://example.org/watch"),
        )
        for adapter, source, rejected in cases:
            with self.subTest(adapter=adapter.name):
                assert_adapter_conformance(
                    adapter,
                    matching_sources=[source], nonmatching_sources=[rejected],
                    normalization_cases=[NormalizationCase(source, INFO, INFO)],
                )

    def test_all_real_required_attributes_are_checked(self) -> None:
        for attribute in ("name", "platform", "local", "generic", "matches", "normalize_info"):
            with self.subTest(attribute=attribute):
                plugin = legacy_plugin()
                delattr(plugin, attribute)
                with self.assertRaisesRegex(AssertionError, attribute):
                    check(plugin)

    def test_invalid_metadata_and_noncallable_hooks_fail(self) -> None:
        for attribute, invalid in (
            ("name", " "), ("platform", ""), ("platform", "Example"),
            ("local", 0), ("generic", "false"),
            ("matches", True), ("normalize_info", {}),
            ("canonicalize_source", "https://video.example"), ("source_id", None),
        ):
            with self.subTest(attribute=attribute, invalid=invalid):
                with self.assertRaisesRegex(AssertionError, attribute):
                    check(legacy_plugin(**{attribute: invalid}))

    def test_empty_case_sets_cannot_pass_vacuously(self) -> None:
        for field in ("matching_sources", "nonmatching_sources", "normalization_cases"):
            with self.subTest(field=field):
                with self.assertRaisesRegex(AssertionError, "supply at least one"):
                    check(legacy_plugin(), **{field: []})

    def test_matching_failures_and_truthy_nonbooleans_fail(self) -> None:
        for result in (True, False, 1, "yes"):
            with self.subTest(result=result):
                with self.assertRaisesRegex(AssertionError, "matches must return"):
                    check(legacy_plugin(matches=lambda source: result))

    def test_normalization_cases_must_match_adapter(self) -> None:
        with self.assertRaisesRegex(AssertionError, "normalization case 0 must match"):
            check(legacy_plugin(), normalization_cases=[NormalizationCase("https://other.example", INFO)])

    def test_normalization_detects_top_level_and_nested_input_mutation(self) -> None:
        original = {**INFO, "chapters": [{"title": "Original"}]}
        for nested in (False, True):
            def mutate(source, info):
                if nested:
                    info["chapters"][0]["title"] = "Changed"
                else:
                    info["title"] = "Changed"
                return legacy_plugin().normalize_info(source, info)

            fixture = deepcopy(original)
            with self.subTest(nested=nested):
                with self.assertRaisesRegex(AssertionError, "mutated input"):
                    check(legacy_plugin(normalize_info=mutate), normalization_cases=[NormalizationCase(SOURCE, fixture)])
                self.assertEqual(fixture, original, "the kit must protect caller fixtures")

    def test_normalization_must_return_separate_dictionary(self) -> None:
        info = legacy_plugin().normalize_info(SOURCE, INFO)
        with self.assertRaisesRegex(AssertionError, "separate dictionary"):
            check(legacy_plugin(normalize_info=lambda source, info: info), normalization_cases=[NormalizationCase(SOURCE, info)])
        with self.assertRaisesRegex(AssertionError, "return a dictionary"):
            check(legacy_plugin(normalize_info=lambda source, info: []))

    def test_normalization_requires_tags_upstream_id_url_and_expected_fields(self) -> None:
        for key in ("_clipmind_platform", "_clipmind_source_adapter", "id", "webpage_url", "title", "uploader", "duration"):
            def drop_field(source, info):
                normalized = legacy_plugin().normalize_info(source, info)
                del normalized[key]
                return normalized

            with self.subTest(key=key):
                with self.assertRaisesRegex(AssertionError, key):
                    check(legacy_plugin(normalize_info=drop_field))

    def test_normalization_rejects_wrong_platform_tags_and_changed_id(self) -> None:
        for key in ("_clipmind_platform", "_clipmind_source_adapter", "id"):
            def replace_field(source, info):
                return {**legacy_plugin().normalize_info(source, info), key: "wrong"}

            with self.subTest(key=key):
                with self.assertRaisesRegex(AssertionError, key):
                    check(legacy_plugin(normalize_info=replace_field))

    def test_normalization_allows_declared_field_transformations(self) -> None:
        def normalize(source, info):
            return {**legacy_plugin().normalize_info(source, info), "title": info["title"].strip()}

        check(legacy_plugin(normalize_info=normalize), normalization_cases=[
            NormalizationCase(SOURCE, {**INFO, "title": "  Trimmed  "}, {"title": "Trimmed"}),
            NormalizationCase(SOURCE, {**INFO, "webpage_url": "https://video.example/original"}, {"webpage_url": "https://video.example/original"}),
        ])

    def test_identity_return_types_are_checked(self) -> None:
        for hook, invalid in (
            ("canonicalize_source", None), ("canonicalize_source", ""),
            ("source_id", 123), ("source_id", ""),
        ):
            with self.subTest(hook=hook, invalid=invalid):
                with self.assertRaisesRegex(AssertionError, hook):
                    check(legacy_plugin(**{hook: lambda source: invalid}))

    def test_nondeterministic_identity_hooks_fail(self) -> None:
        for hook in ("canonicalize_source", "source_id"):
            counter = iter(range(100))
            with self.subTest(hook=hook):
                with self.assertRaisesRegex(AssertionError, f"{hook} is not deterministic"):
                    check(legacy_plugin(**{hook: lambda source: f"https://video.example/{next(counter)}"}))

    def test_canonicalization_must_be_idempotent(self) -> None:
        with self.assertRaisesRegex(AssertionError, "canonicalize_source is not idempotent"):
            check(legacy_plugin(canonicalize_source=lambda source: source + "&extra=1"))

    def test_distinct_sources_require_both_canonical_and_id_separation(self) -> None:
        for hook, value, message in (
            ("canonicalize_source", "https://video.example/watch", "share canonical source"),
            ("source_id", "lesson-7", "share source_id"),
        ):
            with self.subTest(hook=hook):
                with self.assertRaisesRegex(AssertionError, message):
                    check(legacy_plugin(**{hook: lambda source: value}), distinct_sources=[(SOURCE, OTHER_PART)])

    def test_distinct_pairs_must_be_owned_by_the_adapter(self) -> None:
        adapter = LessonAdapter(name="fixture", platform="example", domains=("video.example",))
        with self.assertRaisesRegex(AssertionError, "distinct sources must both match"):
            check(adapter, distinct_sources=[(SOURCE, "https://elsewhere.example/watch")])

    def test_bilibili_multipart_case_surfaces_preserved_legacy_collision(self) -> None:
        first = "https://www.bilibili.com/video/BV1fixture?p=1"
        seventh = "https://www.bilibili.com/video/BV1fixture?p=7"
        self.assertNotEqual(DIRECT.canonicalize_source(first), DIRECT.canonicalize_source(seventh))
        self.assertEqual(DIRECT.source_id(first), DIRECT.source_id(seventh))
        with self.assertRaisesRegex(AssertionError, "distinct sources share source_id"):
            assert_adapter_conformance(
                DIRECT,
                matching_sources=[first, seventh], nonmatching_sources=["ftp://example.org/watch"],
                normalization_cases=[NormalizationCase(first, INFO)],
                distinct_sources=[(first, seventh)],
            )


if __name__ == "__main__":
    unittest.main()
