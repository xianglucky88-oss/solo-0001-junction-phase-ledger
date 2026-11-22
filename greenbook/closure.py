"""Lane-closure simulation.

A closure exercises two turning restriction lines at once, so a verdict can
never be assembled from one per-turn maximum: the whole turn-demand group is
checked together. Field count windows only become simultaneous constraints
when they are explicitly marked as one arrival batch and their full span is
supported by a single wiring segment plus heartbeat coverage. Unknown demand
stays ``null``; closing a lane never turns an unobserved demand into zero.
"""
from .common import require, span, unique

SUBTOTAL = 'subtotal'
TOTAL = 'total'
SCOPES = (SUBTOTAL, TOTAL)

FEASIBLE = 'feasible'
CONTRADICTED = 'contradicted'
UNDETERMINED = 'undetermined'

# Output vocabulary for records that cannot serve as a joint constraint.
UNBATCHED = 'unbatched'
UNWIRED = 'unwired'
REWIRE_STRADDLE = 'rewire_straddle'
HEARTBEAT_GAP = 'heartbeat_gap'
PHASE_MISMATCH = 'phase_mismatch'
NOT_ALIGNED = 'not_aligned'


def _demand(value, turn):
    if value is None:
        return None
    require(type(value) is int and value >= 0, f'demand for {turn} must be a nonnegative integer or null')
    return value


def parse_turns(rows, phases):
    require(isinstance(rows, list) and rows, 'turns must be a nonempty list')
    turns = []
    for row in rows:
        require(isinstance(row, dict), 'turn entry must be an object')
        tid = row.get('id')
        phase = row.get('phase')
        require(isinstance(tid, str) and tid.strip(), 'invalid turn id')
        require(phase in phases, f'turn {tid} references unknown phase: {phase!r}')
        turns.append({'id': tid, 'phase': phase})
    require(len({t['id'] for t in turns}) == len(turns), 'duplicate turn id')
    return turns


def parse_windows(rows, turn_ids):
    require(isinstance(rows, list) and rows, 'count_windows must be a nonempty list')
    windows, batch_span = [], {}
    for row in unique(rows).values():
        lo, hi = span(row['time_ms'], 'count window')
        batch = row.get('batch')
        require(batch is None or (isinstance(batch, str) and batch.strip()),
                f'count window {row["id"]} has an invalid batch mark')
        scope = row.get('scope')
        require(scope in SCOPES, f'count window {row["id"]} has invalid scope: {scope!r}')
        observed = row.get('turns')
        require(isinstance(observed, list) and observed, f'count window {row["id"]} needs observable turns')
        require(len(observed) == len(set(observed)) and all(t in turn_ids for t in observed),
                f'count window {row["id"]} references unknown or repeated turns')
        require(len(observed) == 1 if scope == SUBTOTAL else len(observed) >= 2,
                f'count window {row["id"]} turn set does not match its scope')
        count = row.get('count')
        require(type(count) is int and count >= 0, f'count window {row["id"]} count must be a nonnegative integer')
        detector = row.get('detector')
        require(isinstance(detector, str) and detector.strip(), f'count window {row["id"]} needs a detector')
        if batch is not None:
            current = batch_span.setdefault(batch, (lo, hi))
            require(current == (lo, hi), f'batch {batch} mixes count windows with different time spans')
        windows.append({'id': row['id'], 'batch': batch, 'detector': detector, 'lo': lo, 'hi': hi,
                        'scope': scope, 'turns': list(observed), 'count': count})
    return windows


def parse_wirings(rows):
    segments = []
    for row in unique(rows).values():
        lo, hi = span(row['time_ms'], 'wiring')
        segments.append({'id': row['id'], 'detector': row['detector'], 'phase': row['phase'], 'lo': lo, 'hi': hi})
    return segments


def parse_closures(rows, turn_ids):
    require(isinstance(rows, list) and rows, 'closures must be a nonempty list')
    closures = []
    for row in unique(rows).values():
        lo, hi = span(row['time_ms'], 'closure span')
        restrictions = row.get('restrictions')
        require(isinstance(restrictions, list) and len(restrictions) == 2,
                f'closure {row["id"]} must run exactly two turning restriction lines')
        lines = []
        for line in restrictions:
            turn = line.get('turn')
            require(turn in turn_ids, f'closure {row["id"]} restricts unknown turn: {turn!r}')
            cap = line.get('cap')
            require(type(cap) is int and cap >= 0, f'closure {row["id"]} cap must be a nonnegative integer')
            lines.append({'turn': turn, 'cap': cap})
        require(len({l['turn'] for l in lines}) == 2,
                f'closure {row["id"]} restriction lines must target distinct turns')
        closures.append({'id': row['id'], 'lo': lo, 'hi': hi, 'restrictions': lines})
    return closures


def parse_plans(rows, turn_ids, closure_ids):
    require(isinstance(rows, list) and rows, 'demand_plans must be a nonempty list')
    plans = []
    expected = set(turn_ids)
    for row in unique(rows).values():
        closure = row.get('closure')
        require(closure in closure_ids, f'plan {row["id"]} references unknown closure: {closure!r}')
        demands = row.get('demands')
        require(isinstance(demands, dict), f'plan {row["id"]} demands must be an object')
        require(set(demands) == expected, f'plan {row["id"]} must submit the whole turn group, no more and no less')
        values = {turn: _demand(demands[turn], turn) for turn in turn_ids}
        plans.append({'id': row['id'], 'closure': closure, 'demands': values})
    return plans


