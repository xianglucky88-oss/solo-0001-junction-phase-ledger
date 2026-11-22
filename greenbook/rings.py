"""Dual-ring controllers: two rings that must meet at one agreed crossing point.

A dual-ring bundle describes exactly two rings.  Each ring has its own
controller device, phase order and edge log, and is reconstructed by the
ordinary single-ring engine, so a ring's ``certain``/``possible`` services, gaps
and cycles keep exactly the meaning they have in a single-ring edition.  The
joint layer only *classifies* windows; it never edits, merges away or revokes a
per-ring service.

Each report bucket is classified as:

``certain``
    both rings certainly hold their crossing phase at one shared instant, so
    the meeting holds in every legal schedule.
``possible``
    some legal schedule brings both rings to the crossing point together, but
    not every schedule does.  Both arrival orders are considered and the
    reported ``orderings`` come from the timing intervals, never from the
    read-in order of the rings.
``conflict``
    each ring has its own legal crossing service in this window, yet their
    service windows never co-occur, so the rings cannot meet at the same
    crossing point.
``incomplete``
    a ring has no crossing service in this window because of missing reports
    (a controller gap).  Other windows, including earlier ones, are untouched.
``unproved``
    a ring has no crossing service and no recorded gap to blame.

A ring may wait while the other arrives, so its crossing window only has to
share one instant with the peer's.  Waiting cannot bridge two disjoint windows,
because a ring leaves its crossing phase when the service that proves it ends.
Classification is per window and per ring, so services already proved in an
earlier window are never withdrawn.

Bundle shape
------------
``rings`` holds exactly two objects with distinct ``id`` and a ``phase_order``,
plus their own ``controller`` (or one shared string ``controller``).  Ring
phases must be disjoint and ``phase_order`` must list each of them once.
``crossing`` names the phase each ring must hold: either ``{"id": ..., "rings":
{rid: phase}}`` or ``{"phases": [..]}``/a list aligned with ``rings``.  Edges
carry ``ring`` or, when phase names are unique across rings, are assigned by
``phase``; they may also sit in each ring object's own ``edges``.
"""

from .common import pieces, require, span, unique
from .observed import collect
from .report import summarize
from .service import reconstruct


def _merge(windows):
    merged = []
    for lo, hi in sorted(windows):
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return merged


def is_dual(doc):
    """Whether the bundle declares two rings and a crossing point."""
    return isinstance(doc.get('rings'), (list, dict)) or 'crossing' in doc \
        or isinstance(doc.get('controller'), dict)


def _clipped(windows, window):
    return _merge([list(section) for item in windows if (section := pieces(item, window))])


def _meet(left_windows, right_windows):
    found = []
    for a in left_windows:
        for b in right_windows:
            if section := pieces(a, b):
                found.append(list(section))
    return _merge(found)


def _orderings(left_windows, right_windows, left, right):
    """Arrival orders a legal schedule permits, derived from intervals only.

    Only pairs that actually share an instant are considered: once the two
    crossing windows overlap, either ring may arrive first and wait, so both
    orders and the simultaneous case are legal.  The result depends on the
    intervals alone; neither the members' position in the ``rings`` array nor
    their identifiers may decide who crosses first.
    """
    orders = set()
    for a in left_windows:
        for b in right_windows:
            if pieces(a, b):
                orders.update((f'{left}<{right}', f'{right}<{left}', 'simultaneous'))
    return orders


MISSING_REPORTS = ('controller_gap', 'unexpected_successor')


def _blocked(rows, gaps, clocks, controller):
    """Unserved spans caused by missing reports, for blame attribution.

    Only gaps that come from the report stream itself (a sequence gap or a
    non-rotating successor) blame a window on missing reports.  Gaps caused by
    precision or missing clock evidence do not: those windows stay ``unproved``
    rather than ``incomplete``.
    """
    at = {row['id']: clocks.point(controller, row['tick'])[0] for row in rows}
    windows = []
    for gap in gaps:
        if gap['reason'] not in MISSING_REPORTS or len(gap['edges']) < 2:
            continue
        left, right = at.get(gap['edges'][0]), at.get(gap['edges'][1])
        if left is not None and right is not None:
            windows.append([left[0], right[1]])
    return _merge(windows)


