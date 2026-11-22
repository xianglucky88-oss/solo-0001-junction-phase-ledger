"""Receipt-facing reissue planning for separately signed deliverables.

A plan compares one published edition with a candidate bundle that reissues a
device clock certificate.  It re-signs a deliverable when, and only when, the
deliverable's own recomputed content changed or evidence the receiver cited for
it changed.  For every re-signed deliverable it names the affected original
time spans and an explainable source chain, and it keeps three kinds of change
apart:

    renumbered_facts_unchanged              certificate identifier changed only
    facts_changed_classification_unchanged  measurements moved, labels held
    readjudication_required                 a prior determination must be remade

Reports are never used as an index.  Both editions are recomputed and every
arrival and every observation section is diffed, so the plan finds:

  * an arrival that had no time evidence before the reissue and first counts
    inside a bucket, even though the old report never listed it;
  * a bucket whose effective observation duration changed while no arrival
    moved, explained through the observation sections that lost or gained time.

Certificates are matched across editions by device and tick interval, so a
renumbered certificate is recognised as the same physical clock.  A receipt
references observations by detector, wiring and heartbeat -- never by
certificate identifier -- so a later renumbering cannot invalidate a receipt
that was already accepted.
"""
from .archive import digest
from .common import encode, merge, require
from .observed import touch


RENUMBERED = 'renumbered_facts_unchanged'
FACTS_CHANGED = 'facts_changed_classification_unchanged'
READJUDICATE = 'readjudication_required'
SEVERITY = {RENUMBERED: 1, FACTS_CHANGED: 2, READJUDICATE: 3}
LISTS = ('certain_green', 'certain_non_green', 'unresolved_or_boundary')
EVIDENCE_FIELDS = ('services', 'unusable_edges', 'coverage', 'arrivals')


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _sha(value):
    return digest(encode(value))


def _span(value):
    return None if value is None else [value[0], value[1]]


def _clip(value, window):
    if value is None:
        return None
    if window is None:
        return [value[0], value[1]]
    if value[0] == value[1]:
        return [value[0], value[1]] if window[0] <= value[0] <= window[1] else None
    lo, hi = max(value[0], window[0]), min(value[1], window[1])
    return [lo, hi] if lo < hi else None


def _bounds(spans):
    spans = [span for span in spans if span is not None]
    if not spans:
        return None
    return [min(s[0] for s in spans), max(s[1] for s in spans)]


def _category(changes):
    if not changes:
        return None
    return max((c['category'] for c in changes), key=SEVERITY.get)


def _unique(rows):
    seen, output = set(), []
    for row in rows:
        marker = encode(row)
        if marker not in seen:
            seen.add(marker)
            output.append(row)
    return output


def _source(kind, key, note):
    return {'type': kind, 'id': key, 'note': note}


def _clock_sources(clock_ref, reissues, note):
    if clock_ref is None:
        return []
    for row in reissues:
        if clock_ref in (row['base_id'], row['candidate_id']):
            return [_source('clock_certificate', clock_ref, note),
                    {'type': 'certificate_reissue', 'id': f"{row['base_id']}->{row['candidate_id']}",
                     'device': row['device'], 'base_id': row['base_id'],
                     'candidate_id': row['candidate_id'], 'note': f'certificate {row["change"]}'}]
    return [_source('clock_certificate', clock_ref, note)]


# ---------------------------------------------------------------------------
# certificate reissue: match the same physical clock across editions
# ---------------------------------------------------------------------------

