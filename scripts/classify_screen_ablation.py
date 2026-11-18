#!/usr/bin/env python3
"""Measure information-boundary typing on saved development frames, without extraction."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clipmind.media import Frame
from clipmind.visual_states import INFORMATION_BOUNDARY_ALGORITHM, annotate_change_kinds
from scripts.evaluate_real_talks import runtime_code_fingerprint
from scripts.screen_evaluation import boundary_inventory, reserve_record, resolve_pack_path, sha256


def _legacy_timeline(states: list[dict], frames: list[Frame]) -> list[dict]:
    typed = []
    for state, frame in zip(states, frames, strict=True):
        legacy = dict(state, change_kind=frame.change_kind, change_reason=frame.change_reason)
        legacy.pop("information_boundary", None)
        if frame.ocr_warning:
            legacy["ocr_warning"] = frame.ocr_warning
        typed.append(legacy)
    return typed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('previous', 'corpus', 'labels', 'workdir', 'output'):
        parser.add_argument(f'--{name}', type=Path, required=True)
    args = parser.parse_args()
    corpus = json.loads(args.corpus.read_text())
    if corpus.get('split') == 'holdout':
        parser.error('saved-frame ablations are for development inputs only')
    labels = json.loads(args.labels.read_text())
    previous = json.loads(args.previous.read_text())
    if previous['labels_sha256'] != sha256(args.labels) or previous['corpus_sha256'] != sha256(args.corpus):
        parser.error('source labels and corpus must match the original extraction')
    report = {'status': 'running', 'mode': 'saved-development-frame-typing',
              'previous_sha256': sha256(args.previous),
              'runtime_code_fingerprint': runtime_code_fingerprint(),
              'algorithm': INFORMATION_BOUNDARY_ALGORITHM, 'cases': []}
    reserve_record(args.output, report)
    for row in previous['cases']:
        root = resolve_pack_path(row['pack_path'], args.workdir)
        states = [json.loads(line) for line in (root/'visual_timeline.jsonl').read_text().splitlines()]
        ocr = {item['id']: item for line in (root/'ocr.jsonl').read_text().splitlines()
               if (item := json.loads(line))}
        frames = []
        for i, state in enumerate(states):
            text = ocr[state['ocr_ref']]
            frames.append(Frame(i, state['start'], root/state['file'], lines=tuple(text['lines']),
                                ocr_layout=tuple(text.get('layout', ())),
                                stable_duration=state.get('stable_duration_seconds', 0),
                                observed_sample_count=state.get('observed_sample_count', 1),
                                source_timestamp=state.get('source_timestamp'),
                                ocr_warning=text.get('error') or text.get('warning'),
                                dedupe_warning=state.get('dedupe_warning')))
        started = time.perf_counter()
        annotate_change_kinds(frames)
        typed = _legacy_timeline(states, frames)
        label = labels['cases'][row['case_id']]
        result = {'case_id': row['case_id'], 'typing_seconds': round(time.perf_counter()-started, 3),
                  'states': typed, 'slide_metric_applicable': label.get('slide_metric_applicable', True)}
        if result['slide_metric_applicable']:
            result['information_boundary_score'] = boundary_inventory(
                label['slide_changes'], typed, start=label['review_start'], end=label['review_end'],
                information_only=True)['score']
        report['cases'].append(result)
    report['status'] = 'measured'
    args.output.write_text(json.dumps(report, indent=2)+'\n')


if __name__ == '__main__':
    main()
