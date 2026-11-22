"""Rotation-consistent reconstruction across controller sequence gaps.

A gap declared to keep the normal single-ring rotation is modelled as a
chain of unknown edge instants ``t_0 .. t_n`` (``n`` = sequence
difference, so ``n - 1`` edges are missing).  The two endpoint clock
certificates give closed absolute ranges for ``t_0`` and ``t_n``, and
each phase carries minimum/maximum service durations, turning the chain
into a system of difference constraints ``t_j - t_i <= w``.

All arithmetic is exact (``Fraction``).  Reachable point ranges and
segment ranges come from all-pairs shortest paths; window-clipped phase
totals are linear programs solved by a two-phase Fraction simplex,
split into ordering regions so every clamp term resolves linearly.
"""
from fractions import Fraction

from .common import number, require, scalar, span, unique

# Constraint operators used by the exact simplex.
LE, GE, EQ = '<=', '>=', '='


class DifferenceSystem:
    """Difference constraints t_j - t_i <= w with one absolute anchor."""

    def __init__(self, variables):
        # variables t_0 .. t_{variables-1}, plus the anchor node.
        self.anchor = variables
        size = variables + 1
        self.dist = [[Fraction(0) if i == j else None for j in range(size)]
                     for i in range(size)]

    def edge(self, i, j, w):
        """Record t_j - t_i <= w (keep the tightest edge)."""
        cur = self.dist[i][j]
        if cur is None or w < cur:
            self.dist[i][j] = w

    def anchor_upper(self, i, w):
        self.edge(self.anchor, i, w)

    def anchor_lower(self, i, w):
        self.edge(i, self.anchor, -w)

    def solve(self):
        """Floyd-Warshall. Return False when a negative cycle makes it infeasible."""
        d = self.dist
        size = len(d)
        for k in range(size):
            dk = d[k]
            for i in range(size):
                dik = d[i][k]
                if dik is None:
                    continue
                di = d[i]
                for j in range(size):
                    dkj = dk[j]
                    if dkj is None:
                        continue
                    cand = dik + dkj
                    if di[j] is None or cand < di[j]:
                        di[j] = cand
        return not any(d[i][i] < 0 for i in range(size))

    def point_bounds(self, i):
        """Tightest reachable [lower, upper] range of t_i."""
        s = self.anchor
        return -self.dist[i][s], self.dist[s][i]

    def diff_bounds(self, a, b):
        """Tightest [lower, upper] range of t_b - t_a."""
        return -self.dist[b][a], self.dist[a][b]