def certificate_reissues(base_doc, candidate_doc):
    old = {row['id']: row for row in base_doc['clock_certificates']}
    new = {row['id']: row for row in candidate_doc['clock_certificates']}
    pairs, used_old, used_new = [], set(), set()
    for key in sorted(set(old) & set(new)):
        if old[key] != new[key]:
            pairs.append({'device': old[key]['device'], 'base_id': key, 'candidate_id': key,
                          'change': 'revised',
                          'fields': sorted(f for f in set(old[key]) | set(new[key])
                                           if old[key].get(f) != new[key].get(f))})
            used_old.add(key)
            used_new.add(key)
    for key in sorted(set(old) & set(new)):
        if old[key] == new[key]:
            used_old.add(key)
            used_new.add(key)
    devices = {row['device'] for row in old.values()} | {row['device'] for row in new.values()}
    for device in sorted(devices):
        retired = sorted((row for row in old.values()
                          if row['device'] == device and row['id'] not in used_old),
                         key=lambda r: (r['ticks'], r['id']))
        issued = sorted((row for row in new.values()
                         if row['device'] == device and row['id'] not in used_new),
                        key=lambda r: (r['ticks'], r['id']))
        while retired and issued:
            left, right = retired.pop(0), issued.pop(0)
            overlap = min(left['ticks'][1], right['ticks'][1]) - max(left['ticks'][0], right['ticks'][0])
            pairs.append({'device': device, 'base_id': left['id'], 'candidate_id': right['id'],
                          'change': 'renumbered' if overlap > 0 else 'replaced',
                          'fields': sorted(f for f in set(left) | set(right)
                                           if f != 'id' and left.get(f) != right.get(f))})
        for row in retired:
            pairs.append({'device': device, 'base_id': row['id'], 'candidate_id': None,
                          'change': 'withdrawn', 'fields': []})
        for row in issued:
            pairs.append({'device': device, 'base_id': None, 'candidate_id': row['id'],
                          'change': 'added', 'fields': []})
    pairs.sort(key=lambda row: (row['device'], row['base_id'] or '', row['candidate_id'] or ''))
    return pairs


# ---------------------------------------------------------------------------
# indexes over recomputed results
# ---------------------------------------------------------------------------

def _arrivals(result):
    return {row['id']: row for row in result['arrivals']}


def _coverage(result):
    return {(row['detector'], row['wiring'], row['heartbeat']): row for row in result['coverage']}


def _services(result):
    return {tuple(row['edges']): row for row in result['services'] if len(row['edges']) == 2}


def _gaps(result):
    return {tuple(row['edges']): row for row in result['unusable_edges']}


def _cycles(result):
    return {row['start_edge']: row for row in result['cycles']}


def _phases(report):
    return {row['phase']: row for row in report}


def _membership(report):
    table = {}
    for row in report:
        for field in LISTS:
            for aid in row[field]:
                table[aid] = (row['phase'], field)
    return table


def _relevant(row, window):
    if not row or row.get('phase') is None or row.get('time_ms') is None:
        return False
    return window is None or touch(row['time_ms'], window)


# ---------------------------------------------------------------------------
# change records
# ---------------------------------------------------------------------------

def _arrival_change(aid, old, new, reason, category, reissues, note,
                    old_window=None, new_window=None):
    sources = [_source('arrival', aid, note)]
    for row in (old, new):
        if not row:
            continue
        sources += _clock_sources(row.get('clock_ref'), reissues, note)
        for heartbeat in row.get('heartbeat_refs', []):
            sources.append(_source('heartbeat', heartbeat, note))
        if row.get('wiring'):
            sources.append(_source('wiring', row['wiring'], note))
    old_span = _span(old.get('time_ms')) if old else None
    new_span = _span(new.get('time_ms')) if new else None
    return {'ref': {'type': 'arrival', 'key': aid}, 'reason': reason, 'category': category,
            'old': old, 'new': new,
            'base_time_ms': _clip(old_span, old_window),
            'candidate_time_ms': _clip(new_span, new_window),
            'sources': _unique(sources)}


def _coverage_change(key, old, new, reason, category, reissues, note,
                     old_window=None, new_window=None):
    detector, wiring, heartbeat = key
    sources = [_source('observation', f'{detector}/{wiring}/{heartbeat}', note),
               _source('wiring', wiring, note),
               _source('heartbeat', heartbeat, note)]
    for row in (old, new):
        if row:
            sources += _clock_sources(row.get('clock_ref'), reissues, note)
    return {'ref': {'type': 'observation', 'key': f'{detector}/{wiring}/{heartbeat}'},
            'old_ref': {'detector': detector, 'wiring': wiring, 'heartbeat': heartbeat},
            'reason': reason, 'category': category, 'old': old, 'new': new,
            'base_time_ms': _clip(_span(old.get('time_ms')) if old else None, old_window),
            'candidate_time_ms': _clip(_span(new.get('time_ms')) if new else None, new_window),
            'sources': _unique(sources)}


