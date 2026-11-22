import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / 'examples'


def run(*args, cwd=ROOT):
    return subprocess.run([sys.executable, '-m', 'greenbook', *map(str, args)],
                          cwd=cwd, capture_output=True, text=True)


def preview(path):
    done = run('preview', path)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def load(name):
    return json.loads((EXAMPLES / name).read_text(encoding='utf-8'))


class SingleRingUnchanged(unittest.TestCase):
    """The frozen single-ring editions must keep their exact published results."""

    def test_baseline_values(self):
        result = preview(EXAMPLES / 'baseline.json')
        full = {row['phase']: row for row in result['buckets']['full']}
        self.assertEqual(full['N']['certain_green'], ['a-green'])
        self.assertEqual(full['N']['certain_non_green'], ['a-red'])
        self.assertEqual(full['N']['green_share_of_classified'], '1/2')
        self.assertEqual(full['N']['unresolved_or_boundary'], ['a-edge'])
        self.assertEqual(full['N']['observation_ms_by_detector'], {'loop': 239})
        states = {row['id']: row['state'] for row in result['arrivals']}
        self.assertEqual(states['a-edge'], 'ambiguous_service')
        self.assertEqual(states['a-rewire'], 'unassigned_wiring')
        self.assertEqual(result['cycles'][0]['certain'], [12, 198])
        self.assertNotIn('crossings', result)
        self.assertNotIn('rings', result)

    def test_changed_honours_heartbeat_gap(self):
        result = preview(EXAMPLES / 'changed.json')
        full = {row['phase']: row for row in result['buckets']['full']}
        self.assertEqual(full['N']['observation_ms_by_detector'], {'loop': 236})
        self.assertEqual(full['N']['certain_green'], [])
        states = {row['id']: row['state'] for row in result['arrivals']}
        self.assertEqual(states['a-green'], 'out_of_observation')

    def test_invalid_rejected(self):
        done = run('preview', EXAMPLES / 'invalid.json')
        self.assertEqual(done.returncode, 2)
        self.assertIn('duplicate controller sequence', done.stderr)
        self.assertEqual(done.stdout, '')


class DualRings(unittest.TestCase):
    def test_both_certain(self):
        result = preview(EXAMPLES / 'dual.json')
        states = {row['bucket']: row for row in result['crossings']}
        self.assertEqual(states['early']['state'], 'certain')
        self.assertEqual(states['early']['certain'], [[22, 98]])
        self.assertEqual(states['late']['state'], 'certain')
        self.assertEqual(states['late']['certain'], [[222, 298]])

    def test_each_legal_but_only_some_schedules_meet(self):
        result = preview(EXAMPLES / 'dual-possible.json')
        row = result['crossings'][0]
        self.assertEqual(row['state'], 'possible')
        self.assertEqual(row['certain'], [])
        self.assertEqual(row['possible'], [[98, 102]])
        self.assertEqual(row['ring_states'], {'ring-a': 'certain', 'ring-b': 'certain'})
        self.assertEqual(row['orderings'], ['ring-a<ring-b', 'ring-b<ring-a', 'simultaneous'])
        # Every ring is legal on its own: one service each, no gaps blamed.
        for member in result['rings']['members'].values():
            self.assertEqual(len(member['services']), 1)
            self.assertEqual([g['reason'] for g in member['unusable_edges']], ['open_ended'])

    def test_each_legal_but_jointly_conflicting(self):
        result = preview(EXAMPLES / 'dual-conflict.json')
        row = result['crossings'][0]
        self.assertEqual(row['state'], 'conflict')
        self.assertEqual(row['reason'], 'no_shared_crossing')
        self.assertEqual(row['certain'], [])
        self.assertEqual(row['possible'], [])
        self.assertEqual(row['orderings'], [])
        # The per-ring services are not revoked to create the conflict.
        for member in result['rings']['members'].values():
            self.assertEqual(len(member['services']), 1)
        self.assertEqual(row['services'], {'ring-a': ['a0'], 'ring-b': ['b0']})

    def test_one_ring_missing_keeps_earlier_crossing(self):
        result = preview(EXAMPLES / 'dual-partial.json')
        rows = {row['bucket']: row for row in result['crossings']}
        self.assertEqual(rows['early']['state'], 'certain')
        self.assertEqual(rows['late']['state'], 'incomplete')
        self.assertEqual(rows['late']['missing'], ['ring-b'])
        self.assertEqual(rows['late']['reason'], 'missing_reports')
        # The earlier crossing keeps its services; the later gap is attributed.
        self.assertEqual(rows['early']['services'], {'ring-a': ['a0'], 'ring-b': ['b0']})
        ring_b = result['rings']['members']['ring-b']
        self.assertIn(['b2', 'b4'], [g['edges'] for g in ring_b['unusable_edges']])
        self.assertEqual([s['phase'] for s in result['rings']['members']['ring-a']['services']],
                         ['N', 'E', 'N', 'E', 'N'])

    def test_read_in_order_does_not_pick_the_first_crossing_ring(self):
        doc = load('dual-possible.json')
        swapped = copy.deepcopy(doc)
        swapped['rings'] = list(reversed(swapped['rings']))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'swapped.json'
            path.write_text(json.dumps(swapped), encoding='utf-8')
            self.assertEqual(preview(EXAMPLES / 'dual-possible.json'), preview(path))

    def test_ring_ids_do_not_pick_the_first_crossing_ring(self):
        doc = load('dual-possible.json')
        renamed = copy.deepcopy(doc)
        remap = {'ring-a': 'ring-z', 'ring-b': 'ring-a'}
        for ring in renamed['rings']:
            ring['id'] = remap[ring['id']]
        for edge in renamed['edges']:
            edge['ring'] = remap[edge['ring']]
        renamed['crossing']['rings'] = {remap[k]: v for k, v in renamed['crossing']['rings'].items()}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'renamed.json'
            path.write_text(json.dumps(renamed), encoding='utf-8')
            before, after = preview(EXAMPLES / 'dual-possible.json')['crossings'][0], preview(path)['crossings'][0]
        self.assertEqual(before['state'], after['state'])
        self.assertEqual(before['certain'], after['certain'])
        self.assertEqual(before['possible'], after['possible'])
        self.assertEqual(len(before['orderings']), len(after['orderings']))


