from .common import integer, require, unique
from .preempt import parse_preemptions


PUBLIC_CYCLE = ('id', 'start_edge', 'phase', 'edges', 'certain', 'possible', 'statistics')
PUBLIC_PARTIAL = ('id', 'edges', 'start_phase', 'end_phase', 'certain_ms', 'possible_ms',
                  'missing_head', 'missing_tail', 'reason', 'statistics')
PUBLIC_UNKNOWN = ('id', 'edges', 'start_phase', 'end_phase', 'reason',
                  'certain_ms', 'possible_ms', 'statistics')
PUBLIC_PREEMPT = ('id', 'entry_edge', 'resume_edge', 'resume_phase', 'edges',
                  'entry_time_ms', 'exit_time_ms', 'clock_refs',
                  'certain_ms', 'possible_ms', 'open', 'statistics')


def reconstruct(control, phases, events, clocks, preempt_raw=None):
    require(isinstance(phases, list) and phases and all(isinstance(x, str) and x for x in phases)
            and len(set(phases)) == len(phases), 'expected a nonempty single-ring phase order')
    rows = sorted(unique(events).values(), key=lambda row: integer(row['seq'], 'sequence', 0))
    preempts = parse_preemptions(control, phases, preempt_raw, clocks)
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

    membership, stranded, resume_of = classify_regime(bounds, preempts)
    services, gaps, by_seq = [], [], {}
    for index, ((a, x, p), (b, y, q)) in enumerate(zip(bounds, bounds[1:])):
        if membership[index] is not None or membership[index + 1] is not None or stranded[index + 1]:
            continue  # preemption boundary/interior, or an edge that may not rejoin the ring
        why = ('controller_gap' if b['seq'] != a['seq'] + 1 else
               'unexpected_successor' if b['phase'] != phases[(phases.index(a['phase']) + 1) % len(phases)] else
               'unclocked_edge' if x is None or y is None else
               'ambiguous_edge_order' if x[1] >= y[0] else None)
        row = {'edges': [a['id'], b['id']], 'phase': a['phase'], 'clock_refs': [p, q]}
        if why:
            gaps.append(dict(row, reason=why))
        else:
            service = dict(row, certain=[x[1], y[0]], possible=[x[0], y[1]], sequence=a['seq'])
            services.append(service)
            by_seq[a['seq']] = service
    if bounds and membership[-1] is None and not stranded[-1]:
        gaps.append({'edges': [bounds[-1][0]['id']], 'reason': 'open_ended'})

    ring = build_runs(bounds, membership, stranded)
    cycles, full_run_partials, cycle_starts, cycle_ends = split_runs(bounds, phases, by_seq, ring)
    for cycle in cycles:
        members = [by_seq[seq] for seq in cycle['_sequences']]
        cycle['edges'] = [m['edges'][0] for m in members] + [members[-1]['edges'][1]]
        cycle['certain'] = [members[0]['certain'][0], members[-1]['certain'][1]]
        cycle['possible'] = [members[0]['possible'][0], members[-1]['possible'][1]]
        cycle['statistics'] = None
    head, tail = uncovered_partials(bounds, ring, cycle_starts, cycle_ends, membership, stranded)
    partials = full_run_partials + head + tail
    partials.sort(key=lambda item: item['_indexes'][0])
    unclassified = build_unclassified(bounds, membership, stranded, ring, partials)
    preempt_segments = build_preempt_segments(bounds, preempts, membership, resume_of)
    segments = {
        'normal_cycles': [{k: cycle[k] for k in PUBLIC_CYCLE} for cycle in cycles],
        'normal_partials': [{k: partial[k] for k in PUBLIC_PARTIAL} for partial in partials],
        'preemptions': [{k: segment[k] for k in PUBLIC_PREEMPT} for segment in preempt_segments],
        'unclassified': [{k: segment[k] for k in PUBLIC_UNKNOWN} for segment in unclassified],
    }
    legacy_cycles = [{'start_edge': cycle['start_edge'], 'certain': cycle['certain'],
                      'edges': cycle['edges']} for cycle in cycles]
    return services, gaps, legacy_cycles, segments


