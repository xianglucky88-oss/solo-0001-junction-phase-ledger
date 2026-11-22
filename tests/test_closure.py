"""Acceptance tests for lane-closure joint-constraint simulation.

Run from the repository root:

    python3 -I -m unittest discover -s tests -v
"""
import contextlib
import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from greenbook.cli import main
from greenbook.common import decode
from greenbook.workflow import materialize

BUNDLE = str(ROOT / 'examples' / 'closure.json')


def load_bundle(path=BUNDLE):
    with open(path, encoding='utf-8') as handle:
        return decode(handle.read())


def run():
    return {row['plan']: row
            for row in materialize(load_bundle())['closure_simulations']}


class ClosureSimulation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = run()

    def test_three_verdict_kinds_are_reached(self):
        statuses = {row['status'] for row in self.rows.values()}
        self.assertEqual(statuses, {'feasible', 'contradicted', 'undetermined'})

    def test_two_restriction_lines_evaluated_together(self):
        ok = self.rows['plan-ok']
        self.assertEqual(ok['status'], 'feasible')
        self.assertEqual(len(ok['restrictions']), 2)
        self.assertTrue(all(line['within_cap'] for line in ok['restrictions']))

    def test_each_turn_inside_marginals_but_group_contradicts_batch_total(self):
        # The headline acceptance case: N-left=4 lies in [3,4] and N-thru=7
        # lies in [6,7], yet the shared-probe batch total is 10, not 11.
        row = self.rows['plan-batch']
        self.assertEqual(row['status'], 'contradicted')
        self.assertEqual(row['contradicts'], ['b1', 'b2'])
        total_violations = [v for v in row['violations'] if v['kind'] == 'total']
        self.assertTrue(total_violations)
        for violation in total_violations:
            self.assertEqual(violation['expected'], 10)
            self.assertEqual(violation['actual'], 11)
        self.assertFalse([v for v in row['violations'] if v['kind'] == 'marginal'])

    def test_cap_and_marginal_breach_is_still_reported(self):
        row = self.rows['plan-cap']
        self.assertEqual(row['status'], 'contradicted')
        kinds = {v['kind'] for v in row['violations']}
        self.assertEqual(kinds, {'marginal', 'cap', 'total'})

    def test_rewire_record_is_never_a_joint_constraint(self):
        # loop switches wiring at t=250; the [200,300) window straddles it.
        for row in self.rows.values():
            record = next(w for w in row['windows'] if w['id'] == 'loop-rewire')
            self.assertFalse(record['eligible'])
            self.assertIn('rewire_straddle', record['reasons'])

    def test_unbatched_record_is_excluded(self):
        record = next(w for w in self.rows['plan-ok']['windows'] if w['id'] == 'side-unbatched')
        self.assertFalse(record['eligible'])
        self.assertEqual(record['reasons'], ['unbatched'])

    def test_heartbeat_gap_excludes_even_with_batch_mark(self):
        # Under cl-3 the span matches exactly, wiring holds, but heartbeat
        # trusted coverage ends at 450 while the window runs to 460.
        row = self.rows['plan-side-blind']
        for record_id in ('side-gap', 'side-total-gap'):
            record = next(w for w in row['windows'] if w['id'] == record_id)
            self.assertFalse(record['eligible'])
            self.assertEqual(record['reasons'], ['heartbeat_gap'])

    def test_null_demand_is_unknown_not_zero(self):
        row = self.rows['plan-null']
        self.assertEqual(row['status'], 'undetermined')
        line = next(r for r in row['restrictions'] if r['turn'] == 'E-left')
        self.assertIsNone(line['demand'])
        self.assertIsNone(line['within_cap'])
        self.assertIn({'kind': 'cap', 'turn': 'E-left', 'reason': 'cap_uncheckable'},
                      row['missing_observations'])
        # A zero demand on the same turn would have been checkable.
        zero_line = next(r for r in self.rows['plan-ok']['restrictions'] if r['turn'] == 'E-left')
        self.assertIsNotNone(zero_line['within_cap'])

    def test_lane_closure_keeps_preexisting_unobserved_demand_unknown(self):
        row = self.rows['plan-blind']
        self.assertEqual(row['status'], 'undetermined')
        self.assertTrue(all(bound is None for bound in row['marginals'].values()))
        self.assertTrue(all(m['reason'] == 'unobserved_demand'
                            for m in row['missing_observations'] if m['kind'] == 'marginal'))

    def test_only_aligned_windows_constrain_a_closure(self):
        # cl-2 spans the rewire point; no field window aligns with it, so no
        # constraint is admissible and the plan cannot be decided.
        eligible = {w['id'] for w in self.rows['plan-blind']['windows'] if w['eligible']}
        self.assertEqual(eligible, set())

    def test_simulate_cli_command(self):
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink):
            code = main(['simulate', BUNDLE])
        self.assertEqual(code, 0)
        self.assertTrue(sink.getvalue().startswith('['))

    def test_legacy_bundles_remain_valid(self):
        for name in ('baseline.json', 'changed.json'):
            doc = load_bundle(str(ROOT / 'examples' / name))
            self.assertEqual(materialize(doc)['closure_simulations'], [])

    def test_malformed_bundles_exit_two(self):
        import json
        import tempfile
        base = load_bundle()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'case.json'
            cases = []
            short_lines = dict(base)
            short_lines['closures'] = [{**c, 'restrictions': c['restrictions'][:1]}
                                       for c in base['closures']]
            cases.append(short_lines)
            partial = dict(base)
            partial['demand_plans'] = [{**p,
                                        'demands': {k: v for k, v in p['demands'].items() if k != 'E-thru'}}
                                       if p['id'] == 'plan-ok' else p for p in base['demand_plans']]
            cases.append(partial)
            for doc in cases:
                path.write_text(json.dumps(doc), encoding='utf-8')
                self.assertEqual(main(['simulate', str(path)]), 2)


if __name__ == '__main__':
    unittest.main()
