"""Acceptance tests for the corridor pairing extension.

Run from the repository root with:  python3 -I tests/test_corridor.py
No third-party dependencies; uses only the greenbook package and temp files.
"""
import copy
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from greenbook.archive import load, publish  # noqa: E402
from greenbook.common import decode, encode  # noqa: E402
from greenbook.workflow import materialize  # noqa: E402

FIX = ROOT / 'tests' / 'fixtures'


def load_fixture(name):
    return decode((FIX / name).read_text(encoding='utf-8'))


def expect_error(doc, fragment):
    try:
        materialize(doc)
    except ValueError as exc:
        assert fragment in str(exc), f'{exc!r} lacks {fragment!r}'
        return
    raise AssertionError(f'expected ValueError containing {fragment!r}')


def corridor_of(doc):
    result = materialize(doc)
    assert len(result['corridors']) == 1
    return result, result['corridors'][0]


def test_station_green_rate_identical():
    a = materialize(load_fixture('corridor_contention.json'))
    b = materialize(load_fixture('corridor_disjoint.json'))
    rates = {name: out['buckets']['full'][0]['green_share_of_classified']
             for name, out in (('A', a), ('B', b))}
    assert rates == {'A': '1/2', 'B': '1/2'}, rates
    # Ambiguous arrivals never enter the classified denominator.
    for out in (a, b):
        phase_n = out['buckets']['full'][0]
        assert phase_n['certain_green'] == ['a-green']
        assert phase_n['certain_non_green'] == ['a-red']


def test_contention_range_is_zero_to_one():
    _, c = corridor_of(load_fixture('corridor_contention.json'))
    assert c['released_vehicles'] == 4
    assert c['unclocked_vehicles'] == 1           # missing evidence, not zero
    assert c['evidenced_capacity'] == 3
    assert c['pairable_vehicles'] == 3
    assert c['unevidenced_remainder'] == 0
    assert c['guaranteed_green_range'] == {'min': 0, 'max': 1}
    # Each witness is one complete maximum-volume matching on its own; bounds
    # are never produced by deleting or adding rows of another pairing.
    for key in ('volume', 'minimum_green', 'maximum_green'):
        rows = c['witnesses'][key]
        assert len(rows) == 3
        arrivals = [row['arrival'] for row in rows]
        slots = [row['slot'] for row in rows]
        assert len(set(arrivals)) == 3 and len(set(slots)) == 3
    min_rows = c['witnesses']['minimum_green']
    max_rows = c['witnesses']['maximum_green']
    assert not any(row['state'] == 'green' for row in min_rows)
    assert sum(row['state'] == 'green' for row in max_rows) == 1
    # Both pairings remain within each batch's own feasible windows.
    feasible = {b['batch']: [tuple(w['possible_window_ms']) for w in b['arrival_windows']]
                for b in c['batches'] if b['state'] == 'clocked'}
    facts = {a['id']: a for a in materialize(load_fixture('corridor_contention.json'))['arrivals']}
    for rows in (min_rows, max_rows):
        for row in rows:
            at = facts[row['arrival']]['time_ms']
            assert any(at[0] < w[1] and w[0] <= at[1] for w in feasible[row['batch']])
    # Ambiguous records are reported unresolved, never counted as guaranteed.
    assert set(c['ambiguous_arrivals_unresolved']) == {'a-edge', 'a-maybe'}


def test_disjoint_range_collapses_to_one():
    _, c = corridor_of(load_fixture('corridor_disjoint.json'))
    assert c['released_vehicles'] == 4
    assert c['unclocked_vehicles'] == 1
    assert c['pairable_vehicles'] == 3
    assert c['guaranteed_green_range'] == {'min': 1, 'max': 1}
    for key in ('volume', 'minimum_green', 'maximum_green'):
        green = [row for row in c['witnesses'][key] if row['state'] == 'green']
        assert [row['arrival'] for row in green] == ['a-green']


def test_unevidenced_and_unclocked_are_not_zero_or_red():
    doc = load_fixture('corridor_disjoint.json')
    # Shrink the clocked batches so evidenced capacity exceeds pairings.
    for row in doc['counter_batches']:
        if row['id'] == 'batch-b2':
            row['vehicles'] = 1
    result, c = corridor_of(doc)
    assert c['pairable_vehicles'] == 2
    assert c['unevidenced_remainder'] == 0       # capacity itself shrank
    # Now move the second window beyond every evidenced arrival while staying
    # inside the upstream clock certificate: travel window 208..272 contains
    # no supported loop arrival, so the slot remains an explained unknown.
    for row in doc['counter_batches']:
        if row['id'] == 'batch-b2':
            row['window_ticks'] = [170, 190]
    result, c = corridor_of(doc)
    assert c['evidenced_capacity'] == 2
    assert c['pairable_vehicles'] == 1
    assert c['unevidenced_remainder'] == 1
    assert c['unclocked_vehicles'] == 1
    assert c['guaranteed_green_range'] == {'min': 1, 'max': 1}
    # The unmatched capacity stays an unknown; no fabricated red pairing.
    assert all(row['arrival'] is not None for row in c['witnesses']['volume'])
    assert [row['arrival'] for row in c['witnesses']['volume']] == ['a-green']