def classify_regime(bounds, preempts):
    """Assign every edge to a preemption, or mark it unable to rejoin the ring."""
    membership, stranded = [None] * len(bounds), [False] * len(bounds)
    resume_of = {}
    for preempt in preempts:
        inside = [i for i, (row, _, _) in enumerate(bounds)
                  if preempt['entry_tick'] <= row['tick'] < preempt['exit_tick']]
        for i in inside:
            require(membership[i] is None, 'preemption spans overlap over an edge')
            membership[i] = preempt['id']
        if preempt['resume_phase'] is not None:
            resume = next(((i, row) for i, (row, _, _) in enumerate(bounds)
                           if row['tick'] >= preempt['exit_tick']), None)
            if resume is not None:
                i, row = resume
                require(row['phase'] == preempt['resume_phase'],
                        f"resume phase evidence contradicted by edge {row['id']}")
                require(membership[i] is None, 'resume edge is held inside another preemption')
                resume_of[preempt['id']] = i
        else:
            for i, (row, _, _) in enumerate(bounds):
                if row['tick'] >= preempt['exit_tick']:
                    stranded[i] = True
    return membership, stranded, resume_of


def build_runs(bounds, membership, stranded):
    """Maximal consecutive ordinary edges joined by confirmed service pairs."""
    runs, singletons = [], []
    linked = {i for i in range(len(bounds) - 1)
              if membership[i] is None and membership[i + 1] is None
              and not stranded[i] and not stranded[i + 1]
              and bounds[i + 1][0]['seq'] == bounds[i][0]['seq'] + 1
              and bounds[i][1] is not None and bounds[i + 1][1] is not None
              and bounds[i][1][1] < bounds[i + 1][1][0]}
    current = []
    for i in range(len(bounds)):
        if membership[i] is not None or stranded[i]:
            if current:
                (runs if len(current) > 1 else singletons).append(current)
                current = []
            continue
        if not current:
            current = [i]
        elif i - 1 in linked:
            current.append(i)
        else:
            (runs if len(current) > 1 else singletons).append(current)
            current = [i]
    if current:
        (runs if len(current) > 1 else singletons).append(current)
    return {'runs': runs, 'singletons': singletons}


def split_runs(bounds, phases, by_seq, ring):
    cycles, partials, cycle_starts, cycle_ends = [], [], set(), set()
    width = len(phases)
    for run in ring['runs']:
        run_cycles = []
        # Candidate starts leave width more edges inside the run (width + 1
        # edges total), so the last width - 1 positions cannot start a cycle.
        for position, index in enumerate(run[:len(run) - width + 1]):
            row, left, _ = bounds[index]
            if row['phase'] != phases[0] or left is None:
                continue
            # The run is itself built from confirmed, sequence-consecutive
            # service pairs, so the first seq of each pair must be in by_seq.
            # Sequence numbers may have holes (a preemption consumes whole
            # sequences), therefore membership is checked by run position,
            # never via a bare arithmetic seq range.
            service_starts = [bounds[run[position + offset]][0]['seq'] for offset in range(width)]
            if all(seq in by_seq for seq in service_starts):
                run_cycles.append({'id': f"cycle-{row['id']}", 'start_edge': row['id'],
                                   'phase': phases[0], '_start': index,
                                   '_end': run[position + width], '_sequences': service_starts})
        cycles.extend(run_cycles)
        for cycle in run_cycles:
            cycle_starts.add(cycle['_start'])
            cycle_ends.add(cycle['_end'])
        if not run_cycles:
            partials.append(make_partial(bounds, run, head=True, tail=True, reason='no_complete_cycle'))
    return cycles, partials, cycle_starts, cycle_ends


def boundary_reason(bounds, index, side, membership, stranded):
    """Why an ordinary run stops at this edge."""
    neighbor = index + 1 if side == 'tail' else index - 1
    if 0 <= neighbor < len(bounds) and (membership[neighbor] is not None or stranded[neighbor]):
        return 'preempted_head' if side == 'head' else 'preempted_tail'
    return 'open_head' if side == 'head' else 'open_tail'


def make_partial(bounds, indexes, head, tail, reason=None):
    first, last = indexes[0], indexes[-1]
    certain, possible = pair_window(bounds, first, last) if last > first else (None, None)
    return {'id': f"partial-{bounds[first][0]['id']}-{bounds[last][0]['id']}",
            'edges': [bounds[i][0]['id'] for i in indexes],
            '_indexes': list(indexes),
            'start_phase': bounds[first][0]['phase'], 'end_phase': bounds[last][0]['phase'],
            'certain_ms': certain, 'possible_ms': possible,
            'missing_head': head, 'missing_tail': tail,
            'reason': reason or ('open_head' if head else 'open_tail'), 'statistics': None}