def _coverage_changes(base_result, cand_result, old_window, new_window, reissues, note):
    base_cover, cand_cover = _coverage(base_result), _coverage(cand_result)
    changes = []
    for key in sorted(set(base_cover) | set(cand_cover)):
        old, new = base_cover.get(key), cand_cover.get(key)
        old_part = _clip(_span(old['time_ms']), old_window) if old else None
        new_part = _clip(_span(new['time_ms']), new_window) if new else None
        if old_part is None and new_part is None:
            continue
        if old is None or new is None:
            changes.append(_coverage_change(
                key, old, new,
                'observation_section_appeared' if old is None else 'observation_section_withdrawn',
                READJUDICATE, reissues, note, old_window, new_window))
        elif old['phase'] != new['phase']:
            changes.append(_coverage_change(key, old, new, 'observation_phase_reassigned',
                                            READJUDICATE, reissues, note, old_window, new_window))
        elif old_part != new_part:
            changes.append(_coverage_change(key, old, new, 'observation_interval_changed',
                                            FACTS_CHANGED, reissues, note, old_window, new_window))
        elif old['clock_ref'] != new['clock_ref']:
            changes.append(_coverage_change(key, old, new, 'certificate_renumbered',
                                            RENUMBERED, reissues, note, old_window, new_window))
        elif old != new:
            changes.append(_coverage_change(key, old, new, 'observation_evidence_changed',
                                            FACTS_CHANGED, reissues, note, old_window, new_window))
    return changes


def _arrival_changes(base_result, cand_result, old_report, new_report,
                     old_window, new_window, reissues, note):
    base_arr, cand_arr = _arrivals(base_result), _arrivals(cand_result)
    old_members, new_members = _membership(old_report), _membership(new_report)
    changes = []
    for aid in sorted(set(base_arr) | set(cand_arr)):
        old, new = base_arr.get(aid), cand_arr.get(aid)
        before, after = old_members.get(aid), new_members.get(aid)
        old_seen, new_seen = _relevant(old, old_window), _relevant(new, new_window)
        if old == new and before == after:
            continue
        if not (old_seen or new_seen or before or after):
            continue
        if before != after:
            if before is None:
                reason = 'arrival_entered_bucket'
                detail = ('had no time evidence before the reissue and first counts here'
                          if old and old.get('time_ms') is None
                          else 'was not counted in this window before')
            elif after is None:
                reason, detail = 'arrival_left_bucket', 'no longer counts inside this window'
            else:
                reason, detail = 'arrival_classification_changed', 'moved between report lists'
            changes.append(_arrival_change(aid, old, new, reason, READJUDICATE, reissues,
                                           detail, old_window, new_window))
        elif old_seen or new_seen:
            old_state, new_state = (old or {}).get('state'), (new or {}).get('state')
            old_phase, new_phase = (old or {}).get('phase'), (new or {}).get('phase')
            if old_state != new_state or old_phase != new_phase:
                changes.append(_arrival_change(aid, old, new, 'arrival_state_changed',
                                               READJUDICATE, reissues,
                                               'state or phase changed', old_window, new_window))
            elif (old or {}).get('time_ms') != (new or {}).get('time_ms'):
                changes.append(_arrival_change(aid, old, new, 'arrival_interval_changed',
                                               FACTS_CHANGED, reissues,
                                               'arrival interval moved under the same label',
                                               old_window, new_window))
            elif (old or {}).get('clock_ref') != (new or {}).get('clock_ref'):
                changes.append(_arrival_change(aid, old, new, 'certificate_renumbered',
                                               RENUMBERED, reissues,
                                               'same arrival, new certificate identifier',
                                               old_window, new_window))
            elif old != new:
                changes.append(_arrival_change(aid, old, new, 'arrival_evidence_changed',
                                               FACTS_CHANGED, reissues,
                                               'cited arrival evidence changed',
                                               old_window, new_window))
    return changes


