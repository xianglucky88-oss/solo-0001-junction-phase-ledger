#!/usr/bin/env python3
"""Green-light acceptance suite: input -> preview -> publish -> readback.

Run from the repository root:

    python3 -I acceptance/run_acceptance.py

Part 1 feeds the two hand-built corridor fixtures through the real CLI
(preview, publish, show, source, replay, history) and compares every result
against the paper-derived literals in acceptance/expected.py.

Part 2 injects three controlled regressions into in-process copies of the
pipeline and proves each one flips exactly its predicted expectations while
everything else stays green. Files under greenbook/ are never modified.
"""
import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from acceptance import expected  # noqa: E402
from greenbook import observed, workflow  # noqa: E402
from greenbook.common import decode, encode  # noqa: E402
from greenbook.workflow import materialize  # noqa: E402

# Captured before any in-process mutation so replacement functions can call
# the genuine production implementation without recursing into themselves.
ORIGINAL_SUMMARIZE = workflow.summarize
ORIGINAL_INSIDE = observed.inside
ORIGINAL_DURATION = workflow.duration

FIXTURES = {
    'corridor_a_abutting': {
        'path': ROOT / 'acceptance/fixtures/corridor_a_abutting.json',
        'detector': 'loop-a'},
    'corridor_b_overlap': {
        'path': ROOT / 'acceptance/fixtures/corridor_b_overlap.json',
        'detector': 'loop-b'},
}

# Every probe that can fail, grouped by the regression class it guards.
# R1 = closed right end counted into a half-open service
# R2 = overlapping heartbeats counted as extra online time
# R3 = correct facts, ambiguous numerator leaked at the reporting layer
PROBES = [
    ('R1-facts',          'arrival states (half-open service right end)'),
    ('R2-refs',           'heartbeat chaining across the splice point'),
    ('R2-obs-main-n',     'main bucket N observation union'),
    ('R2-obs-wide-n',     'wide bucket N observation union'),
    ('R2-obs-wide-e',     'wide bucket E observation union'),
    ('R2-obs-cycle',      'cycle observation unions (N and E)'),
    ('R3-main',           'main bucket classification and 1/3 share'),
    ('R3-wide',           'wide bucket classification and 1/2 share'),
    ('R3-cycle',          'two-cycle classification (1/3 then integer 1)'),
    ('E2e-cli',           'preview/publish/show/source/replay/history'),
]
PROBE_IDS = [pid for pid, _ in PROBES]


class Report:
    """scenario -> probe_id -> findings, each tagged with the fixture name."""

    def __init__(self):
        self.rows = {}

    def add(self, scenario, probe, ok, detail='', fixture='shared'):
        self.rows.setdefault(scenario, {}).setdefault(probe, []).append(
            (bool(ok), detail, fixture))

    def failed_probes(self, scenario):
        return {pid for pid, findings in self.rows[scenario].items()
                if any(not ok for ok, _, _ in findings)}


def row_of(stats, phase):
    return next(r for r in stats if r['phase'] == phase)


def expect_row(report, scenario, probe, stats, phase, want, name):
    row = row_of(stats, phase)
    ok = (row['certain_green'] == want['green']
          and row['certain_non_green'] == want['not_green']
          and row['unresolved_or_boundary'] == want['unresolved']
          and row['green_share_of_classified'] == want['share'])
    report.add(scenario, probe, ok,
               f'{phase}: g={row["certain_green"]} r={row["certain_non_green"]} '
               f'u={row["unresolved_or_boundary"]} share={row["green_share_of_classified"]}',
               name)


def expect_obs(report, scenario, probe, stats, phase, name, key):
    row = row_of(stats, phase)
    want = expected.OBSERVATION[name][key]
    detector = FIXTURES[name]['detector']
    if want is None:
        ok = row['observation_ms_by_detector'] == {}
        got = row['observation_ms_by_detector']
    else:
        got = row['observation_ms_by_detector'].get(detector, 0)
        ok = got == want['union']
    report.add(scenario, probe, ok,
               f'{key} -> {got}, want {None if want is None else want["union"]}', name)


