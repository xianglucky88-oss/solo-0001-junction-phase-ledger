"""Behavioural tests for reissue planning. Run: python3 -m unittest discover tests"""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from greenbook.common import decode, encode  # noqa: E402
from greenbook.receipt import canonical  # noqa: E402
from greenbook.resign import (  # noqa: E402
    FACTS_CHANGED, READJUDICATE, RENUMBERED, certificate_reissues, plan)
from greenbook.workflow import materialize  # noqa: E402


def load(name):
    return decode((ROOT / 'examples' / name).read_text(encoding='utf-8'))


BASE = load('baseline.json')
RECEIPT = load('receipt.json')


def build(mutate=None, receipt=None):
    base = copy.deepcopy(BASE)
    candidate = copy.deepcopy(BASE)
    if mutate:
        mutate(candidate)
    base_result = materialize(base)
    candidate_result = materialize(candidate)
    accepted = canonical(receipt or RECEIPT, base_result, 'original')
    return plan(base, base_result, candidate, candidate_result, accepted, 'original', 'candidate')


def by_key(plan_doc):
    return {row['key']: row for row in plan_doc['deliverables']}


def renumber(device, new_id):
    def mutate(doc):
        for cert in doc['clock_certificates']:
            if cert['device'] == device:
                cert['id'] = new_id
    return mutate


def revise(device, **fields):
    def mutate(doc):
        for cert in doc['clock_certificates']:
            if cert['device'] == device:
                cert.update(fields)
    return mutate


class CertificateMatching(unittest.TestCase):
    def test_renumber_is_recognised_as_the_same_clock(self):
        pairs = certificate_reissues(BASE, {**BASE, 'clock_certificates': [
            {**row, 'id': 'clock-d2'} if row['device'] == 'loop' else row
            for row in BASE['clock_certificates']]})
        self.assertEqual(pairs, [{'device': 'loop', 'base_id': 'clock-d',
                                  'candidate_id': 'clock-d2', 'change': 'renumbered',
                                  'fields': []}])

    def test_unrelated_certificates_are_not_reported(self):
        self.assertEqual(certificate_reissues(BASE, BASE), [])


class MinimalResigning(unittest.TestCase):
    def test_renumber_only_leaves_reports_and_cycles_alone(self):
        doc = build(renumber('loop', 'clock-d2'))
        rows = by_key(doc)
        self.assertEqual(rows['bucket:full']['action'], 'keep')
        self.assertEqual(rows['bucket:short']['action'], 'keep')
        self.assertEqual(rows['cycle:e0']['action'], 'keep')
        self.assertEqual(rows['evidence']['action'], 'resign')
        self.assertEqual(rows['evidence']['category'], RENUMBERED)
        self.assertEqual(doc['summary']['resign'], 1)

    def test_unreceived_deliverable_is_not_resigned_but_reported(self):
        receipt = {'edition': 'original', 'deliverables': [
            {'type': 'bucket_report', 'key': 'bucket:short', 'arrivals': ['a-green']}]}
        doc = build(revise('loop', offset_ms=1), receipt)
        rows = by_key(doc)
        self.assertEqual(rows['bucket:short']['action'], 'resign')
        self.assertEqual(rows['bucket:full']['action'], 'resign_if_received')
        self.assertFalse(rows['bucket:full']['received'])
        self.assertEqual(doc['summary']['resign_received'], 1)


class ThreeWaySeparation(unittest.TestCase):
    def test_identifier_change_that_does_not_move_facts(self):
        doc = build(renumber('loop', 'clock-d2'))
        evidence = by_key(doc)['evidence']
        self.assertTrue(evidence['changes'])
        self.assertTrue(all(c['category'] == RENUMBERED for c in evidence['changes']))

    def test_facts_move_but_labels_hold(self):
        doc = build(revise('loop', offset_ms=1))
        rows = by_key(doc)
        self.assertEqual(rows['bucket:short']['category'], FACTS_CHANGED)
        self.assertEqual(rows['bucket:short']['action'], 'resign')
        short = rows['bucket:short']
        self.assertEqual(short['affected_time_ms']['base'], [[59, 61]])
        self.assertEqual(short['affected_time_ms']['candidate'], [[60, 62]])

    def test_readjudication_when_an_arrival_is_reclassified(self):
        doc = build(revise('loop', offset_ms=1))
        rows = by_key(doc)
        self.assertEqual(rows['bucket:full']['category'], READJUDICATE)
        reasons = {c['ref']['key']: c['reason'] for c in rows['bucket:full']['changes']}
        self.assertEqual(reasons['a-rewire'], 'arrival_entered_bucket')

    def test_categories_are_ordered_and_a_deliverable_takes_the_worst(self):
        doc = build(revise('loop', offset_ms=1))
        full = by_key(doc)['bucket:full']
        self.assertIn(FACTS_CHANGED, {c['category'] for c in full['changes']})
        self.assertIn(READJUDICATE, {c['category'] for c in full['changes']})
        self.assertEqual(full['category'], READJUDICATE)