def _duration_changes(old_report, new_report, phases, old_window, new_window,
                      coverage_changes, note):
    old_rows, new_rows = _phases(old_report), _phases(new_report)
    changes = []
    for phase in phases:
        old_row, new_row = old_rows.get(phase), new_rows.get(phase)
        if old_row is None or new_row is None:
            continue
        detectors = sorted(set(old_row['observation_ms_by_detector'])
                           | set(new_row['observation_ms_by_detector']))
        for detector in detectors:
            old_ms = old_row['observation_ms_by_detector'].get(detector)
            new_ms = new_row['observation_ms_by_detector'].get(detector)
            if old_ms == new_ms:
                continue
            spans = [c for c in coverage_changes
                     if c['ref']['key'].startswith(detector + '/')
                     and phase in {(c['old'] or {}).get('phase'), (c['new'] or {}).get('phase')}]
            changes.append({'ref': {'type': 'observation_total', 'key': f'{phase}/{detector}'},
                            'old_ref': {'phase': phase, 'detector': detector},
                            'reason': 'observation_duration_changed',
                            'category': FACTS_CHANGED, 'old': old_ms, 'new': new_ms,
                            'base_time_ms': _clip(_bounds(c['base_time_ms'] for c in spans),
                                                  old_window),
                            'candidate_time_ms': _clip(_bounds(c['candidate_time_ms'] for c in spans),
                                                       new_window),
                            'sources': _unique([s for c in spans for s in c['sources']])})
    return changes


def _report_changes(base_result, cand_result, old_report, new_report,
                    old_window, new_window, phases, reissues, note):
    coverage = _coverage_changes(base_result, cand_result, old_window, new_window, reissues, note)
    arrivals = _arrival_changes(base_result, cand_result, old_report, new_report,
                                old_window, new_window, reissues, note)
    durations = _duration_changes(old_report, new_report, phases, old_window, new_window,
                                  coverage, note)
    return coverage + arrivals + durations


# ---------------------------------------------------------------------------
# evidence package
# ---------------------------------------------------------------------------

def _evidence_changes(base_result, cand_result, reissues):
    changes = []
    base_services, cand_services = _services(base_result), _services(cand_result)
    for edges in sorted(set(base_services) | set(cand_services)):
        old, new = base_services.get(edges), cand_services.get(edges)
        key = '/'.join(edges)
        if old is None or new is None:
            changes.append({'ref': {'type': 'service', 'key': key, 'edges': list(edges)},
                            'reason': 'service_appeared' if old is None else 'service_withdrawn',
                            'category': READJUDICATE, 'old': old, 'new': new,
                            'base_time_ms': _span(old['certain']) if old else None,
                            'candidate_time_ms': _span(new['certain']) if new else None,
                            'sources': [_source('edge', edge, 'service membership changed')
                                        for edge in edges]})
            continue
        if old['phase'] != new['phase']:
            reason, category = 'service_phase_changed', READJUDICATE
        elif old['certain'] != new['certain'] or old['possible'] != new['possible']:
            reason, category = 'service_interval_changed', FACTS_CHANGED
        elif old['clock_refs'] != new['clock_refs']:
            reason, category = 'certificate_renumbered', RENUMBERED
        elif old != new:
            reason, category = 'service_evidence_changed', FACTS_CHANGED
        else:
            continue
        sources = [_source('edge', edge, 'edge clock evidence') for edge in edges]
        for clock in old['clock_refs']:
            sources += _clock_sources(clock, reissues, 'service clock evidence')
        changes.append({'ref': {'type': 'service', 'key': key, 'edges': list(edges)},
                        'reason': reason, 'category': category, 'old': old, 'new': new,
                        'base_time_ms': _span(old['certain']),
                        'candidate_time_ms': _span(new['certain']),
                        'sources': _unique(sources)})
    base_gaps, cand_gaps = _gaps(base_result), _gaps(cand_result)
    for edges in sorted(set(base_gaps) | set(cand_gaps)):
        old, new = base_gaps.get(edges), cand_gaps.get(edges)
        if old == new:
            continue
        changes.append({'ref': {'type': 'unusable_edge', 'key': '/'.join(edges), 'edges': list(edges)},
                        'reason': 'unusable_edge_changed', 'category': READJUDICATE,
                        'old': old, 'new': new, 'base_time_ms': None, 'candidate_time_ms': None,
                        'sources': [_source('edge', edge, 'edge usability changed')
                                    for edge in edges]})
    changes += _coverage_changes(base_result, cand_result, None, None, reissues,
                                 'evidence package observation')
    changes += _arrival_changes(base_result, cand_result, [], [], None, None, reissues,
                                'evidence package arrival')
    return changes


# ---------------------------------------------------------------------------
# cycles
# ---------------------------------------------------------------------------

