"""Preemption evidence for a single-ring controller.

A preemption is stated on the controller's own tick domain.  Entry and exit
carry device-time evidence through the same clock certificates as edges; an
unclocked boundary degrades only that segment's millisecond window, never the
structural classification.  ``resume_phase`` is optional evidence: when it is
absent the ordinary ring cannot be assumed to resume, even at the first phase.
"""
from .common import disjoint, integer, require, unique


def parse_preemptions(control, phases, raw, clocks):
    if raw is None:
        return []
    require(isinstance(raw, list), 'preemptions must be a list')
    rows = []
    for key, doc in unique(raw).items():
        entry = integer(doc['entry_tick'], 'preemption entry tick')
        exit_tick = integer(doc['exit_tick'], 'preemption exit tick')
        require(exit_tick > entry, 'preemption must end after it begins')
        resume = doc.get('resume_phase')
        require(resume is None or (isinstance(resume, str) and resume in phases),
                f'unknown preemption resume phase: {resume!r}')
        entry_at, entry_ref = clocks.point(control, entry)
        exit_at, exit_ref = clocks.point(control, exit_tick)
        if entry_at is not None and exit_at is not None:
            require(exit_at[1] > entry_at[0],
                    'preemption clock evidence contradicts tick order')
        rows.append({'id': key, 'entry_tick': entry, 'exit_tick': exit_tick,
                     'resume_phase': resume, 'entry_time_ms': entry_at,
                     'exit_time_ms': exit_at, 'clock_refs': [entry_ref, exit_ref]})
    disjoint([(row['entry_tick'], row['exit_tick']) for row in rows], 'preemption spans')
    return sorted(rows, key=lambda row: row['entry_tick'])