def check_materialized(name, doc, report, scenario):
    facts = {f['id']: f for f in doc['arrivals']}

    # R1: arrival facts, including the service-edge boundary behavior.
    for aid, want in expected.ARRIVALS.items():
        got = facts[aid]
        report.add(scenario, 'R1-facts',
                   got['state'] == want['state']
                   and got['phase'] == want['phase']
                   and got['wiring'] == want['wiring']
                   and got['service_refs'] == want['service_refs'],
                   f'{aid}: {got["state"]} want {want["state"]}', name)
        want_refs = expected.HEARTBEAT_REFS[name][aid]
        report.add(scenario, 'R2-refs',
                   got.get('heartbeat_refs') == want_refs,
                   f'{aid}: {got.get("heartbeat_refs")} want {want_refs}', name)

    # Service reconstruction and unusable edges.
    svc_ok = len(doc['services']) == len(expected.SERVICES) and all(
        (s['sequence'], s['phase'], s['certain'], s['possible']) ==
        (w['sequence'], w['phase'], w['certain'], w['possible'])
        for s, w in zip(doc['services'], expected.SERVICES))
    report.add(scenario, 'R1-facts', svc_ok, 'service intervals', name)
    gaps = [(g['reason'], g['edges']) for g in doc['unusable_edges']]
    report.add(scenario, 'R1-facts', gaps == expected.UNUSABLE_REASONS, f'gaps={gaps}', name)
    # Two cycles, one per N-phase start edge.
    windows = [c['certain'] for c in doc['cycles']]
    edges = [c['edges'] for c in doc['cycles']]
    report.add(scenario, 'R1-facts',
               windows == expected.CYCLE_WINDOWS and edges == expected.CYCLE_EDGES,
               f'cycles={windows}', name)

    main_n, wide_n = doc['buckets']['main'], doc['buckets']['wide']
    expect_obs(report, scenario, 'R2-obs-main-n', main_n, 'N', name, ('main', 'N'))
    expect_obs(report, scenario, 'R2-obs-wide-n', wide_n, 'N', name, ('wide', 'N'))
    expect_obs(report, scenario, 'R2-obs-wide-e', wide_n, 'E', name, ('wide', 'E'))
    expect_row(report, scenario, 'R3-main', main_n, 'N', expected.BUCKET_ROWS['main']['N'], name)
    expect_row(report, scenario, 'R3-main', main_n, 'E', expected.BUCKET_ROWS['main']['E'], name)
    expect_row(report, scenario, 'R3-wide', wide_n, 'N', expected.BUCKET_ROWS['wide']['N'], name)
    expect_row(report, scenario, 'R3-wide', wide_n, 'E', expected.BUCKET_ROWS['wide']['E'], name)
    for i, cycle in enumerate(doc['cycles'], start=1):
        expect_obs(report, scenario, 'R2-obs-cycle', cycle['statistics'],
                   'N', name, (f'cycle{i}', 'N'))
        expect_obs(report, scenario, 'R2-obs-cycle', cycle['statistics'],
                   'E', name, (f'cycle{i}', 'E'))
        want = expected.CYCLE_ROWS[i - 1]
        expect_row(report, scenario, 'R3-cycle', cycle['statistics'], 'N', want['N'], name)
        expect_row(report, scenario, 'R3-cycle', cycle['statistics'], 'E', want['E'], name)


def run_cli(*args):
    # -P keeps the script directory out of sys.path (no cwd code injection);
    # PYTHONPATH points at the trusted repository root explicitly, since -I/-P
    # do not add the cwd automatically.
    import os
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    proc = subprocess.run([sys.executable, '-P', '-m', 'greenbook', *map(str, args)],
                          cwd=ROOT, capture_output=True, text=True, env=env)
    return proc.returncode, proc.stdout, proc.stderr