def _cycle_changes(base_result, cand_result, start_edge, reissues):
    old, new = _cycles(base_result).get(start_edge), _cycles(cand_result).get(start_edge)
    if old is None or new is None:
        return [{'ref': {'type': 'cycle', 'key': start_edge,
                         'edges': list((old or new)['edges'])},
                 'reason': 'cycle_appeared' if old is None else 'cycle_withdrawn',
                 'category': READJUDICATE, 'old': old, 'new': new,
                 'base_time_ms': _span(old['certain']) if old else None,
                 'candidate_time_ms': _span(new['certain']) if new else None,
                 'sources': [_source('edge', start_edge, 'cycle membership changed')]}]
    changes = []
    if old['edges'] != new['edges']:
        changes.append({'ref': {'type': 'cycle', 'key': start_edge, 'edges': list(old['edges'])},
                        'reason': 'cycle_membership_changed', 'category': READJUDICATE,
                        'old': {'certain': old['certain'], 'edges': old['edges']},
                        'new': {'certain': new['certain'], 'edges': new['edges']},
                        'base_time_ms': _span(old['certain']),
                        'candidate_time_ms': _span(new['certain']),
                        'sources': [_source('edge', edge, 'cycle boundary evidence')
                                    for edge in old['edges']]})
    elif old['certain'] != new['certain']:
        changes.append({'ref': {'type': 'cycle', 'key': start_edge, 'edges': list(old['edges'])},
                        'reason': 'cycle_boundary_changed', 'category': FACTS_CHANGED,
                        'old': {'certain': old['certain'], 'edges': old['edges']},
                        'new': {'certain': new['certain'], 'edges': new['edges']},
                        'base_time_ms': _span(old['certain']),
                        'candidate_time_ms': _span(new['certain']),
                        'sources': [_source('edge', edge, 'cycle boundary evidence')
                                    for edge in old['edges']]})
    changes += _report_changes(base_result, cand_result, old['statistics'], new['statistics'],
                               old['certain'], new['certain'],
                               list(_phases(old['statistics'])), reissues, start_edge)
    return changes


# ---------------------------------------------------------------------------
# citation: did a change touch evidence the receiver cited for this deliverable?
# ---------------------------------------------------------------------------

def _cited(change, cited_services, cited_observations, cited_arrivals):
    ref = change['ref']
    kind = ref['type']
    if kind in ('service', 'unusable_edge', 'cycle'):
        edges = set(ref.get('edges', ()))
        return bool(edges & cited_services)
    if kind == 'observation':
        old_ref = change['old_ref']
        return (old_ref['detector'], old_ref['wiring'], old_ref['heartbeat']) in cited_observations
    if kind == 'observation_total':
        detector = change['old_ref']['detector']
        return any(observation[0] == detector for observation in cited_observations)
    if kind == 'arrival':
        return ref['key'] in cited_arrivals
    return False


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------

def _evaluate(key, kind, content, candidate_content, window, candidate_window,
              changes, receipt_row, suffix):
    received = receipt_row is not None
    cited_services = set(receipt_row['services']) if received else set()
    cited_observations = ({(o['detector'], o['wiring'], o['heartbeat'])
                           for o in receipt_row['observations']} if received else set())
    cited_arrivals = set(receipt_row['arrivals']) if received else set()
    cited_changes = [c for c in changes
                     if c['category'] != RENUMBERED
                     and _cited(c, cited_services, cited_observations, cited_arrivals)]
    content_changed = _sha(content) != _sha(candidate_content)
    if content_changed:
        resign = True
        grounds = changes or [{
            'ref': {'type': kind, 'key': key},
            'reason': 'recomputed_content_changed', 'category': FACTS_CHANGED,
            'old': content, 'new': candidate_content,
            'base_time_ms': _span(window), 'candidate_time_ms': _span(candidate_window),
            'sources': [_source(kind, key, 'recomputed content differs')]}]
    else:
        resign = bool(cited_changes)
        grounds = cited_changes
    if resign and received:
        action = 'resign'
    elif resign:
        action = 'resign_if_received'
    elif received:
        action = 'keep'
    else:
        action = 'not_signed'
    base_spans = merge(c['base_time_ms'] for c in grounds if c.get('base_time_ms'))
    cand_spans = merge(c['candidate_time_ms'] for c in grounds if c.get('candidate_time_ms'))
    if window is not None:
        base_spans = merge(part for part in (_clip(s, window) for s in base_spans) if part)
    if candidate_window is not None:
        cand_spans = merge(part for part in (_clip(s, candidate_window) for s in cand_spans) if part)
    return {'key': key, 'type': kind, 'action': action,
            'received': received, 'resign': resign, 'content_changed': content_changed,
            'category': _category(grounds),
            'base_key': key,
            'new_key': f'{key}@{suffix}' if resign else None,
            'content_sha256': _sha(content),
            'candidate_content_sha256': _sha(candidate_content),
            'affected_time_ms': {'base': base_spans, 'candidate': cand_spans},
            'sources': _unique([s for c in grounds for s in c['sources']]),
            'changes': grounds}