def lp_minimize(coefficients, constraints):
    """Exact two-phase simplex (Bland's rule), all arithmetic in Fraction.

    Decision variables are non-negative; free callers split one free
    variable into a plus/minus pair.  ``constraints`` are
    ``(coeffs, op, rhs)`` rows.  Return the minimum as ``Fraction`` or
    ``None`` when infeasible.
    """
    nv = len(coefficients)
    normalized = []
    for coeffs, op, rhs in constraints:
        rhs = Fraction(rhs)
        if rhs < 0:  # flip the row so every basic value starts non-negative
            coeffs = [-x for x in coeffs]
            rhs = -rhs
            op = GE if op == LE else LE if op == GE else EQ
        normalized.append(([Fraction(x) for x in coeffs], op, rhs))
    m = len(normalized)
    # Column layout: n decision vars, then m slack/surplus slots (one
    # per row), then one artificial column per GE/EQ row.
    art_start = nv + m
    art_index, art_rows = 0, []
    for coeffs, op, rhs in normalized:
        if op == LE:
            art_rows.append(None)
        else:
            art_rows.append(art_index)
            art_index += 1
    width = art_start + art_index + 1
    rows, basics = [], []
    for k, (coeffs, op, rhs) in enumerate(normalized):
        row = [Fraction(0)] * width
        for j, value in enumerate(coeffs):
            row[j] = value
        if op == LE:
            row[nv + k] = Fraction(1)
            basics.append(nv + k)
        else:
            if op == GE:
                row[nv + k] = Fraction(-1)
            col = art_start + art_rows[k]
            row[col] = Fraction(1)
            basics.append(col)
        row[-1] = rhs
        rows.append(row)
    real_columns = art_start

    def pivot(tableau, basic, row, col):
        target = tableau[row]
        pivot_value = target[col]
        for j in range(width):
            target[j] /= pivot_value
        for i in range(len(tableau)):
            if i == row:
                continue
            factor = tableau[i][col]
            if factor:
                source = tableau[i]
                for j in range(width):
                    source[j] -= factor * target[j]
        basic[row] = col

    def optimize(tableau, basic, row_count, columns):
        objective = tableau[-1]
        while True:
            entering = next((j for j in range(columns) if objective[j] < 0), None)
            if entering is None:
                return
            leaving, best = None, None
            for i in range(row_count):
                coef = tableau[i][entering]
                if coef > 0:
                    ratio = tableau[i][-1] / coef
                    if best is None or ratio < best or (ratio == best and basic[i] < basic[leaving]):
                        best, leaving = ratio, i
            if leaving is None:
                raise ArithmeticError('unbounded linear program')
            pivot(tableau, basic, leaving, entering)

    # Phase 1: w = sum of artificial variables; minimize w.
    phase1_obj = [Fraction(0)] * width
    for art_index in art_rows:
        if art_index is None:
            continue
        art_col = art_start + art_index
        art_row = next(r for r in rows if r[art_col] == 1)
        for j in range(width):
            phase1_obj[j] -= art_row[j]
    tableau = rows + [phase1_obj]
    # Artificial columns never re-enter the basis in phase 1.
    optimize(tableau, basics, m, real_columns)
    if tableau[m][-1] != 0:  # RHS holds minus the artificial sum
        return None
    # Drive any artificial variable still in the basis onto a real
    # column; a row with no usable pivot column is redundant and drops.
    live_rows, live_basics = [], []
    for i, basic in enumerate(basics):
        if basic >= art_start:
            entering = next((j for j in range(real_columns) if tableau[i][j] != 0), None)
            if entering is None:
                continue
            pivot(tableau, basics, i, entering)
        live_rows.append(tableau[i])
        live_basics.append(basics[i])
    # Phase 2: build c x - z = 0 against the current basis, eliminating
    # every basic real column (decisions and slack/surplus alike).
    phase2_obj = [Fraction(0)] * width
    for j, value in enumerate(coefficients):
        phase2_obj[j] = Fraction(value)
    for i, basic in enumerate(live_basics):
        if basic < real_columns:
            factor = phase2_obj[basic]
            if factor:
                source = live_rows[i]
                for j in range(width):
                    phase2_obj[j] -= factor * source[j]
    row_count = len(live_rows)
    tableau = live_rows + [phase2_obj]
    optimize(tableau, live_basics, row_count, real_columns)
    return -tableau[row_count][-1]


def _free_coeffs(expression, variable_count):
    """Expand t_i = tp_i - tm_i into non-negative simplex columns."""
    coeffs = [Fraction(0)] * (2 * variable_count)
    for i, value in enumerate(expression):
        coeffs[2 * i] += value
        coeffs[2 * i + 1] -= value
    return coeffs


def _difference_row(i, j, w, variable_count, anchor):
    """Turn t_j - t_i <= w into simplex columns; the anchor is constant zero."""
    expression = [Fraction(0)] * variable_count
    if j != anchor:
        expression[j] = Fraction(1)
    if i != anchor:
        expression[i] = Fraction(-1)
    return _free_coeffs(expression, variable_count), LE, Fraction(w)


def _region_program(base_edges, split_edges, expressions, variable_count, anchor):
    """Feasibility and per-expression extrema inside one ordering region."""
    edges = base_edges + split_edges
    constraints = [_difference_row(i, j, w, variable_count, anchor) for i, j, w in edges]
    results = []
    zero = [Fraction(0)] * (2 * variable_count)
    if lp_minimize(zero, constraints) is None:
        return None
    for expression, constant in expressions:
        objective = _free_coeffs(expression, variable_count)
        minimum = lp_minimize(objective, constraints)
        maximum = lp_minimize([-x for x in objective], constraints)
        require(minimum is not None and maximum is not None, 'ordering region became infeasible')
        results.append((minimum + constant, -maximum + constant))
    return results