def _covers(pieces, lo, hi):
    cursor = lo
    for a, b in sorted(pieces):
        if a > cursor:
            return False
        cursor = max(cursor, b)
        if cursor >= hi:
            return True
    return False


def support_reasons(window, wirings, cover_index, turn_phase):
    """Why a window cannot be trusted; an empty list means it is admissible."""
    reasons = []
    if window['batch'] is None:
        reasons.append(UNBATCHED)
    lo, hi = window['lo'], window['hi']
    hits = [w for w in wirings
            if w['detector'] == window['detector'] and w['lo'] < hi and lo < w['hi']]
    if not hits:
        reasons.append(UNWIRED)
        return reasons
    holder = next((w for w in hits if w['lo'] <= lo and hi <= w['hi']), None)
    if holder is None or len(hits) != 1:
        reasons.append(REWIRE_STRADDLE)
        return reasons
    if not _covers(cover_index.get((window['detector'], holder['id']), ()), lo, hi):
        reasons.append(HEARTBEAT_GAP)
    if any(turn_phase[turn] != holder['phase'] for turn in window['turns']):
        reasons.append(PHASE_MISMATCH)
    return reasons


def _marginal(eligible):
    bounds = {}
    for window in eligible:
        if window['scope'] != SUBTOTAL:
            continue
        turn = window['turns'][0]
        count = window['count']
        if turn not in bounds:
            bounds[turn] = [count, count]
        else:
            bounds[turn] = [min(bounds[turn][0], count), max(bounds[turn][1], count)]
    return bounds


def _evaluate(closure, plan, turns, windows, wirings, cover_index):
    turn_ids = [t['id'] for t in turns]
    turn_phase = {t['id']: t['phase'] for t in turns}
    window_span = [closure['lo'], closure['hi']]
    reported, eligible = [], []
    for window in windows:
        reasons = support_reasons(window, wirings, cover_index, turn_phase)
        aligned = (window['lo'], window['hi']) == tuple(window_span)
        if not aligned:
            reasons.append(NOT_ALIGNED)
        if aligned and not reasons:
            eligible.append(window)
        reported.append({'id': window['id'], 'batch': window['batch'],
                         'eligible': aligned and not reasons, 'reasons': reasons})
    bounds = _marginal(eligible)
    marginals = {turn: (None if turn not in bounds else {'min': bounds[turn][0], 'max': bounds[turn][1]})
                 for turn in turn_ids}
    demands = plan['demands']
    violations, missing = [], []

    for turn in turn_ids:
        value, bound = demands[turn], bounds.get(turn)
        if bound is None:
            missing.append({'kind': 'marginal', 'turn': turn,
                            'reason': 'unobserved_demand' if value is None else 'no_marginal_observation'})
        elif value is None:
            missing.append({'kind': 'marginal', 'turn': turn, 'reason': 'unknown_demand'})
        elif not bound[0] <= value <= bound[1]:
            violations.append({'kind': 'marginal', 'turn': turn,
                               'min': bound[0], 'max': bound[1], 'actual': value})

    restrictions = []
    for line in closure['restrictions']:
        turn, cap, value = line['turn'], line['cap'], demands[line['turn']]
        restrictions.append({'turn': turn, 'cap': cap, 'demand': value,
                             'within_cap': None if value is None else value <= cap})
        if value is None:
            missing.append({'kind': 'cap', 'turn': turn, 'reason': 'cap_uncheckable'})
        elif value > cap:
            violations.append({'kind': 'cap', 'turn': turn, 'cap': cap, 'actual': value})

    for window in sorted((w for w in eligible if w['scope'] == TOTAL), key=lambda w: w['id']):
        unknown = [turn for turn in window['turns'] if demands[turn] is None]
        if unknown:
            missing.append({'kind': 'total', 'record': window['id'], 'batch': window['batch'],
                            'reason': 'unknown_member', 'turns': unknown})
            continue
        actual = sum(demands[turn] for turn in window['turns'])
        if actual != window['count']:
            violations.append({'kind': 'total', 'record': window['id'], 'batch': window['batch'],
                               'turns': list(window['turns']), 'expected': window['count'], 'actual': actual})

    if violations:
        status = CONTRADICTED
    elif missing:
        status = UNDETERMINED
    else:
        status = FEASIBLE
    return {'closure': closure['id'], 'plan': plan['id'], 'status': status, 'span': window_span,
            'restrictions': restrictions, 'marginals': marginals,
            'windows': sorted(reported, key=lambda r: r['id']),
            'violations': violations, 'missing_observations': missing,
            'contradicts': sorted({v['batch'] for v in violations if v['kind'] == 'total'})}


def simulate(doc, coverage):
    """Pure evaluation over the coverage already trusted by the evidence pipeline."""
    if not any(key in doc for key in ('turns', 'count_windows', 'closures', 'demand_plans')):
        return []
    turns = parse_turns(doc.get('turns'), doc['phase_order'])
    turn_ids = [t['id'] for t in turns]
    windows = parse_windows(doc.get('count_windows', []), turn_ids)
    wirings = parse_wirings(doc['wiring'])
    closures = parse_closures(doc.get('closures', []), turn_ids)
    plans = parse_plans(doc.get('demand_plans', []), turn_ids, {c['id'] for c in closures})
    cover_index = {}
    for row in coverage:
        cover_index.setdefault((row['detector'], row['wiring']), []).append(tuple(row['time_ms']))
    results = []
    for closure in closures:
        for plan in plans:
            if plan['closure'] == closure['id']:
                results.append(_evaluate(closure, plan, turns, windows, wirings, cover_index))
    return results