def bucket_window(doc, bucket):
    match = [row for row in doc['buckets'] if row['id'] == bucket]
    require(len(match) == 1, f'unknown bucket: {bucket}')
    return match[0]['time_ms']


def plan(base_doc, base_result, candidate_doc, candidate_result, receipt, base_edition,
         candidate_edition=None):
    reissues = certificate_reissues(base_doc, candidate_doc)
    require(any(row['change'] in ('renumbered', 'revised', 'added', 'withdrawn')
                for row in reissues),
            'candidate does not reissue a device clock certificate')
    suffix = candidate_edition or _sha(candidate_doc)[:12]
    by_key = {row['key']: row for row in receipt['deliverables']}
    phases = base_doc['phase_order']

    rows = [_evaluate(
        'evidence', 'evidence',
        {field: base_result[field] for field in EVIDENCE_FIELDS},
        {field: candidate_result[field] for field in EVIDENCE_FIELDS},
        None, None, _evidence_changes(base_result, candidate_result, reissues),
        by_key.get('evidence'), suffix)]
    base_cycles, cand_cycles = _cycles(base_result), _cycles(candidate_result)
    for start_edge in sorted(set(base_cycles) | set(cand_cycles)):
        key = 'cycle:' + start_edge
        old, new = base_cycles.get(start_edge), cand_cycles.get(start_edge)
        rows.append(_evaluate(
            key, 'cycle', old, new,
            old['certain'] if old else None, new['certain'] if new else None,
            _cycle_changes(base_result, candidate_result, start_edge, reissues),
            by_key.get(key), suffix))
    for bucket in sorted(set(base_result['buckets']) | set(candidate_result['buckets'])):
        key = 'bucket:' + bucket
        window = bucket_window(base_doc, bucket)
        candidate_window = (bucket_window(candidate_doc, bucket)
                            if any(row['id'] == bucket for row in candidate_doc['buckets'])
                            else window)
        rows.append(_evaluate(
            key, 'bucket_report',
            base_result['buckets'].get(bucket), candidate_result['buckets'].get(bucket),
            window, candidate_window,
            _report_changes(base_result, candidate_result,
                            base_result['buckets'].get(bucket, []),
                            candidate_result['buckets'].get(bucket, []),
                            window, candidate_window, phases, reissues, bucket),
            by_key.get(key), suffix))
    rows.sort(key=lambda row: (row['type'], row['key']))
    for row in rows:
        require(not row['resign'] or row['changes'],
                f'deliverable resigns with no explainable change: {row["key"]}')
        require(row['resign'] or row['action'] in ('keep', 'not_signed'),
                f'deliverable action contradicts its verdict: {row["key"]}')
        require(row['new_key'] is None or row['new_key'] != row['base_key'],
                f'new deliverable key must differ from the accepted one: {row["key"]}')
    summary = {'deliverables': len(rows),
               'received': sum(1 for row in rows if row['received']),
               'resign': sum(1 for row in rows if row['resign']),
               'resign_received': sum(1 for row in rows if row['resign'] and row['received']),
               'unchanged': sum(1 for row in rows if not row['resign']),
               'content_changed': sum(1 for row in rows if row['content_changed'])}
    for category in SEVERITY:
        summary[category] = sum(1 for row in rows
                                if row['resign'] and row['category'] == category)
    return {'base_edition': base_edition,
            'candidate_edition': candidate_edition,
            'base_source_sha256': _sha(base_doc),
            'base_result_sha256': _sha(base_result),
            'candidate_source_sha256': _sha(candidate_doc),
            'candidate_result_sha256': _sha(candidate_result),
            'receipt_sha256': _sha(receipt),
            'certificate_reissues': reissues,
            'summary': summary,
            'deliverables': rows}
