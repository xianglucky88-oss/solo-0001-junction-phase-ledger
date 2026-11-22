#!/usr/bin/env python3
"""Local evidence harness for retention/MEMO.md.

It does not add a greenbook command, does not touch the greenbook package, and
does not publish anything. It only drives the existing `python3 -m greenbook
preview` entrypoint and compares:

  * the partner-facing projection: per fixed bucket, certain-service ms per
    phase, plus certified-online ms per detector under its wiring phase; and
  * the exact overlap computed from the full artifact's `services` and
    `coverage`.

Run:  python3 -I retention/check.py
"""
import hashlib
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def preview(name):
    out = subprocess.run(
        [sys.executable, '-m', 'greenbook', 'preview', str(HERE / f'{name}.json')],
        cwd=ROOT, check=True, capture_output=True, text=True, encoding='utf-8')
    return json.loads(out.stdout)


def bundle(name):
    return json.loads((HERE / f'{name}.json').read_text(encoding='utf-8'))


def union(segments):
    result = []
    for lo, hi in sorted(segments):
        if lo >= hi:
            continue
        if result and lo <= result[-1][1]:
            result[-1][1] = max(result[-1][1], hi)
        else:
            result.append([lo, hi])
    return result


def measure(segments):
    return sum(hi - lo for lo, hi in union(segments))


def clip(segments, lo, hi):
    return [[max(a, lo), min(b, hi)] for a, b in segments if max(a, lo) < min(b, hi)]


def green(artifact, phase, lo, hi):
    return union(clip([s['certain'] for s in artifact['services'] if s['phase'] == phase], lo, hi))


def online(artifact, phase, lo, hi):
    """Certified-online pieces in [lo,hi), keyed by (detector, wiring record)."""
    found = defaultdict(list)
    for c in artifact['coverage']:
        if c['phase'] == phase:
            found[(c['detector'], c['wiring'])].extend(clip([c['time_ms']], lo, hi))
    return {key: union(value) for key, value in found.items()}


def overlap(left, right):
    return [[max(a, c), min(b, d)] for a, b in left for c, d in right if max(a, c) < min(b, d)]


def projection(doc, artifact):
    """What the partner keeps: sums only, no ids beyond phase/detector, no intervals."""
    rows = []
    for bucket in sorted(doc['buckets'], key=lambda b: b['time_ms'][0]):
        lo, hi = bucket['time_ms']
        phases = {}
        for phase in doc['phase_order']:
            by_detector = defaultdict(list)
            for (detector, _wire), segments in online(artifact, phase, lo, hi).items():
                by_detector[detector].extend(segments)
            per_detector = {d: measure(segs) for d, segs in by_detector.items() if measure(segs)}
            phases[phase] = {
                'certain_service_ms': measure(green(artifact, phase, lo, hi)),
                'online_ms_by_detector': dict(sorted(per_detector.items())),
            }
        rows.append({'bucket': bucket['id'], 'time_ms': [lo, hi], 'phases': phases})
    return rows


def rows(doc, artifact):
    """Exact per (bucket, wiring phase, detector, wiring episode) overlap."""
    out = []
    for bucket in sorted(doc['buckets'], key=lambda b: b['time_ms'][0]):
        lo, hi = bucket['time_ms']
        length = hi - lo
        for phase in doc['phase_order']:
            g = green(artifact, phase, lo, hi)
            for (detector, wiring), o in sorted(online(artifact, phase, lo, hi).items()):
                a, b = measure(g), measure(o)
                out.append({'bucket': bucket['id'], 'phase': phase, 'detector': detector,
                            'wiring': wiring, 'length': length, 'green_ms': a, 'online_ms': b,
                            'overlap_ms': measure(overlap(g, o)),
                            'old_lower': max(0, a + b - length), 'old_upper': min(a, b)})
    return out


def merged(doc, artifact, phase, detector):
    """Exact overlap and old bounds over the union of all declared buckets."""
    lo = min(b['time_ms'][0] for b in doc['buckets'])
    hi = max(b['time_ms'][1] for b in doc['buckets'])
    length = sum(b['time_ms'][1] - b['time_ms'][0] for b in doc['buckets'])
    g = green(artifact, phase, lo, hi)
    pieces = [s for (d, _w), segs in online(artifact, phase, lo, hi).items()
              if d == detector for s in segs]
    a, b = measure(g), measure(pieces)
    return {'length': length, 'green_ms': a, 'online_ms': b,
            'overlap_ms': measure(overlap(g, pieces)),
            'old_lower': max(0, a + b - length), 'old_upper': min(a, b)}


