from .clock import Clocks
from .common import span, unique
from .observed import collect
from .report import duration, summarize
from .rings import is_dual, materialize_dual
from .service import reconstruct

DESCRIPTION = 'Reconstruct offline single-ring and dual-ring phase and arrival evidence.'

__all__ = ['DESCRIPTION', 'duration', 'materialize', 'materialize_dual', 'summarize']


def materialize(doc):
    clocks = Clocks(doc['clock_certificates'])
    if is_dual(doc):
        return materialize_dual(doc, clocks)
    phases = doc['phase_order']
    service, gaps, cycles = reconstruct(doc['controller'], phases, doc['edges'], clocks)
    coverage, arrivals = collect(doc['wiring'], doc['heartbeats'], doc['arrivals'], phases, service, clocks)
    reports = {k: summarize(span(r['time_ms'], 'report bucket'), phases, coverage, arrivals)
               for k, r in sorted(unique(doc['buckets']).items())}
    for cycle in cycles:
        cycle['statistics'] = summarize(cycle['certain'], phases, coverage, arrivals)
    return {'services': service, 'unusable_edges': gaps, 'cycles': cycles, 'coverage': coverage,
            'arrivals': arrivals, 'buckets': reports}
