"""Acceptance tests for preemption-aware ring segmentation."""
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from greenbook.archive import history, load, publish
from greenbook.cli import main
from greenbook.common import decode, encode
from greenbook.workflow import materialize

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / 'examples'


def load_example(name):
    return decode((EXAMPLES / name).read_text(encoding='utf-8'))


class LegacyParityTests(unittest.TestCase):
    def test_baseline_keeps_legacy_reports(self):
        result = materialize(load_example('baseline.json'))
        self.assertEqual({'services', 'unusable_edges', 'cycles', 'segments',
                          'coverage', 'arrivals', 'buckets'}, set(result))
        self.assertEqual([c['start_edge'] for c in result['cycles']], ['e0'])
        self.assertEqual(result['segments']['preemptions'], [])
        self.assertEqual(result['segments']['unclassified'], [])
        # One full anchored cycle plus the two confirmable incomplete runs.
        partial_edges = [tuple(p['edges']) for p in result['segments']['normal_partials']]
        self.assertEqual(partial_edges, [('e2', 'e3'), ('e5', 'e6')])
        self.assertTrue(all(p['reason'] in ('open_tail', 'no_complete_cycle')
                            for p in result['segments']['normal_partials']))

    def test_changed_remains_publishable(self):
        result = materialize(load_example('changed.json'))
        self.assertEqual(len(result['segments']['normal_cycles']), 1)
        self.assertEqual(result['segments']['preemptions'], [])

    def test_invalid_bundle_still_rejected(self):
        with self.assertRaises(ValueError):
            materialize(load_example('invalid.json'))

    def test_segment_output_has_no_internal_fields(self):
        result = materialize(load_example('preempt.json'))
        for group in result['segments'].values():
            for item in group:
                self.assertNotIn('_indexes', item)
                self.assertNotIn('_sequences', item)
                self.assertNotIn('legacy', item)


class PreemptionAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.result = materialize(load_example('preempt.json'))
        self.segments = self.result['segments']

    def test_four_way_delivery(self):
        self.assertEqual(set(self.segments),
                         {'normal_cycles', 'normal_partials', 'preemptions', 'unclassified'})

    def test_full_cycles_on_both_sides_of_preemption(self):
        cycles = self.segments['normal_cycles']
        self.assertEqual([c['edges'] for c in cycles],
                         [['e0', 'e1', 'e2', 'e3', 'e4'],
                          ['e12', 'e13', 'e14', 'e15', 'e16']])
        # The post-resume cycle is still anchored at the first phase even
        # though the ring physically resumes at phase E.
        self.assertEqual(cycles[1]['start_edge'], 'e12')
        self.assertEqual(cycles[1]['certain'], [1102, 1498])
        # Legacy top-level cycle report carries the same cycles.
        self.assertEqual([c['start_edge'] for c in self.result['cycles']], ['e0', 'e12'])

    def test_green_opening_carries_into_preemption_as_partial(self):
        partials = {tuple(p['edges']): p for p in self.segments['normal_partials']}
        # Half-open tiling: the cycle's closing edge e4 also opens the tail
        # partial, whose last interval e4->e5 is the green opening carried
        # into the preemption.
        tail = partials[('e4', 'e5')]
        self.assertTrue(tail['missing_tail'])
        self.assertFalse(tail['missing_head'])
        self.assertEqual(tail['reason'], 'preempted_tail')
        self.assertEqual(tail['certain_ms'], [412, 508])
        # The resume-side partial ends on the opening edge of the next cycle.
        head = partials[('e9', 'e10', 'e11', 'e12')]
        self.assertTrue(head['missing_head'])
        self.assertFalse(head['missing_tail'])
        self.assertEqual(head['reason'], 'preempted_head')
        # Every edge is reachable from the four-way delivery.  Boundary edges
        # are legitimately shared between abutting segments (half-open
        # tiling), so the partition is checked as set coverage, not a count.
        seen = set()
        for group in ('normal_cycles', 'normal_partials', 'preemptions', 'unclassified'):
            for item in self.segments[group]:
                seen.update(item['edges'])
        self.assertEqual(seen, {f'e{i}' for i in range(17)})

    def test_preempt_segment_evidence(self):
        preempt = self.segments['preemptions'][0]
        self.assertEqual(preempt['id'], 'pre-1')
        self.assertEqual(preempt['entry_edge'], 'e6')
        self.assertEqual(preempt['resume_edge'], 'e9')
        self.assertEqual(preempt['resume_phase'], 'E')
        self.assertEqual(preempt['edges'], ['e6', 'e7', 'e8'])
        self.assertFalse(preempt['open'])
        self.assertEqual(preempt['entry_time_ms'], [558, 562])
        self.assertEqual(preempt['exit_time_ms'], [798, 802])
        self.assertEqual(preempt['certain_ms'], [562, 798])
        self.assertEqual(preempt['possible_ms'], [558, 802])
        self.assertEqual(preempt['clock_refs'], ['clock-c', 'clock-c'])

    def test_services_do_not_cross_preemption_boundaries(self):
        pairs = {tuple(s['edges']) for s in self.result['services']}
        # No service interval spans an entry/exit boundary or sits inside.
        self.assertNotIn(('e5', 'e6'), pairs)
        self.assertNotIn(('e8', 'e9'), pairs)
        self.assertNotIn(('e6', 'e7'), pairs)
        # Boundary pairs are not reported as ordinary unusable edges either.
        gap_pairs = {tuple(g['edges']) for g in self.result['unusable_edges']}
        self.assertNotIn(('e5', 'e6'), gap_pairs)
        self.assertNotIn(('e8', 'e9'), gap_pairs)
        self.assertEqual(self.result['unusable_edges'],
                         [{'edges': ['e16'], 'reason': 'open_ended'}])

    def test_arrival_inside_preemption_cannot_be_classified(self):
        facts = {a['id']: a for a in self.result['arrivals']}
        self.assertEqual(facts['a-normal-green']['state'], 'green')
        self.assertEqual(facts['a-after-resume']['state'], 'green')
        self.assertEqual(facts['a-during-preempt']['state'], 'unproved_service')
        self.assertEqual(facts['a-during-preempt']['service_refs'], [])

    def test_statistics_use_existing_report_conventions(self):
        for group in self.segments.values():
            for item in group:
                stats = item['statistics']
                # Segments with no confirmable window carry null statistics
                # instead of fabricated numbers.
                window = item.get('certain_ms', item.get('certain'))
                if window is None:
                    self.assertIsNone(stats)
                    continue
                phases = [row['phase'] for row in stats]
                self.assertEqual(phases, ['N', 'E', 'S', 'W'])
                for row in stats:
                    self.assertEqual(set(row), {'phase', 'observation_ms_by_detector',
                                                 'certain_green', 'certain_non_green',
                                                 'unresolved_or_boundary',
                                                 'green_share_of_classified'})

    def test_naive_cutting_would_have_failed(self):
        # If the preemption interval were simply removed, e5->e9 would look
        # like an N->E successor; the stateful classification must instead
        # keep e5 as the end of a preempted partial and resume the ordinary
        # ring only at e9, with the next full cycle anchored on e12.
        partials = {tuple(p['edges']): p for p in self.segments['normal_partials']}
        self.assertEqual(partials[('e4', 'e5')]['reason'], 'preempted_tail')
        preempt = self.segments['preemptions'][0]
        self.assertEqual(preempt['resume_edge'], 'e9')
        self.assertEqual([c['start_edge'] for c in self.segments['normal_cycles']],
                         ['e0', 'e12'])


class NoResumeEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.result = materialize(load_example('preempt_no_resume.json'))
        self.segments = self.result['segments']

    def test_no_cycle_is_assumed_after_exit(self):
        self.assertEqual([c['start_edge'] for c in self.segments['normal_cycles']], ['e0'])
        unknown = self.segments['unclassified']
        self.assertEqual(len(unknown), 1)
        self.assertEqual(unknown[0]['edges'],
                         ['e9', 'e10', 'e11', 'e12', 'e13', 'e14', 'e15', 'e16'])
        self.assertEqual(unknown[0]['reason'], 'no_resume_evidence')
        preempt = self.segments['preemptions'][0]
        self.assertIsNone(preempt['resume_phase'])
        self.assertIsNone(preempt['resume_edge'])

    def test_post_exit_services_are_not_reconstructed(self):
        # Only the pre-preemption services survive; nothing after e5.
        self.assertEqual([s['sequence'] for s in self.result['services']],
                         [0, 1, 2, 3, 4])
        facts = {a['id']: a for a in self.result['arrivals']}
        self.assertEqual(facts['a-after-resume']['state'], 'unproved_service')


class PreemptionValidationTests(unittest.TestCase):
    def base(self):
        return load_example('preempt.json')

    def test_unknown_resume_phase_rejected(self):
        doc = self.base()
        doc['preemptions'][0]['resume_phase'] = 'X'
        with self.assertRaises(ValueError):
            materialize(doc)

    def test_contradicted_resume_phase_rejected(self):
        doc = self.base()
        doc['preemptions'][0]['resume_phase'] = 'S'  # e9 is actually E
        with self.assertRaises(ValueError):
            materialize(doc)

    def test_backwards_span_rejected(self):
        doc = self.base()
        doc['preemptions'][0]['exit_tick'] = 10
        with self.assertRaises(ValueError):
            materialize(doc)

    def test_overlapping_spans_rejected(self):
        doc = self.base()
        doc['preemptions'].append({'id': 'pre-2', 'entry_tick': 700, 'exit_tick': 900,
                                   'resume_phase': 'E'})
        with self.assertRaises(ValueError):
            materialize(doc)

    def test_duplicate_preemption_id_rejected(self):
        doc = self.base()
        doc['preemptions'].append({'id': 'pre-1', 'entry_tick': 900, 'exit_tick': 950})
        with self.assertRaises(ValueError):
            materialize(doc)