def _ring_specs(doc, source):
    rings = doc.get('rings', source.get('rings'))
    if isinstance(rings, dict):
        rings = [dict(value, id=key) if isinstance(value, dict) else {'id': key}
                 for key, value in rings.items()]
    require(isinstance(rings, list) and len(rings) == 2, 'dual-ring bundle needs exactly two rings')
    specs, ids = [], set()
    for row in rings:
        require(isinstance(row, dict), 'ring must be an object')
        rid = row.get('id')
        require(isinstance(rid, str) and rid.strip() and rid not in ids,
                f'invalid or duplicate ring id: {rid!r}')
        ids.add(rid)
        phases = row.get('phase_order', row.get('phases'))
        require(isinstance(phases, list) and phases
                and all(isinstance(p, str) and p for p in phases) and len(set(phases)) == len(phases),
                f'ring {rid}: expected a nonempty phase order')
        controller = row.get('controller')
        if controller is None and isinstance(doc.get('controller'), str):
            controller = doc['controller']
        if controller is None and isinstance(source.get('controller'), str):
            controller = source['controller']
        require(isinstance(controller, str) and controller.strip(), f'ring {rid}: missing controller device')
        specs.append({'id': rid, 'phases': phases, 'controller': controller,
                      'edges': row.get('edges', []), 'crossing': row.get('crossing_phase')})
    union = [p for spec in specs for p in spec['phases']]
    require(len(union) == len(set(union)), 'ring phase orders must be disjoint')
    ordered = doc.get('phase_order', source.get('phase_order'))
    if ordered is None:
        ordered = union
    require(isinstance(ordered, list) and len(ordered) == len(set(ordered)) and set(ordered) == set(union),
            'phase_order must list every ring phase exactly once')
    return specs, ordered


def _crossing(doc, source, specs):
    ids = [spec['id'] for spec in specs]
    raw = doc.get('crossing', source.get('crossing'))
    if raw is None:
        require(all(spec['crossing'] is not None for spec in specs),
                'dual-ring bundle needs a crossing object')
        raw = {'id': 'crossing', 'rings': {spec['id']: spec['crossing'] for spec in specs}}
    if isinstance(raw, list):
        require(len(raw) == len(ids), 'crossing list must name one phase per ring')
        raw = {'rings': dict(zip(ids, raw))}
    require(isinstance(raw, dict), 'crossing must be an object or a per-ring list')
    phases = raw.get('rings', raw.get('phases', raw.get('crossing_phases')))
    if phases is None and all(rid in raw for rid in ids):
        phases = {rid: raw[rid] for rid in ids}
    if isinstance(phases, list):
        require(len(phases) == len(ids), 'crossing list must name one phase per ring')
        phases = dict(zip(ids, phases))
    require(isinstance(phases, dict) and set(phases) == set(ids),
            'crossing must name a phase for each ring')
    owners = {spec['id']: spec['phases'] for spec in specs}
    for rid in ids:
        require(phases[rid] in owners[rid], f'crossing phase not in ring {rid}: {phases[rid]!r}')
    cid = raw.get('id', 'crossing')
    require(isinstance(cid, str) and cid.strip(), 'crossing id must not be blank')
    return {'id': cid, 'phases': {rid: phases[rid] for rid in ids}}


def _edge_logs(doc, source, specs):
    owner, clash = {}, set()
    for spec in specs:
        for phase in spec['phases']:
            if phase in owner:
                clash.add(phase)
            owner[phase] = spec['id']
    for phase in clash:
        owner.pop(phase)
    rows = list(doc.get('edges', []))
    if source is not doc:
        rows.extend(source.get('edges', []))
    for spec in specs:
        rows.extend(spec['edges'])
    require(all(isinstance(row, dict) for row in rows), 'edges must be objects')
    unique(rows)
    logs = {spec['id']: [] for spec in specs}
    for row in rows:
        rid = row.get('ring', owner.get(row.get('phase')))
        require(rid in logs,
                f'edge {row.get("id")!r} must name one of the two rings, or have an unambiguous phase')
        logs[rid].append(row)
    return logs


def _phases(specs, rid):
    return next(spec['phases'] for spec in specs if spec['id'] == rid)


def _controller(specs, rid):
    return next(spec['controller'] for spec in specs if spec['id'] == rid)


def _ring_state(ring, phase, window):
    """One ring's crossing status inside a window, independent of the other ring."""
    picks = [s for s in ring['services'] if s['phase'] == phase and pieces(s['possible'], window)]
    if not picks:
        blamed = any(pieces(block, window) for block in ring['blocked'])
        return [], 'missing_reports' if blamed else 'no_evidence'
    state = 'certain' if any(pieces(s['certain'], window) for s in picks) else 'possible'
    return picks, state


