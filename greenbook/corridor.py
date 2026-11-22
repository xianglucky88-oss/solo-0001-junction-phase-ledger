"""Corridor pairing: upstream counter batches against evidenced downstream arrivals.

A batch contributes an integer vehicle count measured on one link; controller
edges are never counts.  Feasibility combines upstream clock certificates, the
link travel-time range and downstream arrivals individually supported by clock,
wiring and heartbeat evidence.  Missing evidence is reported, never treated as
zero.  When two batches contend for the same downstream record, every green
bound is computed over complete maximum-volume matchings; conclusions from
different pairings are never spliced together.
"""
from .common import integer, require, scalar, span, unique

# Arrivals in these states carry clock + wiring + heartbeat evidence.
EVIDENCED_STATES = ('green', 'not_green', 'ambiguous_service', 'unproved_service')
NO_EVIDENCE_REASON = {
    'unclocked': 'excluded_arrival_no_clock',
    'unassigned_wiring': 'excluded_arrival_unassigned_wiring',
    'out_of_observation': 'excluded_arrival_no_heartbeat',
}


def travel_span(value, label):
    require(isinstance(value, list) and len(value) == 2, f'invalid {label}: want [min, max]')
    lo, hi = value
    require(type(lo) is int and type(hi) is int and lo >= 0 and hi >= lo,
            f'{label} must be nonnegative integers with min <= max')
    return lo, hi


def cert_pieces(clocks, device, ticks_lo, ticks_hi):
    """Return (possible, certain) millisecond pieces for one raw window."""
    possible, certain = [], []
    for lo, hi, key, offset, error in clocks.by_device.get(device, ()):
        a, b = max(ticks_lo, lo), min(ticks_hi, hi)
        if a < b:
            possible.append((a + offset - error, b + offset + error, key))
            c_lo, c_hi = a + offset + error, b + offset - error
            if c_lo < c_hi:
                certain.append((c_lo, c_hi, key))
    return sorted(possible), sorted(certain)


def envelope(pieces):
    return [scalar(min(p[0] for p in pieces)), scalar(max(p[1] for p in pieces))] if pieces else None


def kuhn(slots, edges_of):
    """Maximum bipartite matching; deterministic on ordered slot/edge ids."""
    matched_edge = {}
    seen = set()

    def augment(slot):
        for edge in edges_of.get(slot, ()):
            if edge in seen:
                continue
            seen.add(edge)
            if edge not in matched_edge or augment(matched_edge[edge]):
                matched_edge[edge] = slot
                return True
        return False

    for slot in slots:
        seen.clear()
        augment(slot)
    return {slot: edge for edge, slot in matched_edge.items()}


def complete_witness(slots, edges_of, fixed):
    """Extend a fixed matching (slot -> edge) to a maximum-volume one.

    Fixed slots never change; the residual matching only fills open slots so
    the witness stays a single coherent pairing rather than splicing results.
    """
    used_edges = set(fixed.values())
    open_slots = [slot for slot in slots if slot not in fixed]
    residual = {slot: [edge for edge in edges_of.get(slot, ()) if edge not in used_edges]
                for slot in open_slots}
    witness = dict(fixed)
    witness.update(kuhn(open_slots, residual))
    return witness


def pair_rows(witness, slot_meta, edge_meta):
    return [{'batch': slot_meta[slot]['batch'], 'slot': slot_meta[slot]['index'],
             'arrival': edge, 'certain_window_match': edge_meta[edge]['certain'],
             'state': edge_meta[edge]['state']}
            for slot, edge in sorted(witness.items(), key=lambda item: item[0])]