class SegmentationEdgeCaseTests(unittest.TestCase):
    def base(self):
        return load_example('preempt.json')

    def test_adjacent_cycles_share_their_boundary_without_empty_partials(self):
        doc = self.base()
        doc.pop('preemptions')
        doc['edges'] = [
            {'id': f'e{i}', 'seq': i, 'tick': 100 + i * 100,
             'phase': ['N', 'E', 'S', 'W'][i % 4]}
            for i in range(9)
        ]
        result = materialize(doc)
        cycles = result['segments']['normal_cycles']
        self.assertEqual([c['edges'] for c in cycles],
                         [['e0', 'e1', 'e2', 'e3', 'e4'],
                          ['e4', 'e5', 'e6', 'e7', 'e8']])
        self.assertEqual(result['segments']['normal_partials'], [])
        self.assertEqual(result['segments']['unclassified'], [])

    def test_open_preemption_runs_to_end_of_stream(self):
        doc = self.base()
        doc['preemptions'][0]['exit_tick'] = 2000
        doc['preemptions'][0].pop('resume_phase')
        result = materialize(doc)
        preempt = result['segments']['preemptions'][0]
        self.assertTrue(preempt['open'])
        self.assertIsNone(preempt['resume_edge'])
        self.assertEqual(preempt['edges'][0], 'e6')
        self.assertEqual(preempt['edges'][-1], 'e16')
        # No ordinary cycle or service is reconstructed after entry.
        self.assertEqual([c['start_edge'] for c in result['segments']['normal_cycles']], ['e0'])
        self.assertEqual([s['sequence'] for s in result['services']], [0, 1, 2, 3, 4])

    def test_unclocked_boundary_degrades_window_but_keeps_structure(self):
        doc = self.base()
        doc['clock_certificates'][0]['ticks'] = [0, 700]
        result = materialize(doc)
        preempt = result['segments']['preemptions'][0]
        self.assertIsNone(preempt['certain_ms'])
        self.assertIsNone(preempt['possible_ms'])
        self.assertIsNone(preempt['statistics'])
        # Structural membership still follows ticks, not clock coverage.
        self.assertEqual(preempt['edges'], ['e6', 'e7', 'e8'])

    def test_span_without_edges_is_still_delivered(self):
        doc = self.base()
        doc['preemptions'] = [{'id': 'pre-gap', 'entry_tick': 555, 'exit_tick': 558}]
        result = materialize(doc)
        preempt = result['segments']['preemptions'][0]
        self.assertEqual(preempt['edges'], [])
        self.assertIsNone(preempt['entry_edge'])
        self.assertIsNone(preempt['resume_edge'])

    def test_multiple_sequential_preemptions(self):
        doc = self.base()
        doc['preemptions'] = [
            {'id': 'p1', 'entry_tick': 560, 'exit_tick': 800, 'resume_phase': 'E'},
            {'id': 'p2', 'entry_tick': 950, 'exit_tick': 1050, 'resume_phase': 'N'},
        ]
        result = materialize(doc)
        preempts = {p['id']: p for p in result['segments']['preemptions']}
        self.assertEqual(preempts['p1']['edges'], ['e6', 'e7', 'e8'])
        self.assertEqual(preempts['p2']['edges'], ['e11'])
        self.assertEqual(preempts['p2']['resume_edge'], 'e12')
        self.assertEqual([c['start_edge'] for c in result['segments']['normal_cycles']],
                         ['e0', 'e12'])
        covered = {e for group in result['segments'].values() for item in group for e in item['edges']}
        self.assertEqual(covered, {f'e{i}' for i in range(17)})


class ArchiveRoundTripTests(unittest.TestCase):
    def test_publish_replay_and_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'ledger.sqlite'
            doc = load_example('preempt.json')
            receipt = publish(archive, 'preempt', doc, materialize(doc))
            self.assertEqual(set(receipt), {'edition', 'source_sha256', 'result_sha256'})
            stored = load(archive, 'preempt')
            self.assertEqual(encode(stored), encode(materialize(doc)))
            self.assertEqual(history(archive)[0]['edition'], 'preempt')
            # Replay through the CLI and verify the digest check passes.
            self.assertEqual(main(['replay', str(archive), 'preempt']), 0)
            # Re-publishing the same edition name is refused.
            with self.assertRaises(ValueError):
                publish(archive, 'preempt', doc, materialize(doc))

    def test_cli_preview_exit_codes(self):
        good = EXAMPLES / 'preempt.json'
        self.assertEqual(main(['preview', str(good)]), 0)
        self.assertEqual(main(['preview', str(EXAMPLES / 'invalid.json')]), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
