"""Validate a receiver acknowledgement against a published edition.

A receipt names the deliverables the receiver accepted and the service,
observation and arrival references it recorded.  References are checked
against the base edition only; certificate identifiers are deliberately not
part of an observation reference, so a later certificate renumbering cannot
invalidate an already accepted receipt.
"""
from .common import require

PREFIX = {'evidence': 'evidence', 'cycle': 'cycle:', 'bucket_report': 'bucket:'}
FIELDS = {'type', 'key', 'services', 'observations', 'arrivals'}


def known(result):
    services = {edge for row in result['services'] for edge in row['edges']}
    services |= {edge for row in result['unusable_edges'] for edge in row['edges']}
    observations = {(c['detector'], c['wiring'], c['heartbeat']) for c in result['coverage']}
    arrivals = {row['id'] for row in result['arrivals']}
    return services, observations, arrivals


def deliverable_keys(result):
    keys = {'evidence'}
    keys |= {'cycle:' + row['start_edge'] for row in result['cycles']}
    keys |= {'bucket:' + key for key in result['buckets']}
    return keys


def _observation(value, label):
    if isinstance(value, dict):
        require(set(value) == {'detector', 'wiring', 'heartbeat'}, f'invalid observation reference: {label}')
        return value['detector'], value['wiring'], value['heartbeat']
    require(isinstance(value, list) and len(value) == 3, f'invalid observation reference: {label}')
    return tuple(value)


def canonical(doc, result, edition=None):
    require(isinstance(doc, dict), 'receipt must be an object')
    require(set(doc) <= {'edition', 'deliverables'}, 'receipt has unknown members')
    if edition is not None:
        require(doc.get('edition') in (None, edition), f'receipt edition does not match: {doc.get("edition")!r}')
    rows = doc.get('deliverables')
    require(isinstance(rows, list), 'receipt deliverables must be a list')
    services, observations, arrivals = known(result)
    keys = deliverable_keys(result)
    output, seen = [], set()
    for row in rows:
        require(isinstance(row, dict) and set(row) <= FIELDS, 'invalid receipt deliverable')
        kind, key = row.get('type'), row.get('key')
        require(kind in PREFIX, f'unknown deliverable type: {kind!r}')
        require(isinstance(key, str) and key in keys, f'unknown deliverable: {key!r}')
        require(key.startswith(PREFIX[kind]), f'deliverable type does not match key: {key!r}')
        require(key not in seen, f'duplicate receipt deliverable: {key}')
        seen.add(key)
        refs = row.get('services', [])
        finds = row.get('observations', [])
        hits = row.get('arrivals', [])
        require(isinstance(refs, list) and isinstance(finds, list) and isinstance(hits, list),
                f'receipt references must be lists: {key}')
        for edge in refs:
            require(isinstance(edge, str) and edge in services, f'unknown service reference: {edge!r}')
        for value in finds:
            require(_observation(value, key) in observations, f'unknown observation reference: {value!r}')
        for arrival in hits:
            require(isinstance(arrival, str) and arrival in arrivals, f'unknown arrival reference: {arrival!r}')
        output.append({'type': kind, 'key': key,
                       'services': sorted(set(refs)),
                       'observations': [{'detector': d, 'wiring': w, 'heartbeat': h}
                                        for d, w, h in sorted({_observation(x, key) for x in finds})],
                       'arrivals': sorted(set(hits))})
    output.sort(key=lambda item: item['key'])
    return {'edition': doc.get('edition'), 'deliverables': output}