class DifficultFindings(unittest.TestCase):
    def test_untimed_arrival_first_entering_a_bucket_is_found(self):
        # a-unknown-clock has no time evidence at all in the base edition and is
        # listed in no report; extending the loop certificate gives it a time.
        doc = build(revise('loop', ticks=[0, 500], offset_ms=1))
        full = by_key(doc)['bucket:full']
        change = next(c for c in full['changes']
                      if c['ref']['key'] == 'a-unknown-clock')
        self.assertEqual(change['reason'], 'arrival_entered_bucket')
        self.assertEqual(change['category'], READJUDICATE)
        self.assertIsNone(change['base_time_ms'])
        self.assertEqual(change['candidate_time_ms'], [405, 407])
        self.assertTrue(any(s['type'] == 'certificate_reissue' for s in change['sources']))

    def test_duration_only_bucket_change_is_found_without_any_arrival(self):
        # shrink a side heartbeat so the short bucket loses observation time,
        # while the receipt cites no arrival for that bucket at all.
        receipt = {'edition': 'original', 'deliverables': [
            {'type': 'bucket_report', 'key': 'bucket:short'}]}

        def mutate(doc):
            for cert in doc['clock_certificates']:
                if cert['device'] == 'side':
                    cert['id'] = 'clock-x2'
            for hb in doc['heartbeats']:
                if hb['id'] == 'h3':
                    hb['ticks'] = [106, 240]

        doc = build(mutate, receipt)
        short = by_key(doc)['bucket:short']
        self.assertEqual(short['action'], 'resign')
        self.assertEqual(short['affected_time_ms']['base'], [[105, 140]])
        self.assertEqual(short['affected_time_ms']['candidate'], [[106, 140]])
        reasons = {c['ref']['key']: c['reason'] for c in short['changes']}
        self.assertEqual(reasons['E/side'], 'observation_duration_changed')
        self.assertEqual(reasons['side/wire-side/h3'], 'observation_interval_changed')

    def test_scan_is_not_driven_by_the_old_report_index(self):
        # the base report never lists a-unknown-clock; it must still be surfaced
        base_result = materialize(BASE)
        listed = {aid for phase in base_result['buckets']['full']
                  for field in ('unresolved_or_boundary',)
                  for aid in phase[field]}
        self.assertNotIn('a-unknown-clock', listed)
        doc = build(revise('loop', ticks=[0, 500]))
        self.assertIn('a-unknown-clock',
                      {c['ref']['key'] for c in by_key(doc)['bucket:full']['changes']})


class Explainability(unittest.TestCase):
    def test_every_resigned_deliverable_names_spans_and_sources(self):
        doc = build(revise('loop', offset_ms=1))
        for row in doc['deliverables']:
            if not row['resign']:
                continue
            self.assertTrue(row['changes'], row['key'])
            self.assertTrue(row['sources'], row['key'])
            self.assertTrue(row['affected_time_ms']['base']
                            or row['affected_time_ms']['candidate'], row['key'])
            for change in row['changes']:
                self.assertIn('sources', change)
                self.assertIn('reason', change)
                self.assertIn('category', change)

    def test_new_deliverables_get_distinct_keys(self):
        doc = build(revise('loop', offset_ms=1))
        for row in doc['deliverables']:
            if row['resign']:
                self.assertNotEqual(row['new_key'], row['base_key'])
            else:
                self.assertIsNone(row['new_key'])

    def test_plan_is_deterministic(self):
        first = encode(build(revise('loop', offset_ms=1)))
        second = encode(build(revise('loop', offset_ms=1)))
        self.assertEqual(first, second)


if __name__ == '__main__':
    unittest.main()
