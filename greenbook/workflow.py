from .clock import Clocks
from .common import pieces, scalar, span, unique
from .observed import collect, inside, touch
from .rotation import analyze_rotation
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


def materialize(doc):
    clocks = Clocks(doc['clock_certificates'])
    phases = doc['phase_order']
    service, gaps, cycles = reconstruct(doc['controller'], phases, doc['edges'], clocks)
    rotation_entries, reconstructed = analyze_rotation(doc, gaps, clocks, phases)
    service_with_reconstruction = service + reconstructed
    coverage, arrivals = collect(doc['wiring'], doc['heartbeats'], doc['arrivals'],
                                 phases, service_with_reconstruction, clocks)
    reports = {k: summarize(span(r['time_ms'], 'report bucket'), phases, coverage, arrivals)
               for k, r in sorted(unique(doc['buckets']).items())}
    for cycle in cycles:
        cycle['statistics'] = summarize(cycle['certain'], phases, coverage, arrivals)
    result = {'services': service_with_reconstruction, 'unusable_edges': gaps, 'cycles': cycles,
              'coverage': coverage, 'arrivals': arrivals, 'buckets': reports}
    if rotation_entries is not None:
        result['rotation_gaps'] = rotation_entries
    return result