def build_corridors(doc, clocks, arrivals):
    has_corridors = 'corridors' in doc
    has_batches = 'counter_batches' in doc
    require(has_corridors == has_batches,
            'corridors and counter_batches must be published together')
    if not has_corridors:
        return []
    corridors = unique(doc['corridors'])
    batches = unique(doc['counter_batches'], field='id')
    facts = unique(arrivals, field='id')

    parsed_links = {}
    windows_by_device = {}
    parsed_batches = {}
    for key, row in corridors.items():
        require(isinstance(row.get('link'), str) and row['link'].strip(), f'invalid link: {key}')
        require(isinstance(row.get('upstream_device'), str) and row['upstream_device'].strip(),
                f'invalid upstream device: {key}')
        require(isinstance(row.get('downstream_detector'), str)
                and row['downstream_detector'].strip(), f'invalid downstream detector: {key}')
        tmin, tmax = travel_span(row.get('travel_time_ms'), f'travel time of {key}')
        parsed_links[key] = {'link': row['link'], 'upstream_device': row['upstream_device'],
                             'downstream_detector': row['downstream_detector'],
                             'tmin': tmin, 'tmax': tmax}
    for key, row in batches.items():
        corridor_id = row.get('corridor')
        require(corridor_id in parsed_links, f'batch {key} references unknown corridor: {corridor_id!r}')
        link = parsed_links[corridor_id]
        device = row.get('device')
        require(isinstance(device, str) and device.strip(), f'invalid batch device: {key}')
        require(device == link['upstream_device'],
                f'batch {key} device {device!r} is not the upstream device of corridor {corridor_id}')
        lo, hi = span(row.get('window_ticks'), f'counter window of {key}')
        vehicles = integer(row.get('vehicles'), f'vehicle count of {key}', 0)
        raw_windows = windows_by_device.setdefault(device, [])
        require((lo, hi) not in raw_windows,
                f'duplicate counter window on {device}: {key}')
        raw_windows.append((lo, hi))
        parsed_batches[key] = {'corridor': corridor_id, 'device': device,
                               'raw_window': (lo, hi), 'vehicles': vehicles}

    output = []
    for corridor_id, link in parsed_links.items():
        rows = sorted((key, batch) for key, batch in parsed_batches.items()
                      if batch['corridor'] == corridor_id)
        detector = link['downstream_detector']
        tmin, tmax = link['tmin'], link['tmax']

        sources = {'clock_certificates': set(), 'wirings': set(),
                   'heartbeats': set(), 'service_edges': set()}
        slot_meta, edges_of, edge_meta = {}, {}, {}
        batch_reports = []
        released = unclocked_total = capacity = 0
        slot_counter = 0
        within_window = set()

        candidates = {key: fact for key, fact in facts.items()
                      if fact.get('detector') == detector}
        excluded = [{'arrival': key, 'reason': NO_EVIDENCE_REASON[fact['state']]}
                    for key, fact in sorted(candidates.items())
                    if fact['state'] in NO_EVIDENCE_REASON]
        for fact in candidates.values():
            if fact['state'] in EVIDENCED_STATES:
                sources['clock_certificates'].add(fact['clock_ref'])
                sources['wirings'].add(fact['wiring'])
                sources['heartbeats'].update(fact['heartbeat_refs'])
                sources['service_edges'].update(fact.get('service_refs', ()))

        for batch_id, batch in rows:
            lo, hi = batch['raw_window']
            possible_pieces, certain_pieces = cert_pieces(clocks, batch['device'], lo, hi)
            sources['clock_certificates'].update(p[2] for p in possible_pieces)
            released += batch['vehicles']
            report = {'batch': batch_id, 'device': batch['device'],
                      'window_ticks': [lo, hi], 'vehicles': batch['vehicles'],
                      'possible_window_ms': envelope(possible_pieces),
                      'certain_window_ms': envelope(certain_pieces)}
            if not possible_pieces:
                report['state'] = 'unclocked'
                report['arrival_windows'] = []
                unclocked_total += batch['vehicles']
                batch_reports.append(report)
                continue
            report['state'] = 'clocked'
            travel_windows = []
            for p_lo, p_hi, cert in possible_pieces:
                travel_windows.append({
                    'clock_ref': cert,
                    'possible_window_ms': [scalar(p_lo + tmin), scalar(p_hi + tmax)],
                    'certain_window_ms': next(([scalar(c_lo + tmin), scalar(c_hi + tmax)]
                                               for c_lo, c_hi, c_cert in certain_pieces
                                               if c_cert == cert), None)})
            report['arrival_windows'] = travel_windows
            possible_windows = [(w['possible_window_ms'][0], w['possible_window_ms'][1])
                                for w in travel_windows]
            certain_windows = [(w['certain_window_ms'][0], w['certain_window_ms'][1])
                               for w in travel_windows if w['certain_window_ms'] is not None]
            own_slots = []
            for _ in range(batch['vehicles']):
                slot = f'slot-{slot_counter}'
                slot_meta[slot] = {'batch': batch_id, 'index': slot_counter}
                own_slots.append(slot)
                slot_counter += 1
                edges_of[slot] = []
                capacity += 1
            for key, fact in candidates.items():
                if fact['state'] not in EVIDENCED_STATES:
                    continue
                at = fact['time_ms']
                if not any(at[0] < w[1] and w[0] <= at[1] for w in possible_windows):
                    continue
                certain = any(w[0] <= at[0] <= at[1] < w[1] for w in certain_windows)
                within_window.add(key)
                if key not in edge_meta:
                    edge_meta[key] = {'state': fact['state'], 'certain': certain,
                                      'time_ms': [scalar(at[0]), scalar(at[1])],
                                      'clock_ref': fact['clock_ref'], 'wiring': fact['wiring'],
                                      'heartbeat_refs': sorted(fact['heartbeat_refs']),
                                      'service_refs': sorted(fact.get('service_refs', ()))}
                else:
                    edge_meta[key]['certain'] = edge_meta[key]['certain'] or certain
                for slot in own_slots:
                    edges_of[slot].append(key)
            batch_reports.append(report)

        no_evidence_ids = {item['arrival'] for item in excluded}
        excluded.extend({'arrival': key, 'reason': 'excluded_arrival_outside_travel_window'}
                        for key in sorted(set(candidates) - no_evidence_ids - within_window))
        slots = sorted(slot_meta, key=lambda s: slot_meta[s]['index'])
        for slot in slots:
            edges_of[slot] = sorted(set(edges_of[slot]))

        # Maximum explainable pairing volume under contention.
        volume = kuhn(slots, edges_of)
        pairable = len(volume)
        certain_green = {edge for edge, meta in edge_meta.items() if meta['state'] == 'green'}
        ambiguous = {edge for edge, meta in edge_meta.items()
                     if meta['state'] == 'ambiguous_service'}
        non_green = set(edge_meta) - certain_green
        # Guaranteed green means certain-green only; ambiguous_service can never
        # be guaranteed and is reported separately.
        green_graph = {slot: [edge for edge in edges_of[slot] if edge in certain_green]
                       for slot in slots}
        non_green_graph = {slot: [edge for edge in edges_of[slot] if edge in non_green]
                           for slot in slots}
        max_green = len(kuhn(slots, green_graph))
        min_green = pairable - len(kuhn(slots, non_green_graph))
        require(0 <= min_green <= max_green <= pairable, 'corridor green bounds inconsistent')
        require(pairable <= capacity and len(edge_meta) >= pairable,
                'corridor matching inconsistent')

        min_witness = complete_witness(slots, edges_of, kuhn(slots, non_green_graph))
        max_witness = complete_witness(slots, edges_of, kuhn(slots, green_graph))
        require(len(min_witness) == pairable and len(max_witness) == pairable,
                'corridor witness must stay maximum volume')
        require(sum(1 for e in min_witness.values() if e in certain_green) == min_green
                and sum(1 for e in max_witness.values() if e in certain_green) == max_green,
                'corridor witness does not realize its bound')

        output.append({
            'corridor': corridor_id,
            'link': link['link'],
            'upstream_device': link['upstream_device'],
            'downstream_detector': detector,
            'travel_time_ms': [scalar(tmin), scalar(tmax)],
            'released_vehicles': released,
            'unclocked_vehicles': unclocked_total,
            'evidenced_capacity': capacity,
            'downstream_candidate_arrivals': sorted(edge_meta),
            'pairable_vehicles': pairable,
            'unevidenced_remainder': capacity - pairable,
            'guaranteed_green_range': {'min': min_green, 'max': max_green},
            'ambiguous_arrivals_unresolved': sorted(ambiguous),
            'sources': {key: sorted(value) for key, value in sorted(sources.items())},
            'batches': batch_reports,
            'witnesses': {
                'volume': pair_rows(volume, slot_meta, edge_meta),
                'minimum_green': pair_rows(min_witness, slot_meta, edge_meta),
                'maximum_green': pair_rows(max_witness, slot_meta, edge_meta)},
            'excluded_downstream_arrivals': excluded})
    return output
