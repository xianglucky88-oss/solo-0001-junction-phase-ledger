from .common import disjoint, pieces, require, span, unique


def inside(point, interval):
    return interval[0] <= point[0] <= point[1] < interval[1]


def touch(point, interval):
    return point[0] < interval[1] and interval[0] <= point[1]


def supporting_heartbeats(at, sections):
    cursor = at[0]
    refs = set()
    for row in sorted(sections, key=lambda c: c['time_ms']):
        lo, hi = row['time_ms']
        if lo > cursor or hi <= cursor:
            continue
        cursor = hi
        refs.add(row['heartbeat'])
        if cursor > at[1]:
            return sorted(refs)
    return []


def collect(wirings, heartbeats, arrivals, phases, services, clocks):
    wire_by_detector, hb_by_detector = {}, {}
    for key, row in unique(wirings).items():
        require(row['phase'] in phases, f'unknown wiring phase: {key}')
        a, b = span(row['time_ms'], 'wiring')
        wire_by_detector.setdefault(row['detector'], []).append((a, b, key, row['phase']))
    for rows in wire_by_detector.values():
        disjoint(rows, 'detector wiring')
    for key, row in unique(heartbeats).items():
        hb_by_detector.setdefault(row['detector'], []).extend((*w, key) for w in clocks.interior(row['detector'], row['ticks']))
    coverage = []
    for detector, wiring in sorted(wire_by_detector.items()):
        for lo, hi, wire, phase in sorted(wiring):
            for a, b, cert, hb in sorted(hb_by_detector.get(detector, [])):
                intersection = pieces((lo, hi), (a, b))
                if intersection:
                    coverage.append({'detector': detector, 'phase': phase, 'time_ms': list(intersection),
                                     'wiring': wire, 'heartbeat': hb, 'clock_ref': cert})
    facts = []
    for key, row in sorted(unique(arrivals).items()):
        detector = row['detector']
        at, clock = clocks.point(detector, row['tick'])
        fact = {'id': key, 'detector': detector, 'time_ms': at, 'clock_ref': clock}
        if at is None:
            facts.append(dict(fact, state='unclocked'))
            continue
        wires = [w for w in wire_by_detector.get(detector, ()) if inside(at, w)]
        if not wires:
            facts.append(dict(fact, state='unassigned_wiring'))
            continue
        phase, wire = wires[0][3], wires[0][2]
        refs = supporting_heartbeats(at, [c for c in coverage if c['wiring'] == wire])
        if not refs:
            facts.append(dict(fact, state='out_of_observation', phase=phase, wiring=wire))
            continue
        compatible = [s for s in services if touch(at, s['possible'])]
        certain = next((s for s in compatible if s['certain'] is not None and inside(at, s['certain'])), None)
        state = ('green' if certain['phase'] == phase else 'not_green') if certain else (
            'ambiguous_service' if compatible else 'unproved_service')
        facts.append(dict(fact, state=state, phase=phase, wiring=wire, heartbeat_refs=refs,
                          service_refs=[s['edges'][0] for s in compatible]))
    return coverage, facts
