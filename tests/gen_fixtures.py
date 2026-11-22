"""Generate the two corridor acceptance bundles from the same evidence base.

Both bundles keep every clock/edge/wiring/heartbeat/arrival/bucket record of
examples/baseline.json so each station keeps the identical green rate 1/2.
Only the counter batches differ, which changes the pairability relations.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = json.loads((ROOT / 'examples' / 'baseline.json').read_text(encoding='utf-8'))

# One extra heartbeat covers the late loop window so the additional evidenced
# ambiguous arrival below has full clock + wiring + heartbeat support.
EXTRA_HEARTBEAT = {'id': 'h5', 'detector': 'loop', 'ticks': [180, 310]}
# Tick 200 -> 199..201 ms on loop, straddling the e1/e2 service boundary and
# inside wire-old + heartbeat coverage: ambiguous_service. It is never
# classified, so the 1/2 green share stays untouched in both bundles.
EXTRA_ARRIVAL = {'id': 'a-maybe', 'detector': 'loop', 'tick': 200}

UPSTREAM_CLOCK = {
    'id': 'clock-counter',
    'device': 'counter-A',
    'ticks': [0, 200],
    'offset_ms': 0,
    'error_ms': 2,
}

CORRIDOR = {
    'id': 'corridor-main',
    'link': 'link-A',
    'upstream_device': 'counter-A',
    'downstream_detector': 'loop',
    'travel_time_ms': [40, 80],
}

# Contention: a-green, a-red and a-maybe are reachable from both clocked
# batches while a-edge only from batch-a2. Non-certain-green edges number three
# (a-red, a-edge, a-maybe), so one maximum-volume matching needs no green at
# all and another needs one: guaranteed green range [0, 1].
BATCHES_A = [
    {'id': 'batch-a1', 'corridor': 'corridor-main', 'device': 'counter-A',
     'window_ticks': [10, 75], 'vehicles': 1},
    {'id': 'batch-a2', 'corridor': 'corridor-main', 'device': 'counter-A',
     'window_ticks': [16, 120], 'vehicles': 2},
    {'id': 'batch-a3', 'corridor': 'corridor-main', 'device': 'counter-A',
     'window_ticks': [300, 330], 'vehicles': 1},
]

# Same released total and identical evidence, but disjoint feasible windows:
# no downstream record is reachable from more than one batch, so the single
# green record is structurally mandatory inside every maximum matching and the
# range collapses to [1, 1].
BATCHES_B = [
    {'id': 'batch-b1', 'corridor': 'corridor-main', 'device': 'counter-A',
     'window_ticks': [0, 10], 'vehicles': 1},
    {'id': 'batch-b2', 'corridor': 'corridor-main', 'device': 'counter-A',
     'window_ticks': [45, 130], 'vehicles': 2},
    {'id': 'batch-b3', 'corridor': 'corridor-main', 'device': 'counter-A',
     'window_ticks': [300, 330], 'vehicles': 1},
]


def bundle(batches):
    doc = dict(BASE)
    doc['clock_certificates'] = BASE['clock_certificates'] + [UPSTREAM_CLOCK]
    doc['heartbeats'] = BASE['heartbeats'] + [EXTRA_HEARTBEAT]
    doc['arrivals'] = BASE['arrivals'] + [EXTRA_ARRIVAL]
    doc['corridors'] = [CORRIDOR]
    doc['counter_batches'] = batches
    return doc


out = ROOT / 'tests' / 'fixtures'
out.mkdir(parents=True, exist_ok=True)
(out / 'corridor_contention.json').write_text(
    json.dumps(bundle(BATCHES_A), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
(out / 'corridor_disjoint.json').write_text(
    json.dumps(bundle(BATCHES_B), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print('wrote', out)