def test_unproved_and_unobserved_arrivals_cannot_fill_capacity():
    doc = load_fixture('corridor_disjoint.json')
    # Point the corridor at side, whose arrivals lack heartbeat or service proof.
    doc['corridors'][0]['downstream_detector'] = 'side'
    _, c = corridor_of(doc)
    assert c['pairable_vehicles'] == 0
    assert c['unevidenced_remainder'] == c['evidenced_capacity'] == 3
    assert c['downstream_candidate_arrivals'] == []
    reasons = {item['arrival']: item['reason'] for item in c['excluded_downstream_arrivals']}
    assert reasons == {'a-gap': 'excluded_arrival_outside_travel_window',
                       'a-offline': 'excluded_arrival_no_heartbeat'}
    # a-gap has clock+wiring+heartbeat but no service proof: still ineligible to
    # fill capacity, and never silently turned into a green or a zero.
    gap_fact = next(a for a in materialize(doc)['arrivals'] if a['id'] == 'a-gap')
    assert gap_fact['state'] == 'unproved_service'


def test_exclusion_bookkeeping_is_complete():
    _, c = corridor_of(load_fixture('corridor_disjoint.json'))
    reasons = {item['arrival']: item['reason'] for item in c['excluded_downstream_arrivals']}
    assert reasons == {
        'a-rewire': 'excluded_arrival_unassigned_wiring',
        'a-unknown-clock': 'excluded_arrival_no_clock',
        'a-new': 'excluded_arrival_outside_travel_window'}
    candidates = set(c['downstream_candidate_arrivals'])
    excluded = set(reasons)
    loop_arrivals = {a['id'] for a in materialize(load_fixture('corridor_disjoint.json'))['arrivals']
                     if a['detector'] == 'loop'}
    assert candidates | excluded == loop_arrivals and not (candidates & excluded)


def test_edges_are_never_treated_as_counts():
    doc = load_fixture('corridor_disjoint.json')
    doc['counter_batches'] = []
    # Corridors still published: zero counter capacity regardless of 6 edges.
    _, c = corridor_of(doc)
    assert c['released_vehicles'] == 0 and c['pairable_vehicles'] == 0
    assert c['evidenced_capacity'] == 0
    assert c['guaranteed_green_range'] == {'min': 0, 'max': 0}


def test_backward_compatible_output_without_corridors():
    base = decode((ROOT / 'examples' / 'baseline.json').read_text(encoding='utf-8'))
    assert 'corridors' not in materialize(base)
    changed = decode((ROOT / 'examples' / 'changed.json').read_text(encoding='utf-8'))
    assert 'corridors' not in materialize(changed)


def test_invalid_bundles_rejected():
    base = load_fixture('corridor_disjoint.json')

    only_one = copy.deepcopy(base)
    del only_one['counter_batches']
    expect_error(only_one, 'published together')

    unknown = copy.deepcopy(base)
    unknown['counter_batches'][0]['corridor'] = 'nope'
    expect_error(unknown, 'unknown corridor')

    wrong_device = copy.deepcopy(base)
    wrong_device['counter_batches'][0]['device'] = 'loop'
    expect_error(wrong_device, 'not the upstream device')

    bad_travel = copy.deepcopy(base)
    bad_travel['corridors'][0]['travel_time_ms'] = [80, 40]
    expect_error(bad_travel, 'min <= max')

    negative_count = copy.deepcopy(base)
    negative_count['counter_batches'][0]['vehicles'] = -1
    expect_error(negative_count, 'vehicle count')

    edge_as_count = copy.deepcopy(base)
    edge_as_count['counter_batches'][0]['vehicles'] = 'e1'
    expect_error(edge_as_count, 'vehicle count')

    duplicate_window = copy.deepcopy(base)
    duplicate_window['counter_batches'][1]['window_ticks'] = \
        duplicate_window['counter_batches'][0]['window_ticks']
    expect_error(duplicate_window, 'duplicate counter window')

    missing_field = copy.deepcopy(base)
    del missing_field['corridors'][0]['travel_time_ms']
    expect_error(missing_field, 'travel time')


def test_publish_replay_and_digest():
    doc = load_fixture('corridor_contention.json')
    result = materialize(doc)
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / 'ledger.sqlite'
        info = publish(archive, 'corr-original', doc, result)
        stored = load(archive, 'corr-original')
        assert encode(stored) == encode(result)
        replayed = materialize(load(archive, 'corr-original', source=True))
        assert encode(replayed) == encode(result)
        try:
            publish(archive, 'corr-original', doc, result)
        except ValueError as exc:
            assert 'already exists' in str(exc)
        else:
            raise AssertionError('duplicate edition must be rejected')
        with sqlite3.connect(archive) as db:
            row = db.execute('SELECT source_hash, result_hash FROM editions').fetchone()
        assert row == (info['source_sha256'], info['result_sha256'])


def main():
    tests = [value for name, value in sorted(globals().items())
             if name.startswith('test_') and callable(value)]
    for test in tests:
        test()
        print(f'ok {test.__name__}')
    print(f'{len(tests)} tests passed')


if __name__ == '__main__':
    main()
