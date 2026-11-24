"""Exact world-position nearest queries, no dependencies, Python 2.7."""
import math


def position(value):
    if len(value) != 3:
        raise ValueError('Position must contain three coordinates')
    result = tuple(float(v) for v in value)
    if any(math.isnan(v) or math.isinf(v) for v in result):
        raise ValueError('Position must be finite')
    return result


class NearestIndex(object):
    """Balanced KD tree; equal distances choose the lowest source vertex ID."""
    def __init__(self, samples):
        unique = {}
        for vertex, point, color in samples:
            point = position(point)
            sample = (vertex, point, tuple(color))
            previous = unique.get(point)
            if previous is None or vertex < previous[0]:
                unique[point] = sample
        if not unique:
            raise ValueError('Nearest index requires at least one source vertex')
        self.size = len(unique)
        self.root = self._build(list(unique.values()))

    def _build(self, samples):
        if not samples:
            return None
        axis = max(range(3), key=lambda i: max(s[1][i] for s in samples) - min(s[1][i] for s in samples))
        samples.sort(key=lambda s: (s[1][axis], s[0]))
        middle = len(samples) // 2
        return (samples[middle], axis, self._build(samples[:middle]), self._build(samples[middle + 1:]))

    def nearest(self, point):
        point = position(point)
        best, distance = None, float('inf')
        pending = [(self.root, 0.)]
        while pending:
            node, lower = pending.pop()
            if node is None or lower > distance:
                continue
            sample, axis, left, right = node
            squared = sum((point[i] - sample[1][i]) ** 2 for i in range(3))
            if best is None or squared < distance or (squared == distance and sample[0] < best[0]):
                best, distance = sample, squared
            delta = point[axis] - sample[1][axis]
            near, far = (left, right) if delta <= 0 else (right, left)
            far_lower = max(lower, delta * delta)
            if far is not None and far_lower <= distance:
                pending.append((far, far_lower))
            if near is not None:
                pending.append((near, lower))
        return best