def verdict(value, threshold):
    return 'PASS' if value >= threshold else 'FAIL'


def bounded(lower, upper, threshold):
    if threshold <= lower:
        return 'PASS'
    if threshold > upper:
        return 'FAIL'
    return 'UNDECIDED'


def digest(value):
    text = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]


def main():
    alpha_doc, beta_doc = bundle('alpha'), bundle('beta')
    alpha, beta = preview('alpha'), preview('beta')
    pa, pb = projection(alpha_doc, alpha), projection(beta_doc, beta)

    print('### A. 见证对 alpha / beta（投影相同，完整产物 coverage 不同）')
    print('projection(alpha) == projection(beta):', pa == pb, digest(pa), digest(pb))
    print('services(alpha) == services(beta):', alpha['services'] == beta['services'])
    print('coverage(alpha) == coverage(beta):', alpha['coverage'] == beta['coverage'])
    print('projection[b000]:', json.dumps(pa[0], ensure_ascii=False))
    for name, art in (('alpha', alpha), ('beta', beta)):
        cov = [c for c in art['coverage'] if c['detector'] == 'loop' and c['phase'] == 'N']
        print(f'  {name} loop/N coverage:', [[c['heartbeat'], c['time_ms']] for c in cov])
    gamma_doc, gamma = bundle('gamma'), preview('gamma')
    pg = projection(gamma_doc, gamma)
    print('projection(gamma) == projection(alpha):', pg == pa, digest(pg))
    print('services(gamma) == services(alpha):', gamma['services'] == alpha['services'])
    wa = next(r for r in rows(alpha_doc, alpha)
              if (r['bucket'], r['phase'], r['detector']) == ('b000', 'N', 'loop'))
    wb = next(r for r in rows(beta_doc, beta)
              if (r['bucket'], r['phase'], r['detector']) == ('b000', 'N', 'loop'))
    wg = next(r for r in rows(gamma_doc, gamma)
              if (r['bucket'], r['phase'], r['detector']) == ('b000', 'N', 'loop'))
    for tag, w in (('alpha', wa), ('beta', wb), ('gamma', wg)):
        print(f'  b000/N/loop {tag:5}: A={w["green_ms"]} B={w["online_ms"]} O={w["overlap_ms"]} '
              f'old=[{w["old_lower"]},{w["old_upper"]}]')
    print('  投影一致，但 O 取到上界(38)、内点(28)、下界(27)：界是紧的，O 本身不被投影决定')
    for t in (27, 28, 29, 30, 38, 39):
        print(f'    T={t:>2}ms exact alpha {verdict(wa["overlap_ms"], t)} / beta '
              f'{verdict(wb["overlap_ms"], t)} / gamma {verdict(wg["overlap_ms"], t)}; '
              f'old projection {bounded(wa["old_lower"], wa["old_upper"], t)}')

    print('\n### B. 反方向：投影相同，完整产物 services 不同（shift-a / shift-b）')
    sa_doc, sb_doc = bundle('shift-a'), bundle('shift-b')
    sa, sb = preview('shift-a'), preview('shift-b')
    qa, qb = projection(sa_doc, sa), projection(sb_doc, sb)
    print('projection ==:', qa == qb, digest(qa), digest(qb))
    print('services(shift-a) == services(shift-b):', sa['services'] == sb['services'])
    print('coverage(shift-a) == coverage(shift-b):', sa['coverage'] == sb['coverage'])
    ra = rows(sa_doc, sa)[0]
    rb = rows(sb_doc, sb)[0]
    print(f'  b000/N/loop a: A={ra["green_ms"]} B={ra["online_ms"]} O={ra["overlap_ms"]} '
          f'old=[{ra["old_lower"]},{ra["old_upper"]}]')
    print(f'  b000/N/loop b: A={rb["green_ms"]} B={rb["online_ms"]} O={rb["overlap_ms"]} '
          f'old=[{rb["old_lower"]},{rb["old_upper"]}]')
    for t in (5, 10, 20):
        print(f'    T={t:>2}ms exact a {verdict(ra["overlap_ms"], t)} / b '
              f'{verdict(rb["overlap_ms"], t)}; old projection '
              f'{bounded(ra["old_lower"], ra["old_upper"], t)}')

    print('\n### C. 合并相邻整桶（alpha / beta 的 14 个 50 ms 桶并到 [0,700)）')
    for name, doc, art in (('alpha', alpha_doc, alpha), ('beta', beta_doc, beta)):
        per_bucket = [r for r in rows(doc, art) if r['phase'] == 'N' and r['detector'] == 'loop']
        lo = sum(r['old_lower'] for r in per_bucket)
        hi = sum(r['old_upper'] for r in per_bucket)
        m = merged(doc, art, 'N', 'loop')
        print(f'  {name}: sum(O)={m["overlap_ms"]} sum(per-bucket old bounds)=[{lo},{hi}] '
              f'single-window old bound=[{m["old_lower"]},{m["old_upper"]}]')
        for t in (100, 130):
            print(f'    T={t}ms exact {verdict(m["overlap_ms"], t)}; sum-of-bounds '
                  f'{bounded(lo, hi, t)}; single-window {bounded(m["old_lower"], m["old_upper"], t)}')

    print('\n### C2. 合并后不重算 A+B-L，而按桶累加界')
    g_doc, g = bundle('beta'), preview('beta')
    nr = [r for r in rows(g_doc, g) if r['phase'] == 'N' and r['detector'] == 'loop']
    lo = sum(r['old_lower'] for r in nr)
    hi = sum(r['old_upper'] for r in nr)
    print(f'  beta 逐桶界和=[{lo},{hi}]，真实 sum(O)={sum(r["overlap_ms"] for r in nr)}；'
          f'T=130 逐桶界和 {bounded(lo, hi, 130)}，单窗口 {bounded(*[merged(g_doc, g, "N", "loop")[k] for k in ("old_lower", "old_upper")], 130)}')

    print('\n### D. 换接与接回同一相位')
    for name, threshold in (('reattach', 30), ('reattach_r2', 25), ('rewire_split', 15), ('merge', 50)):
        doc, art = bundle(name), preview(name)
        print(f'  {name} (T={threshold}ms):')
        ep, ph = defaultdict(int), defaultdict(int)
        for r in rows(doc, art):
            ep[(r['phase'], r['detector'], r['wiring'])] += r['overlap_ms']
            ph[(r['phase'], r['detector'])] += r['overlap_ms']
        for key, total in sorted(ep.items()):
            print(f'    episode    {key}: O={total} ({verdict(total, threshold)})')
        for key, total in sorted(ph.items()):
            print(f'    phase-only {key}: sum(O)={total} ({verdict(total, threshold)})')
    ra_art, rb_art = preview('reattach'), preview('reattach_r2')
    print('  reattach vs reattach_r2 projection identical:',
          projection(bundle('reattach'), ra_art) == projection(bundle('reattach_r2'), rb_art))

    print('\n### E. 旧投影仍不可判的情形')
    gaps_doc, gaps = bundle('gaps'), preview('gaps')
    for r in rows(gaps_doc, gaps):
        print(f'  gaps {r["bucket"]} {r["phase"]} {r["detector"]}/{r["wiring"]}: '
              f'A={r["green_ms"]} B={r["online_ms"]} O={r["overlap_ms"]} '
              f'old=[{r["old_lower"]},{r["old_upper"]}]')
    win_doc, win = bundle('windows'), preview('windows')
    for r in rows(win_doc, win):
        print(f'  windows {r["bucket"]} {r["phase"]}: A={r["green_ms"]} B={r["online_ms"]} '
              f'O={r["overlap_ms"]} old=[{r["old_lower"]},{r["old_upper"]}]')
    print('  windows 同一桶内 N 的确定窗被 E 切成 3 段，各 10 ms；O=30 是累计量，'
          '最长连续段只有 10 ms')
    t = next(r for r in rows(alpha_doc, alpha)
             if (r['bucket'], r['phase'], r['detector']) == ('b000', 'N', 'loop'))
    exact = measure(overlap(green(alpha, 'N', 30, 50), online(alpha, 'N', 30, 50)[('loop', 'wire-old')]))
    lower, upper = max(0, t['overlap_ms'] - (t['length'] - 20)), min(t['overlap_ms'], 20)
    print(f'  alpha b000/N/loop 窗口 [30,50)（20 ms）: 精确 O={exact}; 由桶总量 O={t["overlap_ms"]} '
          f'只能给界 [{lower},{upper}]；T=15ms {bounded(lower, upper, 15)}')

    print('\n### F. 记录规模与一致性恒等式')
    for name in ('alpha', 'beta', 'reattach', 'reattach_r2', 'rewire_split', 'merge', 'gaps', 'windows'):
        doc, art = bundle(name), preview(name)
        ep, ph = set(), set()
        for b in doc['buckets']:
            lo, hi = b['time_ms']
            for phase in doc['phase_order']:
                for (detector, wiring), segs in online(art, phase, lo, hi).items():
                    if measure(segs):
                        ep.add((b['id'], phase, detector, wiring))
                        ph.add((b['id'], phase, detector))
        print(f'  {name}: 口径B 行数={len(ep)}  口径A 行数={len(ph)}  增量={len(ep) - len(ph)}')
    for name in ('alpha', 'beta'):
        doc, art = bundle(name), preview(name)
        for r in rows(doc, art):
            assert r['old_lower'] <= r['overlap_ms'] <= r['old_upper'], r
        print(f'  {name}: 所有行满足 L<=O<=U')

    print('\n### F2. 桶边界本身没有被入口约束')
    import tempfile
    base = bundle('alpha')
    probes = {
        '重叠桶': [{'id': 'x', 'time_ms': [0, 50]}, {'id': 'y', 'time_ms': [25, 75]}],
        '有空隙': [{'id': 'a', 'time_ms': [0, 50]}, {'id': 'b', 'time_ms': [60, 110]}],
        '不等宽': [{'id': 'w', 'time_ms': [0, 50]}, {'id': 'v', 'time_ms': [50, 70]}],
    }
    for label, buckets in probes.items():
        doc = dict(base, buckets=buckets)
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8') as fh:
            json.dump(doc, fh)
            path = fh.name
        try:
            preview_path = subprocess.run(
                [sys.executable, '-m', 'greenbook', 'preview', path], cwd=ROOT,
                check=True, capture_output=True, text=True, encoding='utf-8')
            print(f'  {label}: 入口照收（不会拒绝），{len(json.loads(preview_path.stdout)["buckets"])} 个桶')
        except subprocess.CalledProcessError:
            print(f'  {label}: 入口拒绝')
        finally:
            Path(path).unlink()
    print('  说明：本报告的桶假设来自合作方约定，不是 greenbook 当前会强制的输入约束')

    print('\n### G. 事后改门槛')
    for t in (20, 30, 38):
        print(f'  T={t:>2}ms: alpha O={wa["overlap_ms"]} {verdict(wa["overlap_ms"], t)}, '
              f'beta O={wb["overlap_ms"]} {verdict(wb["overlap_ms"], t)}; '
              f'old projection {bounded(wa["old_lower"], wa["old_upper"], t)}')
    per_bucket = [r for r in rows(alpha_doc, alpha) if r['phase'] == 'N' and r['detector'] == 'loop']
    lo = sum(r['old_lower'] for r in per_bucket)
    hi = sum(r['old_upper'] for r in per_bucket)
    print(f'  merged alpha N/loop: exact O={merged(alpha_doc, alpha, "N", "loop")["overlap_ms"]}; '
          f'sum-of-bounds=[{lo},{hi}]; at T=130 {bounded(lo, hi, 130)}')