def _make_clamp(point_ranges, variable_count, window):
    """Build a clamp ``t_i -> min(max(t_i, w_lo), w_hi)`` resolver.

    A region's decision dictionary records, for every window boundary
    the point can straddle, whether the point is forced to the low side
    (``t <= boundary``) or the high side (``t >= boundary``).
    """
    w_lo, w_hi = window

    def clamp(i, decision):
        lo, hi = point_ranges[i]
        if lo >= w_hi:  # always at or beyond the upper boundary
            return [Fraction(0)] * variable_count, Fraction(w_hi)
        if hi <= w_lo:  # always at or below the lower boundary
            return [Fraction(0)] * variable_count, Fraction(w_lo)
        if lo >= w_lo and hi <= w_hi:  # whole reachable range inside window
            expression = [Fraction(0)] * variable_count
            expression[i] = Fraction(1)
            return expression, Fraction(0)
        # At least one boundary is straddled; region decisions resolve it.
        if lo < w_lo and decision.get((i, w_lo)) == 'lo_side':
            return [Fraction(0)] * variable_count, Fraction(w_lo)
        if hi > w_hi and decision.get((i, w_hi)) == 'hi_side':
            return [Fraction(0)] * variable_count, Fraction(w_hi)
        expression = [Fraction(0)] * variable_count  # w_lo <= t_i <= w_hi
        expression[i] = Fraction(1)
        return expression, Fraction(0)

    return clamp


def window_totals(base_edges, variable_count, segment_phases, phases, window):
    """Reachable [min, max] total service duration per phase inside window.

    The window is half-open ``[lo, hi)``.  Variables that can straddle a
    window boundary split the feasible space into closed ordering
    regions; within one region every ``min(max(t, lo), hi)`` clamp is a
    fixed variable or constant, so each total is linear.
    """
    probe = DifferenceSystem(variable_count)
    for i, j, w in base_edges:
        probe.edge(i, j, w)
    require(probe.solve(), 'base difference system is infeasible')
    point_ranges = [probe.point_bounds(i) for i in range(variable_count)]
    w_lo, w_hi = window
    splits = []
    for i, (lo, hi) in enumerate(point_ranges):
        for boundary in (w_lo, w_hi):
            if lo < boundary < hi:
                splits.append((i, boundary))
    clamp = _make_clamp(point_ranges, variable_count, window)
    extrema = {phase: [None, None] for phase in phases}

    def walk(index, decision, split_edges):
        if index == len(splits):
            regional = []
            for phase in phases:
                expression = [Fraction(0)] * variable_count
                constant = Fraction(0)
                for i, served in enumerate(segment_phases):
                    if served != phase:
                        continue
                    left, left_const = clamp(i, decision)
                    right, right_const = clamp(i + 1, decision)
                    for k in range(variable_count):
                        expression[k] += right[k] - left[k]
                    constant += right_const - left_const
                regional.append((expression, constant))
            found = _region_program(base_edges, split_edges, regional,
                                    variable_count, probe.anchor)
            if found is None:
                return
            for phase, (minimum, maximum) in zip(phases, found):
                slot = extrema[phase]
                slot[0] = minimum if slot[0] is None else min(slot[0], minimum)
                slot[1] = maximum if slot[1] is None else max(slot[1], maximum)
            return
        i, boundary = splits[index]
        decision_lo = {**decision, (i, boundary): 'lo_side'}
        walk(index + 1, decision_lo,
             split_edges + [(probe.anchor, i, Fraction(boundary))])  # t_i <= boundary
        decision_hi = {**decision, (i, boundary): 'hi_side'}
        walk(index + 1, decision_hi,
             split_edges + [(i, probe.anchor, Fraction(-boundary))])  # t_i >= boundary

    walk(0, {}, [])
    return extrema


