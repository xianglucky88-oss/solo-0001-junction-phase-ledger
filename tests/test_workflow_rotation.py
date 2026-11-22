import copy
import unittest

from greenbook.common import decode
from greenbook.workflow import materialize

ROTATION_PATH = 'examples/rotation.json'
BASELINE_PATH = 'examples/baseline.json'


class RotationWorkflowTests(unittest.TestCase):
    def setUp(self):
        with open(ROTATION_PATH, encoding='utf-8') as handle:
            self.doc = decode(handle.read())

    def result(self, doc=None):
        return materialize(doc or self.doc)

    def test_feasible_gap_reconstructs_services_and_bounds(self):
        out = self.result()
        entry = out['rotation_gaps'][0]
        self.assertEqual(entry['status'], 'feasible')
        self.assertEqual(entry['missing_edge_count'], 1)
        self.assertEqual([s['phase'] for s in entry['services']], ['E', 'N'])
        for service in entry['services']:
            self.assertTrue(service['reconstructed'])
            self.assertIsNotNone(service['certain'])
            self.assertEqual(service['duration_bounds_ms'], [60, 144])
        # Window [300, 500) totals: 58..142 for each phase, exact rationals.
        bounds = entry['phase_duration_bounds_ms']
        self.assertEqual(bounds['E'], {'min_ms': 58, 'max_ms': 142})
        self.assertEqual(bounds['N'], {'min_ms': 58, 'max_ms': 142})

    def test_reconstructed_services_join_the_service_list(self):
        out = self.result()
        sequences = {s['sequence'] for s in out['services']}
        self.assertIn(3, sequences)
        self.assertIn(4, sequences)
        reconstructed = [s for s in out['services'] if s.get('reconstructed')]
        self.assertEqual(len(reconstructed), 2)

    def test_proposal_joint_feasibility(self):
        entry = self.result()['rotation_gaps'][0]
        proposal = entry['proposal']
        self.assertTrue(proposal['simultaneously_achievable'])
        self.assertTrue(all(proposal['per_phase_reachable'].values()))

        doc = copy.deepcopy(self.doc)
        doc['rotation_gaps'][0]['proposal']['durations_ms'] = {'N': 140, 'E': 140}
        proposal = self.result(doc)['rotation_gaps'][0]['proposal']
        self.assertTrue(all(proposal['per_phase_reachable'].values()))
        self.assertFalse(proposal['simultaneously_achievable'])

    def test_infeasible_limits_keep_proposal_unproved(self):
        doc = copy.deepcopy(self.doc)
        for row in doc['rotation_gaps'][0]['phase_duration_limits_ms']:
            row['min_ms'] = 300
            row['max_ms'] = 400
        entry = self.result(doc)['rotation_gaps'][0]
        self.assertEqual(entry['status'], 'infeasible')
        self.assertIsNone(entry['services'])
        self.assertIsNone(entry['phase_duration_bounds_ms'])
        self.assertIsNone(entry['proposal']['simultaneously_achievable'])

    def test_rotation_conflict_is_unproved(self):
        doc = copy.deepcopy(self.doc)
        doc['edges'][4]['phase'] = 'E'
        entry = self.result(doc)['rotation_gaps'][0]
        self.assertEqual(entry['status'], 'unproved')
        self.assertEqual(entry['reason'], 'rotation_conflict')

    def test_endpoint_without_certificate_is_unproved(self):
        doc = copy.deepcopy(self.doc)
        doc['clock_certificates'][0]['ticks'] = [0, 400]
        entry = self.result(doc)['rotation_gaps'][0]
        self.assertEqual(entry['status'], 'unproved')
        self.assertEqual(entry['reason'], 'endpoint_unclocked')

    def test_fractional_proposal_is_exact(self):
        doc = copy.deepcopy(self.doc)
        doc['rotation_gaps'][0]['proposal']['durations_ms'] = {'N': '100/3', 'E': '99/2'}
        proposal = self.result(doc)['rotation_gaps'][0]['proposal']
        self.assertEqual(proposal['values_ms'], {'N': '100/3', 'E': '99/2'})
        self.assertFalse(proposal['simultaneously_achievable'])

    def test_arrival_promotion_needs_unanimous_timelines(self):
        out = self.result()
        by_id = {a['id']: a for a in out['arrivals']}
        # a-new (304..306) sits inside every feasible E service -> green.
        self.assertEqual(by_id['a-new']['state'], 'green')
        # a-gap at 420 can be E or N across feasible timelines -> stays ambiguous.
        self.assertEqual(by_id['a-gap']['state'], 'ambiguous_service')

    def test_gap_without_rotation_declaration_keeps_original_behavior(self):
        with open(BASELINE_PATH, encoding='utf-8') as handle:
            doc = decode(handle.read())
        out = self.result(doc)
        self.assertNotIn('rotation_gaps', out)
        states = {a['id']: a['state'] for a in out['arrivals']}
        self.assertEqual(states['a-new'], 'unproved_service')
        self.assertEqual(states['a-gap'], 'unproved_service')

    def test_long_chain_with_null_certain_segments_still_classifies(self):
        doc = copy.deepcopy(self.doc)
        doc['edges'] = [
            {'id': 'e0', 'seq': 0, 'tick': 10, 'phase': 'N'},
            {'id': 'e1', 'seq': 1, 'tick': 100, 'phase': 'E'},
            {'id': 'e7', 'seq': 7, 'tick': 700, 'phase': 'N'},
        ]
        doc['rotation_gaps'] = [{
            'id': 'gap-big', 'controller': 'ctrl', 'edges': ['e1', 'e7'],
            'observation_window_ms': [90, 710],
            'phase_duration_limits_ms': [
                {'phase': 'N', 'min_ms': '40/1', 'max_ms': 200},
                {'phase': 'E', 'min_ms': '40/1', 'max_ms': 200}],
        }]
        out = self.result(doc)
        entry = out['rotation_gaps'][0]
        self.assertEqual(entry['status'], 'feasible')
        self.assertEqual(entry['missing_edge_count'], 5)
        # Interior segments overlap across feasible timelines -> null certain.
        self.assertIsNone(entry['services'][1]['certain'])
        # Arrival classification must not crash on null-certain services.
        self.assertTrue(all('state' in a for a in out['arrivals']))


if __name__ == '__main__':
    unittest.main()