class DualInput(unittest.TestCase):
    def test_invalid_dual_shapes_rejected(self):
        base = load('dual.json')
        cases = {
            'one ring': lambda d: d.__setitem__('rings', d['rings'][:1]),
            'duplicate ring id': lambda d: d['rings'][1].__setitem__('id', 'ring-a'),
            'shared phase': lambda d: d['rings'][0].__setitem__('phase_order', ['N', 'S']),
            'crossing omits ring': lambda d: d['crossing']['rings'].pop('ring-b'),
            'crossing phase misplaced': lambda d: d['crossing']['rings'].__setitem__('ring-a', 'S'),
            'unknown edge ring': lambda d: d['edges'][0].__setitem__('ring', 'ring-c'),
            'no crossing': lambda d: d.pop('crossing'),
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'case.json'
            for name, mutate in cases.items():
                doc = copy.deepcopy(base)
                mutate(doc)
                path.write_text(json.dumps(doc), encoding='utf-8')
                done = run('preview', path)
                self.assertEqual(done.returncode, 2, f'{name} was accepted')

    def test_edges_assignable_by_phase(self):
        doc = load('dual.json')
        for edge in doc['edges']:
            edge.pop('ring')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'by-phase.json'
            path.write_text(json.dumps(doc), encoding='utf-8')
            self.assertEqual(preview(path)['crossings'], preview(EXAMPLES / 'dual.json')['crossings'])


class Archive(unittest.TestCase):
    def test_publish_show_source_replay_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'ledger.sqlite'
            self.assertEqual(run('publish', EXAMPLES / 'baseline.json', archive, 'original').returncode, 0)
            self.assertEqual(run('publish', EXAMPLES / 'dual.json', archive, 'dual').returncode, 0)

            shown = json.loads(run('show', archive, 'dual').stdout)
            self.assertEqual([c['state'] for c in shown['crossings']], ['certain', 'certain'])
            source = json.loads(run('source', archive, 'dual').stdout)
            self.assertEqual(len(source['rings']), 2)

            self.assertEqual(run('replay', archive, 'original').returncode, 0)
            self.assertEqual(run('replay', archive, 'dual').returncode, 0)

            names = [row['edition'] for row in json.loads(run('history', archive).stdout)]
            self.assertEqual(names, ['original', 'dual'])

            duplicate = run('publish', EXAMPLES / 'dual.json', archive, 'dual')
            self.assertEqual(duplicate.returncode, 2)
            self.assertIn('edition already exists', duplicate.stderr)

    def test_failed_publish_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'ledger.sqlite'
            self.assertEqual(run('publish', EXAMPLES / 'invalid.json', archive, 'bad').returncode, 2)
            self.assertFalse(archive.exists() and json.loads(run('history', archive).stdout))


if __name__ == '__main__':
    unittest.main()
