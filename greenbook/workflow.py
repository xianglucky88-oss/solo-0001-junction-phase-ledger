from .clock import Clocks
from .common import pieces, scalar, span, unique
from .observed import collect, inside, touch
from .service import reconstruct

DESCRIPTION = 'Reconstruct offline single-ring phase and arrival evidence.'


def duration(windows):
    end = None
    total = 0
    for lo, hi in sorted(windows):
        total += hi - (lo if end is None else max(lo, min(hi, end)))
        end = hi if end is None else max(hi, end)
    return total


def summarize(window, phases, coverage, arrivals):
    output = []
    for phase in phases:
        detectors = {}
        for c in coverage:
            if c['phase'] == phase and (section := pieces(window, c['time_ms'])):
                detectors.setdefault(c['detector'], []).append(section)
        considered = [a for a in arrivals if a.get('phase') == phase and touch(a['time_ms'], window)]
        bound = [a for a in considered if inside(a['time_ms'], window)]
        green = [a['id'] for a in bound if a['state'] == 'green']
        other = [a['id'] for a in bound if a['state'] == 'not_green']
        coverage_ms = {d: duration(windows) for d, windows in sorted(detectors.items())}
        output.append({'phase': phase, 'observation_ms_by_detector': coverage_ms,
                       'certain_green': green, 'certain_non_green': other,
                       'unresolved_or_boundary': [a['id'] for a in considered if a['id'] not in green + other],
                       'green_share_of_classified': scalar(f'{len(green)}/{len(green) + len(other)}') if green or other else None})
    return output


def attach_statistics(segments, phases, coverage, arrivals):
    for cycle in segments['normal_cycles']:
        cycle['statistics'] = summarize(cycle['certain'], phases, coverage, arrivals)
    for partial in segments['normal_partials']:
        partial['statistics'] = (summarize(partial['certain_ms'], phases, coverage, arrivals)
                                 if partial['certain_ms'] is not None else None)
    for preempt in segments['preemptions']:
        preempt['statistics'] = (summarize(preempt['certain_ms'], phases, coverage, arrivals)
                                 if preempt['certain_ms'] is not None else None)
    for unknown in segments['unclassified']:
        unknown['statistics'] = (summarize(unknown['certain_ms'], phases, coverage, arrivals)
                                 if unknown['certain_ms'] is not None else None)


def materialize(doc):
    clocks = Clocks(doc['clock_certificates'])
    phases = doc['phase_order']
    service, gaps, cycles, segments = reconstruct(doc['controller'], phases, doc['edges'],
                                                  clocks, doc.get('preemptions'))
    coverage, arrivals = collect(doc['wiring'], doc['heartbeats'], doc['arrivals'], phases, service, clocks)
    reports = {k: summarize(span(r['time_ms'], 'report bucket'), phases, coverage, arrivals)
               for k, r in sorted(unique(doc['buckets']).items())}
    for cycle in cycles:
        cycle['statistics'] = summarize(cycle['certain'], phases, coverage, arrivals)
    attach_statistics(segments, phases, coverage, arrivals)
    return {'services': service, 'unusable_edges': gaps, 'cycles': cycles, 'segments': segments,
            'coverage': coverage, 'arrivals': arrivals, 'buckets': reports}
