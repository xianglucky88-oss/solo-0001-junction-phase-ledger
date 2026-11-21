from .common import integer, require, unique


def reconstruct(control, phases, events, clocks):
    require(isinstance(phases, list) and phases and all(isinstance(x, str) and x for x in phases)
            and len(set(phases)) == len(phases), 'expected a nonempty single-ring phase order')
    rows = sorted(unique(events).values(), key=lambda row: integer(row['seq'], 'sequence', 0))
    for row in rows:
        integer(row['tick'], 'event tick')
    require(len({r['seq'] for r in rows}) == len(rows), 'duplicate controller sequence')
    require(all(a['tick'] < b['tick'] for a, b in zip(rows, rows[1:])), 'controller ticks must increase in sequence order')
    bounds = []
    earliest_seen = None
    for row in rows:
        phase = row['phase']
        require(phase in phases, f'unknown phase: {phase}')
        interval, cert = clocks.point(control, row['tick'])
        if interval is not None:
            require(earliest_seen is None or interval[1] > earliest_seen,
                    'controller clock evidence contradicts sequence order')
            earliest_seen = interval[0] if earliest_seen is None else max(earliest_seen, interval[0])
        bounds.append((row, interval, cert))
    services, gaps = [], []
    for (a, x, p), (b, y, q) in zip(bounds, bounds[1:]):
        why = ('controller_gap' if b['seq'] != a['seq'] + 1 else
               'unexpected_successor' if b['phase'] != phases[(phases.index(a['phase']) + 1) % len(phases)] else
               'unclocked_edge' if x is None or y is None else
               'ambiguous_edge_order' if x[1] >= y[0] else None)
        row = {'edges': [a['id'], b['id']], 'phase': a['phase'], 'clock_refs': [p, q]}
        if why:
            gaps.append(dict(row, reason=why))
        else:
            services.append(dict(row, certain=[x[1], y[0]], possible=[x[0], y[1]], sequence=a['seq']))
    if rows:
        gaps.append({'edges': [rows[-1]['id']], 'reason': 'open_ended'})
    by_seq = {row['sequence']: row for row in services}
    cycles = []
    for row, left, _ in bounds:
        if row['phase'] != phases[0] or left is None:
            continue
        expected = range(row['seq'], row['seq'] + len(phases))
        if all(seq in by_seq for seq in expected):
            members = [by_seq[seq] for seq in expected]
            cycles.append({'start_edge': row['id'], 'certain': [members[0]['certain'][0], members[-1]['certain'][1]],
                           'edges': [m['edges'][0] for m in members] + [members[-1]['edges'][1]]})
    return services, gaps, cycles
