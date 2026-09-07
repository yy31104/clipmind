"""Offline, fixture-driven contract checks for built-in and third-party adapters.

This module does not load entry points or perform acquisition. Adapter methods
are ordinary trusted Python code; callers must supply synthetic, offline cases.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Sequence

from .base import SourceAdapterProtocol
from .identity import legacy_canonicalize_source, legacy_source_id


@dataclass(frozen=True)
class NormalizationCase:
    """Synthetic upstream metadata and expected normalized field values."""

    source: str
    info: dict
    expected: dict = field(default_factory=dict)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def assert_adapter_conformance(
    adapter: SourceAdapterProtocol,
    *,
    matching_sources: Sequence[str],
    nonmatching_sources: Sequence[str],
    normalization_cases: Sequence[NormalizationCase],
    distinct_sources: Sequence[tuple[str, str]] = (),
) -> None:
    """Raise AssertionError when an adapter violates a supplied contract case.

    At least one positive, negative, and normalization case is required. Include
    subdomains and lookalikes appropriate to the adapter. ``expected`` compares
    a subset of normalized fields, so plugins can test their own transformations.

    Identity hooks remain optional. Supplied hooks must be deterministic, and
    canonicalization must be idempotent. Distinct pairs test the effective
    identity (including legacy defaults for missing hooks): neither canonical
    sources nor nonempty IDs may collide, since either can trigger pack reuse.
    No claim is made about distinctness beyond the pairs the caller supplies.
    """
    for attribute in ("name", "platform"):
        value = getattr(adapter, attribute, None)
        _require(
            isinstance(value, str) and bool(value.strip()),
            f"{attribute} must be a non-empty string",
        )
    _require(adapter.platform == adapter.platform.lower(), "platform must be lowercase")
    for attribute in ("local", "generic"):
        _require(isinstance(getattr(adapter, attribute, None), bool), f"{attribute} must be a boolean")
    for hook in ("matches", "normalize_info"):
        _require(callable(getattr(adapter, hook, None)), f"{hook} must be callable")

    identity_hooks = {}
    for hook in ("canonicalize_source", "source_id"):
        if hasattr(adapter, hook):
            implementation = getattr(adapter, hook)
            _require(callable(implementation), f"{hook} must be callable when supplied")
            identity_hooks[hook] = implementation

    _require(bool(matching_sources), "supply at least one matching source")
    _require(bool(nonmatching_sources), "supply at least one nonmatching source")
    _require(bool(normalization_cases), "supply at least one normalization case")
    for expected, sources in ((True, matching_sources), (False, nonmatching_sources)):
        for index, source in enumerate(sources):
            _require(
                adapter.matches(source) is expected,
                f"matches must return {expected} for case {index}",
            )

    for index, case in enumerate(normalization_cases):
        _require(adapter.matches(case.source) is True, f"normalization case {index} must match")
        supplied = deepcopy(case.info)
        original = deepcopy(supplied)
        normalized = adapter.normalize_info(case.source, supplied)
        _require(supplied == original, f"normalize_info mutated input for case {index}")
        _require(isinstance(normalized, dict), "normalize_info must return a dictionary")
        _require(normalized is not supplied, "normalize_info must return a separate dictionary")
        for key, value in (
            ("_clipmind_platform", adapter.platform),
            ("_clipmind_source_adapter", adapter.name),
        ):
            _require(normalized.get(key) == value, f"normalize_info must set {key}")
        if "id" in original:
            _require("id" in normalized and normalized["id"] == original["id"], "normalize_info must preserve upstream id")
        if "webpage_url" not in original:
            value = normalized.get("webpage_url")
            _require(isinstance(value, str) and bool(value.strip()), "normalize_info must provide webpage_url")
        for key, value in case.expected.items():
            _require(key in normalized and normalized[key] == value, f"normalize_info unexpected {key} for case {index}")

    for index, (left, right) in enumerate(distinct_sources):
        _require(
            adapter.matches(left) is True and adapter.matches(right) is True,
            f"distinct sources must both match for pair {index}",
        )
    sources = dict.fromkeys([
        *matching_sources,
        *(case.source for case in normalization_cases),
        *(source for pair in distinct_sources for source in pair),
    ])
    for index, source in enumerate(sources):
        if "canonicalize_source" in identity_hooks:
            canonicalize = identity_hooks["canonicalize_source"]
            canonical = canonicalize(source)
            _require(isinstance(canonical, str) and bool(canonical.strip()), "canonicalize_source must return a non-empty string")
            _require(canonicalize(source) == canonical, f"canonicalize_source is not deterministic for case {index}")
            _require(canonicalize(canonical) == canonical, f"canonicalize_source is not idempotent for case {index}")
        if "source_id" in identity_hooks:
            identify = identity_hooks["source_id"]
            source_id = identify(source)
            _require(source_id is None or (isinstance(source_id, str) and bool(source_id.strip())), "source_id must return a non-empty string or None")
            _require(identify(source) == source_id, f"source_id is not deterministic for case {index}")

    canonicalize = identity_hooks.get("canonicalize_source", legacy_canonicalize_source)
    identify = identity_hooks.get("source_id", legacy_source_id)
    for index, (left, right) in enumerate(distinct_sources):
        _require(canonicalize(left) != canonicalize(right), f"distinct sources share canonical source for pair {index}")
        left_id, right_id = identify(left), identify(right)
        _require(not left_id or not right_id or left_id != right_id, f"distinct sources share source_id for pair {index}")