def assertions():
    """Make every claim in MEMO.md fail loudly if a fixture drifts."""
    alpha_doc, alpha = bundle('alpha'), preview('alpha')
    beta_doc, beta = bundle('beta'), preview('beta')
    gamma_doc, gamma = bundle('gamma'), preview('gamma')
    assert projection(alpha_doc, alpha) == projection(beta_doc, beta) == projection(gamma_doc, gamma)
    assert alpha['services'] == beta['services'] == gamma['services']
    assert alpha['coverage'] != beta['coverage']
    pick = lambda doc, art: next(r for r in rows(doc, art)
                                 if (r['bucket'], r['phase'], r['detector']) == ('b000', 'N', 'loop'))
    assert (pick(alpha_doc, alpha)['overlap_ms'], pick(beta_doc, beta)['overlap_ms'],
            pick(gamma_doc, gamma)['overlap_ms']) == (38, 28, 27)
    assert pick(alpha_doc, alpha)['old_lower'] == pick(alpha_doc, alpha)['old_upper'] - 11
    assert verdict(38, 30) == 'PASS' and verdict(28, 30) == 'FAIL'
    assert bounded(27, 38, 30) == 'UNDECIDED'

    sa_doc, sb_doc = bundle('shift-a'), bundle('shift-b')
    sa, sb = preview('shift-a'), preview('shift-b')
    assert projection(sa_doc, sa) == projection(sb_doc, sb)
    assert sa['coverage'] == sb['coverage'] and sa['services'] != sb['services']
    assert (rows(sa_doc, sa)[0]['overlap_ms'], rows(sb_doc, sb)[0]['overlap_ms']) == (0, 10)

    for name in ('alpha', 'beta'):
        doc, art = bundle(name), preview(name)
        assert all(r['old_lower'] <= r['overlap_ms'] <= r['old_upper'] for r in rows(doc, art))
        assert sum(r['overlap_ms'] for r in rows(doc, art) if r['phase'] == 'N' and r['detector'] == 'loop') \
            == merged(doc, art, 'N', 'loop')['overlap_ms']

    # 口径B：同一接线段在相邻桶上的 O 相加，应等于把窗口并起来的精确 O
    for name in ('alpha', 'beta', 'merge', 'reattach_r2'):
        doc, art = bundle(name), preview(name)
        lo = min(b['time_ms'][0] for b in doc['buckets'])
        hi = max(b['time_ms'][1] for b in doc['buckets'])
        by_episode = defaultdict(int)
        for r in rows(doc, art):
            by_episode[(r['phase'], r['detector'], r['wiring'])] += r['overlap_ms']
        for (phase, detector, wiring), total in by_episode.items():
            g = green(art, phase, lo, hi)
            segs = [c['time_ms'] for c in art['coverage']
                    if c['phase'] == phase and c['detector'] == detector and c['wiring'] == wiring]
            assert total == measure(overlap(g, clip(segs, lo, hi))), (name, phase, detector, wiring)

    re_doc, re_art = bundle('reattach'), preview('reattach')
    ep = {r['wiring']: r['overlap_ms'] for r in rows(re_doc, re_art) if r['phase'] == 'N'}
    assert ep == {'wire-n1': 20, 'wire-n2': 20}
    assert sum(ep.values()) == 40
    assert verdict(40, 30) == 'PASS' and all(verdict(v, 30) == 'FAIL' for v in ep.values())

    win_doc, win = bundle('windows'), preview('windows')
    w = rows(win_doc, win)[0]
    # 重叠心跳时，现有 summarize 用 duration() 合并成并集，不会重复计数
    overlap_hb = {
        'controller': 'ctrl', 'phase_order': ['N'],
        'clock_certificates': [
            {'id': 'clock-c', 'device': 'ctrl', 'ticks': [0, 1000], 'offset_ms': 0, 'error_ms': 0},
            {'id': 'clock-d', 'device': 'loop', 'ticks': [0, 1000], 'offset_ms': 0, 'error_ms': 0}],
        'edges': [{'id': 'e0', 'seq': 0, 'tick': 0, 'phase': 'N'},
                  {'id': 'e1', 'seq': 1, 'tick': 50, 'phase': 'N'}],
        'wiring': [{'id': 'wire-n', 'detector': 'loop', 'phase': 'N', 'time_ms': [0, 50]}],
        'heartbeats': [{'id': 'h1', 'detector': 'loop', 'ticks': [0, 30]},
                       {'id': 'h2', 'detector': 'loop', 'ticks': [10, 20]}],
        'arrivals': [], 'buckets': [{'id': 'b000', 'time_ms': [0, 50]}]}
    import tempfile
    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8') as fh:
        json.dump(overlap_hb, fh)
        path = fh.name
    try:
        probe = json.loads(subprocess.run(
            [sys.executable, '-m', 'greenbook', 'preview', path], cwd=ROOT,
            check=True, capture_output=True, text=True, encoding='utf-8').stdout)
    finally:
        Path(path).unlink()
    assert probe['buckets']['b000'][0]['observation_ms_by_detector'] == {'loop': 30}
    assert probe['coverage'][0]['time_ms'] == [0, 30] and probe['coverage'][1]['time_ms'] == [10, 20]
    assert (w['overlap_ms'], w['old_lower'], w['old_upper']) == (30, 30, 30)
    assert max(hi - lo for lo, hi in overlap(green(win, 'N', 0, 50), online(win, 'N', 0, 50)[('loop', 'wire-n')])) == 10
    print('\nall assertions passed')


if __name__ == '__main__':
    main()
    assertions()
