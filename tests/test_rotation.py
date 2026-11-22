import unittest
from fractions import Fraction

from greenbook.rotation import (
    DifferenceSystem,
    LE,
    GE,
    EQ,
    lp_minimize,
    proposal_feasible,
    window_totals,
)

F = Fraction


class SimplexTests(unittest.TestCase):
    def test_free_difference_minimum(self):
        # minimize t1 - t0 with t1 - t0 in [5, 9], free vars split t = p - m.
        # columns p0, m0, p1, m1
        def row(i, j):
            # Coefficients of t_j - t_i under columns p0, m0, p1, m1.
            data = [F(0)] * 4
            data[2 * j] += F(1)
            data[2 * j + 1] -= F(1)
            data[2 * i] -= F(1)
            data[2 * i + 1] += F(1)
            return data

        constraints = [
            (row(0, 1), LE, F(9)),
            (row(1, 0), LE, F(-5)),
        ]
        objective = row(0, 1)
        self.assertEqual(lp_minimize(objective, constraints), F(5))
        # Maximizing t1 - t0 means minimizing t0 - t1.
        self.assertEqual(lp_minimize([-x for x in objective], constraints), F(-9))

    def test_infeasible_constraints(self):
        self.assertIsNone(lp_minimize([F(-1)], [([F(1)], LE, F(1)), ([F(1)], GE, F(2))]))

    def test_equality_and_ge(self):
        # x + y = 5, x >= 2, minimize x -> 2
        constraints = [
            ([F(1), F(1)], EQ, F(5)),
            ([F(1), F(0)], GE, F(2)),
        ]
        self.assertEqual(lp_minimize([F(1), F(0)], constraints), F(2))

    def test_unbounded_reports(self):
        with self.assertRaises(ArithmeticError):
            lp_minimize([F(-1)], [])

    def test_fractional_coefficients(self):
        # x >= 1/2 ; minimize 2x -> 1 (objective 2x, return value 1)
        result = lp_minimize([F(2)], [([F(1)], GE, F(1, 2))])
        self.assertEqual(result, F(1))


class DifferenceSystemTests(unittest.TestCase):
    def test_chain_bounds(self):
        # t0 in [8, 12], t2 in [498, 502], two segments each in [50, 300].
        system = DifferenceSystem(3)
        system.anchor_lower(0, F(8))
        system.anchor_upper(0, F(12))
        system.anchor_lower(2, F(498))
        system.anchor_upper(2, F(502))
        for i in range(2):
            system.edge(i, i + 1, F(300))
            system.edge(i + 1, i, F(-50))
        self.assertTrue(system.solve())
        self.assertEqual(system.point_bounds(0), (F(8), F(12)))
        self.assertEqual(system.point_bounds(2), (F(498), F(502)))
        # t1 must sit between t0+50 and t2-50, and t2-300..t0+300.
        lo, hi = system.point_bounds(1)
        self.assertEqual(lo, F(198))
        self.assertEqual(hi, F(312))
        dlo, dhi = system.diff_bounds(0, 2)
        self.assertEqual((dlo, dhi), (F(486), F(494)))

    def test_negative_cycle(self):
        system = DifferenceSystem(2)
        system.anchor_lower(0, F(0))
        system.anchor_upper(0, F(10))
        system.anchor_lower(1, F(0))
        system.anchor_upper(1, F(10))
        system.edge(0, 1, F(4))   # t1 - t0 <= 4
        system.edge(1, 0, F(-6))  # t0 - t1 <= -6  => t1 - t0 >= 6
        self.assertFalse(system.solve())


class WindowAndProposalTests(unittest.TestCase):
    def make_edges(self):
        # 3 points, two segments (phases N, E), t0 in [0,10], t2 in [90,100],
        # each segment 10..80 long. Anchor node is index 3.
        edges = [
            (0, 1, F(80)),
            (1, 0, F(-10)),
            (1, 2, F(80)),
            (2, 1, F(-10)),
            (3, 0, F(10)), (0, 3, F(0)),
            (3, 2, F(100)), (2, 3, F(-90)),
        ]
        return edges

    def test_window_totals_no_clip(self):
        totals = window_totals(self.make_edges(), 3, ['N', 'E'], ['N', 'E'], (-50, 150))
        self.assertEqual(totals['N'], [F(10), F(80)])
        self.assertEqual(totals['E'], [F(10), F(80)])

    def test_window_clip_lower(self):
        # Window [40, 150): N's counted portion max(0, t1 - 40).
        # N reaches 50 when t1 = 90 (t0 = 10, span 80), 0 when t1 <= 40.
        totals = window_totals(self.make_edges(), 3, ['N', 'E'], ['N', 'E'], (40, 150))
        self.assertEqual(totals['N'][0], F(0))
        self.assertEqual(totals['N'][1], F(50))
        # E inside the window: t1 can be as low as 10, so the maximum is 60.
        self.assertEqual(totals['E'], [F(10), F(60)])

    def test_window_clip_upper(self):
        # Window [0, 60): E counted portion max(0, 60 - t1), at most 50.
        totals = window_totals(self.make_edges(), 3, ['N', 'E'], ['N', 'E'], (0, 60))
        self.assertEqual(totals['E'][0], F(0))
        self.assertEqual(totals['E'][1], F(50))
        self.assertEqual(totals['N'], [F(10), F(60)])

    def test_window_clip_both_boundaries(self):
        # Point t1 in [10, 90] straddles both edges of a tight window.
        totals = window_totals(self.make_edges(), 3, ['N', 'E'], ['N', 'E'], (40, 60))
        # N portion = clamp(t1) - 0 since t0 <= 10 < 40.
        self.assertEqual(totals['N'], [F(0), F(20)])
        # E portion = 60 - clamp(t1), from 0 (t1 >= 60) to 20 (t1 <= 40).
        self.assertEqual(totals['E'], [F(0), F(20)])

    def test_joint_proposal(self):
        edges = self.make_edges()
        # Total span t2-t0 in [80, 100]: N=40, E=40 jointly feasible.
        yes = proposal_feasible(edges, 3, ['N', 'E'], ['N', 'E'], (-50, 150),
                                {'N': F(40), 'E': F(40)})
        self.assertTrue(yes)
        # N=70, E=70 requires span 140, impossible.
        no = proposal_feasible(edges, 3, ['N', 'E'], ['N', 'E'], (-50, 150),
                               {'N': F(70), 'E': F(70)})
        self.assertFalse(no)
        # Individually N=70 is reachable (80 max), but not jointly with E=70.
        per_phase = window_totals(edges, 3, ['N', 'E'], ['N', 'E'], (-50, 150))
        self.assertTrue(per_phase['N'][0] <= F(70) <= per_phase['N'][1])


if __name__ == '__main__':
    unittest.main()