def _crossing_row(bucket, window, per_ring, phase_of, cid):
    left, right = sorted(per_ring)
    picks, states = {}, {}
    for rid in (left, right):
        picks[rid], states[rid] = _ring_state(per_ring[rid], phase_of[rid], window)
    row = {'bucket': bucket, 'window': list(window), 'crossing': cid,
           'rings': {rid: phase_of[rid] for rid in (left, right)},
           'ring_states': states,
           'services': {rid: sorted({s['edges'][0] for s in picks[rid]}) for rid in (left, right)},
           'certain': [], 'possible': [], 'orderings': [], 'missing': [], 'reason': None}
    absent = [rid for rid in (left, right) if not picks[rid]]
    if absent:
        row['missing'] = absent
        row['reason'] = 'missing_reports' if 'missing_reports' in (states[rid] for rid in absent) else 'no_evidence'
        row['state'] = 'incomplete' if row['reason'] == 'missing_reports' else 'unproved'
        return row
    certain = {rid: _clipped([s['certain'] for s in picks[rid]], window) for rid in (left, right)}
    possible = {rid: _clipped([s['possible'] for s in picks[rid]], window) for rid in (left, right)}
    meet_certain = _meet(certain[left], certain[right])
    meet_possible = _meet(possible[left], possible[right])
    if not meet_possible:
        row['state'] = 'conflict'
        row['reason'] = 'no_shared_crossing'
        return row
    row['certain'] = meet_certain
    row['possible'] = meet_possible
    row['orderings'] = sorted(_orderings(possible[left], possible[right], left, right))
    row['state'] = 'certain' if meet_certain else 'possible'
    return row


def materialize_dual(doc, clocks):
    source = doc['controller'] if isinstance(doc.get('controller'), dict) else doc
    specs, ordered = _ring_specs(doc, source)
    crossing = _crossing(doc, source, specs)
    logs = _edge_logs(doc, source, specs)
    ring_of_phase = {phase: spec['id'] for spec in specs for phase in spec['phases']}
    ids = sorted(spec['id'] for spec in specs)

    per_ring, services, gaps, cycles = {}, [], [], []
    for rid in ids:
        ring_services, ring_gaps, ring_cycles = reconstruct(
            _controller(specs, rid), _phases(specs, rid), logs[rid], clocks)
        for row in ring_services:
            row['ring'] = rid
        for row in ring_gaps:
            row['ring'] = rid
        for cycle in ring_cycles:
            cycle['ring'] = rid
        blocked = _blocked(logs[rid], ring_gaps, clocks, _controller(specs, rid))
        per_ring[rid] = {'services': ring_services, 'unusable_edges': ring_gaps,
                         'cycles': ring_cycles, 'blocked': blocked}
        services.extend(ring_services)
        gaps.extend(ring_gaps)
        cycles.extend(ring_cycles)

    coverage, arrivals = collect(doc['wiring'], doc['heartbeats'], doc['arrivals'],
                                 ordered, services, clocks, ring_of_phase)

    for rid in ids:
        own = {phase: rid for phase in _phases(specs, rid)}
        for cycle in per_ring[rid]['cycles']:
            cycle['statistics'] = summarize(cycle['certain'], _phases(specs, rid), coverage, arrivals, own)

    buckets = sorted(unique(doc.get('buckets', [])).items())
    reports = {k: summarize(span(r['time_ms'], 'report bucket'), ordered, coverage, arrivals, ring_of_phase)
               for k, r in buckets}
    crossings = [_crossing_row(k, span(r['time_ms'], 'report bucket'), per_ring, crossing['phases'], crossing['id'])
                 for k, r in buckets]
    members = {rid: {'controller': _controller(specs, rid),
                     'phase_order': _phases(specs, rid),
                     'services': per_ring[rid]['services'],
                     'unusable_edges': per_ring[rid]['unusable_edges'],
                     'cycles': per_ring[rid]['cycles']} for rid in ids}
    return {
        'services': services,
        'unusable_edges': gaps,
        'cycles': cycles,
        'coverage': coverage,
        'arrivals': arrivals,
        'buckets': reports,
        'crossings': crossings,
        'rings': {'crossing': {'id': crossing['id'], 'phases': dict(crossing['phases'])},
                  'members': members},
    }
