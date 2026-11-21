from .common import disjoint, integer, require, span, unique


class Clocks:
    def __init__(self, rows):
        self.by_device = {}
        for key, row in unique(rows).items():
            lo, hi = span(row['ticks'], 'clock ticks')
            offset = integer(row['offset_ms'], 'clock offset')
            error = integer(row['error_ms'], 'clock error', 0)
            self.by_device.setdefault(row['device'], []).append((lo, hi, key, offset, error))
        for spans in self.by_device.values():
            disjoint(spans, 'clock certificates')

    def point(self, device, raw):
        integer(raw, 'event tick')
        for lo, hi, key, offset, error in self.by_device.get(device, ()):
            if lo <= raw < hi:
                return [raw + offset - error, raw + offset + error], key
        return None, None

    def interior(self, device, raw):
        start, end = span(raw, 'heartbeat ticks')
        found = []
        for lo, hi, key, offset, error in self.by_device.get(device, ()):
            a, b = max(start, lo) + offset + error, min(end, hi) + offset - error
            if a < b:
                found.append((a, b, key))
        return sorted(found)