def proposal_feasible(base_edges, variable_count, segment_phases, phases, window, proposed):
    """Joint feasibility check: one timeline meeting every proposed total."""
    probe = DifferenceSystem(variable_count)
    for i, j, w in base_edges:
        probe.edge(i, j, w)
    require(probe.solve(), 'base difference system is infeasible')
    point_ranges = [probe.point_bounds(i) for i in range(variable_count)]
    w_lo, w_hi = window
    splits = []
    for i, (lo, hi) in enumerate(point_ranges):
        for boundary in (w_lo, w_hi):
            if lo < boundary < hi:
                splits.append((i, boundary))
    clamp = _make_clamp(point_ranges, variable_count, window)
    answers = []

    def walk(index, decision, split_edges):
        if answers:
            return
        if index == len(splits):
            constraints = []
            for i, j, w in base_edges + split_edges:
                constraints.append(_difference_row(i, j, w, variable_count, probe.anchor))
            for phase, target in proposed.items():
                expression = [Fraction(0)] * variable_count
                constant = Fraction(0)
                for i, served in enumerate(segment_phases):
                    if served != phase:
                        continue
                    left, lc = clamp(i, decision)
                    right, rc = clamp(i + 1, decision)
                    for k in range(variable_count):
                        expression[k] += right[k] - left[k]
                    constant += rc - lc
                row = _free_coeffs(expression, variable_count)
                constraints.append((row, EQ, Fraction(target) - constant))
            zero = [Fraction(0)] * (2 * variable_count)
            if lp_minimize(zero, constraints) is not None:
                answers.append(True)
            return
        i, boundary = splits[index]
        decision_lo = {**decision, (i, boundary): 'lo_side'}
        walk(index + 1, decision_lo,
             split_edges + [(probe.anchor, i, Fraction(boundary))])  # t_i <= boundary
        decision_hi = {**decision, (i, boundary): 'hi_side'}
        walk(index + 1, decision_hi,
             split_edges + [(i, probe.anchor, Fraction(-boundary))])  # t_i >= boundary

    walk(0, {}, [])
    return bool(answers)


def _scaled(value):
    return scalar(Fraction(value))