def pair_window(bounds, first, last):
    x, y = bounds[first][1], bounds[last][1]
    if x is None or y is None:
        return None, None
    return [x[1], y[0]], [x[0], y[1]]


def uncovered_partials(bounds, ring, cycle_starts, cycle_ends, membership, stranded):
    """Split runs around cycles using half-open tiling.

    A cycle spans ``width`` service intervals; its closing edge is also the
    first edge of the following segment, so a tail partial begins on that
    closing edge rather than after it.
    """
    head, tail = [], []
    for run in ring['runs']:
        starts = [i for i in run if i in cycle_starts]
        ends = [i for i in run if i in cycle_ends]
        if not starts:
            continue
        first_cycle, last_cycle = min(starts), max(ends)
        head_indexes = [i for i in run if i < first_cycle]
        if head_indexes:
            # The cycle's opening edge closes the last service interval of
            # the preceding partial, so it belongs to that partial as well.
            head_indexes = head_indexes + [first_cycle]
        tail_indexes = [i for i in run if i > last_cycle]
        if tail_indexes:
            tail_indexes = [last_cycle] + tail_indexes
        # Adjacent cycles leave no service interval for a partial; a single
        # shared boundary edge must not be emitted as its own empty segment.
        if len(head_indexes) >= 2:
            reason = boundary_reason(bounds, head_indexes[0], 'head', membership, stranded)
            head.append(make_partial(bounds, head_indexes, head=True, tail=False, reason=reason))
        if len(tail_indexes) >= 2:
            reason = boundary_reason(bounds, tail_indexes[-1], 'tail', membership, stranded)
            tail.append(make_partial(bounds, tail_indexes, head=False, tail=True, reason=reason))
    return head, tail


def build_unclassified(bounds, membership, stranded, ring, partials):
    in_run, in_partial = set(), set()
    for run in ring['runs']:
        in_run.update(run)
    for partial in partials:
        in_partial.update(partial['_indexes'])
    chains, current, reason = [], [], 'no_confirmed_service'
    for i in range(len(bounds)):
        if membership[i] is not None or i in in_run or i in in_partial:
            if current:
                chains, current = chains + [(current, reason)], []
            continue
        edge_reason = 'no_resume_evidence' if stranded[i] else 'no_confirmed_service'
        if not current:
            reason = edge_reason
        current.append(i)
    if current:
        chains.append((current, reason))
    output = []
    for indexes, why in chains:
        certain, possible = pair_window(bounds, indexes[0], indexes[-1]) if len(indexes) > 1 else (None, None)
        output.append({'id': f"unclassified-{bounds[indexes[0]][0]['id']}-{bounds[indexes[-1]][0]['id']}",
                       'edges': [bounds[i][0]['id'] for i in indexes],
                       'start_phase': bounds[indexes[0]][0]['phase'],
                       'end_phase': bounds[indexes[-1]][0]['phase'], 'reason': why,
                       'certain_ms': certain, 'possible_ms': possible, 'statistics': None})
    return output


def build_preempt_segments(bounds, preempts, membership, resume_of):
    output = []
    for preempt in preempts:
        inside = [i for i, owner in enumerate(membership) if owner == preempt['id']]
        entry_edge = next((i for i in inside if bounds[i][0]['tick'] == preempt['entry_tick']),
                          inside[0] if inside else None)
        resume = resume_of.get(preempt['id'])
        open_segment = preempt['exit_tick'] > bounds[-1][0]['tick'] if bounds else True
        certain, possible = None, None
        lo, hi = preempt['entry_time_ms'], preempt['exit_time_ms']
        if lo is not None and hi is not None and hi[1] > lo[0]:
            certain, possible = [lo[1], hi[0]], [lo[0], hi[1]]
        output.append({'id': preempt['id'],
                       'entry_edge': bounds[entry_edge][0]['id'] if entry_edge is not None else None,
                       'resume_edge': bounds[resume][0]['id'] if resume is not None else None,
                       'resume_phase': preempt['resume_phase'],
                       'edges': [bounds[i][0]['id'] for i in inside],
                       'entry_time_ms': preempt['entry_time_ms'],
                       'exit_time_ms': preempt['exit_time_ms'],
                       'clock_refs': preempt['clock_refs'],
                       'certain_ms': certain, 'possible_ms': possible,
                       'open': bool(open_segment), 'statistics': None})
    return output