def cli_section(report):
    materialized = {}
    sources = {}
    for name, meta in FIXTURES.items():
        sources[name] = decode(meta['path'].read_text(encoding='utf-8'))
        materialized[name] = materialize(sources[name])
        rc, out, err = run_cli('preview', meta['path'])
        report.add(name, 'E2e-cli', rc == 0 and decode(out) == materialized[name],
                   'preview mismatch' if rc == 0 else err, name)

    scenario = 'baseline (no mutation)'
    with tempfile.TemporaryDirectory() as tmp:
        ledger = Path(tmp) / 'ledger.sqlite'
        receipts = {}
        for i, name in enumerate(FIXTURES):
            edition = f'ed-{i}'
            rc, out, err = run_cli('publish', FIXTURES[name]['path'], ledger, edition)
            receipt = decode(out) if rc == 0 else {}
            receipts[edition] = receipt
            src_hash = hashlib.sha256(encode(sources[name]).encode()).hexdigest()
            res_hash = hashlib.sha256(encode(materialized[name]).encode()).hexdigest()
            report.add(scenario, 'E2e-cli',
                       rc == 0 and receipt.get('source_sha256') == src_hash
                       and receipt.get('result_sha256') == res_hash, f'publish {name}: {err}', name)
            rc2, _, _ = run_cli('publish', FIXTURES[name]['path'], ledger, edition)
            report.add(scenario, 'E2e-cli', rc2 == 2, 'duplicate edition was accepted', name)

            rc, out, err = run_cli('show', ledger, edition)
            show_ok = rc == 0 and decode(out) == materialized[name]
            rc, out, err = run_cli('source', ledger, edition)
            source_ok = rc == 0 and decode(out) == sources[name]
            rc, out, err = run_cli('replay', ledger, edition)
            replay_ok = rc == 0 and decode(out) == materialized[name]
            report.add(scenario, 'E2e-cli', show_ok and source_ok and replay_ok,
                       f'{name} show={show_ok} source={source_ok} replay={replay_ok}', name)

        rc, out, err = run_cli('history', ledger)
        history = decode(out) if rc == 0 else []
        report.add(scenario, 'E2e-cli',
                   [h['edition'] for h in history] == list(receipts)
                   and all(h['source_sha256'] == receipts[h['edition']]['source_sha256']
                           and h['result_sha256'] == receipts[h['edition']]['result_sha256']
                           for h in history),
                   'history digests', 'shared')

    with tempfile.TemporaryDirectory() as tmp:
        ledger = Path(tmp) / 'empty.sqlite'
        rc, _, _ = run_cli('publish', ROOT / 'examples/invalid.json', ledger, 'x')
        report.add(scenario, 'E2e-cli', rc == 2 and not ledger.exists(),
                   'invalid bundle was published', 'shared')


# --------------------------------------------------------------------------
# Controlled regressions, applied in-process only and always restored.
# --------------------------------------------------------------------------

def closed_right_inside(point, interval):
    """R1 mutation: count the closed right end as inside the half-open service."""
    return interval[0] <= point[0] <= point[1] <= interval[1]


def naive_duration(windows):
    """R2 mutation: sum heartbeat pieces, double-counting overlaps."""
    return sum(hi - lo for lo, hi in windows)


def leaky_summarize(window, phases, coverage, arrivals):
    """R3 mutation: facts stay correct, but every touch-only/ambiguous fact is
    added to the green numerator at the reporting layer."""
    output = ORIGINAL_SUMMARIZE(window, phases, coverage, arrivals)
    from fractions import Fraction
    for row in output:
        leaked = [x for x in row['unresolved_or_boundary']]
        if not leaked:
            continue
        greens = row['certain_green'] + leaked
        reds = row['certain_non_green']
        row['certain_green'] = greens
        row['unresolved_or_boundary'] = []
        q = Fraction(len(greens), len(greens) + len(reds))
        row['green_share_of_classified'] = (q.numerator if q.denominator == 1
                                            else f'{q.numerator}/{q.denominator}')
    return output


MUTATIONS = [
    ('R1 closed-right service', [(observed, 'inside', closed_right_inside)]),
    ('R2 overlapping beats as extra time', [(workflow, 'duration', naive_duration)]),
    ('R3 ambiguous leaked into numerator', [(workflow, 'summarize', leaky_summarize)]),
]