def analyze_rotation(doc, gap_rows, clocks, phases):
    """Build rotation-gap analyses; return (entries, reconstructed services)."""
    if 'rotation_gaps' not in doc:
        return None, []
    raw = doc['rotation_gaps']
    require(isinstance(raw, list), 'rotation_gaps must be a list')
    if not raw:
        return [], []
    unique(raw)
    edge_index = {row['id']: row for row in doc['edges']}
    controller = doc['controller']
    gap_pairs = {(row['edges'][0], row['edges'][1]): row
                 for row in gap_rows if row['reason'] == 'controller_gap'}
    entries, reconstructed = [], []
    phase_count = len(phases)
    for item in raw:
        require(isinstance(item, dict), 'rotation gap entry must be an object')
        key = item['id']
        require(item.get('controller') == controller, f'rotation gap controller mismatch: {key}')
        pair = item.get('edges')
        require(isinstance(pair, list) and len(pair) == 2 and all(x in edge_index for x in pair),
                f'rotation gap must reference two known edges: {key}')
        left_edge, right_edge = pair
        require((left_edge, right_edge) in gap_pairs,
                f'rotation gap must reference a controller sequence gap: {key}')
        window = span(item['observation_window_ms'], 'observation window')
        limits_raw = item.get('phase_duration_limits_ms')
        require(isinstance(limits_raw, list), f'missing phase duration limits: {key}')
        limits = {}
        for limit in limits_raw:
            phase = limit.get('phase')
            require(phase in phases, f'unknown rotation limit phase: {key}')
            require(phase not in limits, f'duplicate rotation limit phase: {key}')
            minimum = number(limit.get('min_ms'), 'minimum duration')
            maximum = number(limit.get('max_ms'), 'maximum duration')
            require(minimum > 0 and maximum >= minimum, f'invalid rotation duration limit: {key}')
            limits[phase] = (minimum, maximum)
        require(set(limits) == set(phases), f'every phase needs duration limits: {key}')
        proposal_raw = item.get('proposal')
        proposal = None
        if proposal_raw is not None:
            values = proposal_raw.get('durations_ms')
            require(isinstance(values, dict) and values, f'invalid proposal: {key}')
            proposal = {}
            for phase, value in values.items():
                require(phase in phases, f'unknown proposal phase: {key}')
                target = number(value, 'proposal duration')
                require(target >= 0, f'proposal duration below zero: {key}')
                proposal[phase] = target

        left, right = edge_index[left_edge], edge_index[right_edge]
        seq_l, seq_r = left['seq'], right['seq']
        segment_count = seq_r - seq_l
        missing_count = segment_count - 1
        base = {'id': key, 'controller': controller, 'edges': [left_edge, right_edge],
                'missing_edge_count': missing_count, 'window_ms': list(window)}
        left_phase_index = phases.index(left['phase'])
        segment_phases = [phases[(left_phase_index + i) % phase_count]
                          for i in range(segment_count)]
        missing_edges = [{'id': f'{left_edge}->{right_edge}:m{k}',
                          'seq': seq_l + k,
                          'phase': phases[(left_phase_index + k) % phase_count]}
                         for k in range(1, segment_count)]
        base['missing_edges'] = missing_edges
        left_cert, left_ref = clocks.point(controller, left['tick'])
        right_cert, right_ref = clocks.point(controller, right['tick'])
        if left_cert is None or right_cert is None:
            entries.append(dict(base, status='unproved', reason='endpoint_unclocked',
                                clock_refs=[left_ref, right_ref], services=None,
                                phase_duration_bounds_ms=None,
                                proposal=_unproved_proposal(proposal)))
            continue
        if segment_phases[-1] != right['phase']:
            entries.append(dict(base, status='unproved', reason='rotation_conflict',
                                clock_refs=[left_ref, right_ref], services=None,
                                phase_duration_bounds_ms=None,
                                proposal=_unproved_proposal(proposal)))
            continue

        variables = segment_count + 1
        anchor = variables
        base_edges = [
            (anchor, 0, Fraction(left_cert[1])), (0, anchor, Fraction(-left_cert[0])),
            (anchor, segment_count, Fraction(right_cert[1])),
            (segment_count, anchor, Fraction(-right_cert[0])),
        ]
        for i, phase in enumerate(segment_phases):
            minimum, maximum = limits[phase]
            base_edges.append((i, i + 1, Fraction(maximum)))
            base_edges.append((i + 1, i, Fraction(-minimum)))
        system = DifferenceSystem(variables)
        for i, j, w in base_edges:
            system.edge(i, j, w)
        if not system.solve():
            entries.append(dict(base, status='infeasible', reason='no_feasible_timeline',
                                clock_refs=[left_ref, right_ref], services=None,
                                phase_duration_bounds_ms=None,
                                proposal=_unproved_proposal(proposal, 'no_feasible_timeline')))
            continue

        services = []
        for i, phase in enumerate(segment_phases):
            lo_i, hi_i = system.point_bounds(i)
            lo_j, hi_j = system.point_bounds(i + 1)
            duration_min, duration_max = system.diff_bounds(i, i + 1)
            certain = [hi_i, lo_j] if hi_i < lo_j else None
            service = {'id': f'{left_edge}->{right_edge}:s{i}',
                       'edges': [left_edge if i == 0 else missing_edges[i - 1]['id'],
                                 missing_edges[i]['id'] if i < missing_count else right_edge],
                       'phase': phase, 'sequence': seq_l + i,
                       'possible': [_scaled(lo_i), _scaled(hi_j)],
                       'certain': [_scaled(certain[0]), _scaled(certain[1])] if certain else None,
                       'duration_bounds_ms': [_scaled(duration_min), _scaled(duration_max)],
                       'clock_refs': [left_ref, right_ref], 'reconstructed': True}
            services.append(service)
            reconstructed.append(service)

        totals = window_totals(base_edges, variables, segment_phases, phases, window)
        bounds = {phase: {'min_ms': _scaled(values[0]), 'max_ms': _scaled(values[1])}
                  for phase, values in totals.items()}
        proposal_block = None
        if proposal is not None:
            per_phase = {phase: _scaled(totals[phase][0]) <= target <= totals[phase][1]
                         for phase, target in proposal.items()}
            joint = proposal_feasible(base_edges, variables, segment_phases, phases, window, proposal)
            proposal_block = {'values_ms': {phase: _scaled(target) for phase, target in proposal.items()},
                              'per_phase_reachable': per_phase,
                              'simultaneously_achievable': joint,
                              'reason': None if joint else 'no_joint_timeline'}
        entries.append(dict(base, status='feasible', reason=None,
                            clock_refs=[left_ref, right_ref], services=services,
                            phase_duration_bounds_ms=bounds, proposal=proposal_block))
    return entries, reconstructed


def _unproved_proposal(proposal, reason='unproved_rotation_gap'):
    if proposal is None:
        return None
    return {'values_ms': {phase: _scaled(target) for phase, target in proposal.items()},
            'per_phase_reachable': None, 'simultaneously_achievable': None, 'reason': reason}
