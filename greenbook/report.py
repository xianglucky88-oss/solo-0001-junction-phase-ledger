"""Bucket statistics shared by the single-ring and dual-ring pipelines."""

from .common import pieces, scalar
from .observed import inside, touch


def duration(windows):
    end = None
    total = 0
    for lo, hi in sorted(windows):
        total += hi - (lo if end is None else max(lo, min(hi, end)))
        end = hi if end is None else max(hi, end)
    return total


def summarize(window, phases, coverage, arrivals, ring_of_phase=None):
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
        entry = {'phase': phase, 'observation_ms_by_detector': coverage_ms,
                 'certain_green': green, 'certain_non_green': other,
                 'unresolved_or_boundary': [a['id'] for a in considered if a['id'] not in green + other],
                 'green_share_of_classified': scalar(f'{len(green)}/{len(green) + len(other)}') if green or other else None}
        if ring_of_phase is not None:
            entry['ring'] = ring_of_phase[phase]
        output.append(entry)
    return output