# Paper prediction: exactly these probes must fail under each mutation.
PREDICTED_FAILURES = {
    'baseline (no mutation)': set(),
    'R1 closed-right service': {'R1-facts', 'R3-main', 'R3-wide', 'R3-cycle'},
    'R2 overlapping beats as extra time': {'R2-obs-main-n', 'R2-obs-wide-n',
                                           'R2-obs-wide-e', 'R2-obs-cycle'},
    'R3 ambiguous leaked into numerator': {'R3-main', 'R3-wide', 'R3-cycle'},
}

# The R2 mutation must be invisible on fixture A (heartbeats merely abut, so
# union == naive sum) and visible on every overlap probe of fixture B only.
R2_FIXTURE_PREDICTION = {
    'corridor_a_abutting': set(),
    'corridor_b_overlap': {'R2-obs-main-n', 'R2-obs-wide-n',
                           'R2-obs-wide-e', 'R2-obs-cycle'},
}


def mutation_section(report):
    for scenario, patches in MUTATIONS:
        saved = [(mod, attr, getattr(mod, attr)) for mod, attr, _ in patches]
        try:
            for mod, attr, replacement in patches:
                setattr(mod, attr, replacement)
            for name, meta in FIXTURES.items():
                doc = materialize(decode(meta['path'].read_text(encoding='utf-8')))
                check_materialized(name, doc, report, scenario)
        finally:
            for mod, attr, original in saved:
                setattr(mod, attr, original)


def main():
    report = Report()
    scenario = 'baseline (no mutation)'
    for name, meta in FIXTURES.items():
        doc = materialize(decode(meta['path'].read_text(encoding='utf-8')))
        check_materialized(name, doc, report, scenario)
    cli_section(report)
    mutation_section(report)

    scenarios = list(report.rows)
    width = max(len(s) for s in scenarios)
    print('\nExpectation -> verdict matrix\n')
    header = 'scenario'.ljust(width) + '  ' + '  '.join(pid.ljust(16) for pid in PROBE_IDS)
    print(header)
    print('-' * len(header))
    for scenario in scenarios:
        cells = []
        for pid in PROBE_IDS:
            findings = report.rows[scenario].get(pid)
            if not findings:
                cells.append('n/a'.ljust(16))
            elif all(ok for ok, _, _ in findings):
                cells.append('PASS'.ljust(16))
            else:
                reason = next(d for ok, d, _ in findings if not ok)
                cells.append(f'FAIL {reason[:11]}'.ljust(16))
        print(scenario.ljust(width) + '  ' + ' '.join(cells))

    print('\nProbe meanings:')
    for pid, label in PROBES:
        print(f'  {pid:16} {label}')

    all_match = True
    print('\nPrediction correspondence:')
    for scenario, want in PREDICTED_FAILURES.items():
        got = report.failed_probes(scenario)
        match = got == want
        all_match &= match
        print(f'  {scenario:38} {"matches" if match else "MISMATCH"} '
              f'(failed: {sorted(got) or "none"})')

    # Fixture-pair discrimination for R2: the abutting fixture must stay fully
    # green under the naive-sum mutation; only the overlapping fixture trips.
    r2 = report.rows['R2 overlapping beats as extra time']
    print('\nR2 fixture discrimination:')
    r2_pair_match = True
    for name, want_probes in R2_FIXTURE_PREDICTION.items():
        failed_here = {pid for pid, findings in r2.items()
                       if any(not ok and fx == name for ok, _, fx in findings)}
        match = failed_here == want_probes
        r2_pair_match &= match
        print(f'  {name:22} {"matches" if match else "MISMATCH"} '
              f'(failed: {sorted(failed_here) or "none"})')
    all_match &= r2_pair_match

    print('\n' + ('ALL GREEN: acceptance matches the hand-derived prediction.'
                  if all_match else 'RED: acceptance prediction mismatch.'))
    return 0 if all_match else 1


if __name__ == '__main__':
    raise SystemExit(main())
