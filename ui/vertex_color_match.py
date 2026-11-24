"""Match whole Max poly topology in the hub, then filter to the color overlap."""
from collections import Counter, defaultdict
from functools import lru_cache

import numpy as np


class MatchError(ValueError):
    pass


def _cycle(face):
    i = face.index(min(face))
    cycle = face[i:] + face[:i]
    return min(cycle, cycle[:1] + cycle[:0:-1])


def _label_cycle(face):
    # Unlike geometry indices, labels can repeat.
    return min(row[i:] + row[:i] for row in (face, face[::-1]) for i in range(len(row)))


class _Mesh:
    def __init__(self, data):
        self.points = np.asarray(data["points"], dtype=float)
        self.faces = [tuple(v - 1 for v in face) for face in data["faces"]]
        self.incident = [[] for _ in self.points]
        self.edges = defaultdict(list)
        self.face_index = {}
        for i, face in enumerate(self.faces):
            if len(face) < 3 or len(set(face)) != len(face) or any(not 0 <= v < len(self.points) for v in face):
                raise MatchError("存在无效面，未粘贴")
            key = _cycle(face)
            if key in self.face_index:
                raise MatchError("存在重复面，未粘贴")
            self.face_index[key] = i
            for k, v in enumerate(face):
                self.incident[v].append((i, k))
                self.edges[tuple(sorted((v, face[k - 1])))].append(i)
        if any(not row for row in self.incident) or any(len(row) > 2 for row in self.edges.values()):
            raise MatchError("存在孤立顶点或非流形边，未粘贴")
        self.arity = [tuple(sorted(len(self.faces[i]) for i, _ in row)) for row in self.incident]
        uv = data.get("uv")
        profiles = [set() for _ in self.points]
        if uv is not None:
            for f, coords in zip(self.faces, uv, strict=True):
                for v, coord in zip(f, coords, strict=True):
                    profiles[v].add(tuple(round(c, 7) for c in coord))
        self.uv = [tuple(sorted(row)) for row in profiles] if uv is not None else None

        visited = set()
        vertex_comp = {}
        self.components = []
        self.face_component = {}
        for face in range(len(self.faces)):
            if face in visited:
                continue
            stack, component = [face], []
            visited.add(face)
            ci = len(self.components)
            while stack:
                i = stack.pop()
                component.append(i)
                self.face_component[i] = ci
                for k, v in enumerate(self.faces[i]):
                    if vertex_comp.setdefault(v, ci) != ci:
                        raise MatchError("存在仅通过单个顶点连接的网格片，未粘贴")
                    for other in self.edges[tuple(sorted((v, self.faces[i][k - 1])))]:
                        if other not in visited:
                            visited.add(other)
                            stack.append(other)
            self.components.append(component)


def _refine(a, b, use_uv):
    labels = [m.arity for m in (a, b)]
    if use_uv:
        keys = [list(zip(m.arity, m.uv)) for m in (a, b)]
        left, right = map(Counter, keys)
        # Ignore UV outliers on either side; retain their topology signatures.
        labels = [[key if left[key] == right[key] else (arity, None)
                   for key, arity in zip(row, m.arity)] for row, m in zip(keys, (a, b))]
    for _ in range(48):
        classes = {}
        updated = []
        for mesh, old in zip((a, b), labels):
            row = []
            for v, incident in enumerate(mesh.incident):
                around = []
                for i, k in incident:
                    f = mesh.faces[i]
                    seq = tuple(old[w] for w in f[k + 1:] + f[:k])
                    if isinstance(old[0], int):
                        around.append(min(seq, seq[::-1]))
                    else:
                        around.append(min(seq, seq[::-1], key=repr))
                key = old[v], tuple(sorted(around, key=None if isinstance(old[0], int) else repr))
                row.append(classes.setdefault(key, len(classes)))
            updated.append(row)
        if Counter(updated[0]) != Counter(updated[1]):
            raise MatchError("两个模型的面连接或 UV 对应不一致，未粘贴")
        stable = len(classes) == len(set(labels[0]))
        labels = updated
        if stable:
            break
    return labels


def _walk(a, b, labels, start, ordered):
    vertices, inverse, faces, target_faces = {}, {}, {}, set()
    queue = [(start, ordered)]
    while queue:
        i, target = queue.pop()
        j = b.face_index.get(_cycle(target))
        if j is None:
            return None
        f = a.faces[i]
        for v, w in zip(f, target):
            if (labels[0][v] != labels[1][w] or vertices.get(v, w) != w
                    or inverse.get(w, v) != v):
                return None
            vertices[v], inverse[w] = w, v
        if i in faces:
            if faces[i] != j:
                return None
            continue
        if j in target_faces:
            return None
        faces[i] = j
        target_faces.add(j)
        for k, v in enumerate(f):
            u = f[k - 1]
            left = a.edges[tuple(sorted((u, v)))]
            right = b.edges[tuple(sorted((vertices[u], vertices[v])))]
            if len(left) != len(right):
                return None
            if len(left) == 1:
                continue
            x = left[0] if left[1] == i else left[1]
            y = right[0] if right[1] == j else right[1]
            if x in faces:
                if faces[x] != y:
                    return None
                continue
            g, h = a.faces[x], b.faces[y]
            if len(g) != len(h):
                return None
            gu, gv = g.index(u), g.index(v)
            hu, hv = h.index(vertices[u]), h.index(vertices[v])
            step = 1 if (gv - gu) % len(g) == (hv - hu) % len(h) else -1
            queue.append((x, tuple(h[(hu + step * (r - gu)) % len(h)] for r in range(len(h)))))
    return vertices


def _options(a, b, labels):
    def seq(face, lab):
        return tuple(lab[v] for v in face)
    index = defaultdict(list)
    for j, face in enumerate(b.faces):
        index[_label_cycle(seq(face, labels[1]))].append(j)
    keys = [_label_cycle(seq(f, labels[0])) for f in a.faces]
    output = []
    # Bound genuinely repetitive cases so they fail safely rather than tie up the hub.
    work = 0
    for component in a.components:
        start = min(component, key=lambda i: len(index[keys[i]]))
        row, seen = [], set()
        for j in index[keys[start]]:
            ci = b.face_component[j]
            if len(b.components[ci]) != len(component):
                continue
            f = b.faces[j]
            wanted = seq(a.faces[start], labels[0])
            for g in (f, f[::-1]):
                for i in range(len(g)):
                    h = g[i:] + g[:i]
                    if seq(h, labels[1]) != wanted:
                        continue
                    work += len(component)
                    if work > max(150000, len(a.faces) * 30):
                        raise MatchError("重复网格的候选过多，无法可靠判断对应关系，未粘贴")
                    vertices = _walk(a, b, labels, start, h)
                    if vertices is None:
                        continue
                    key = tuple(sorted(vertices.items()))
                    if key not in seen:
                        seen.add(key)
                        row.append((ci, vertices))
        if not row:
            raise MatchError("找不到完整的面连接对应关系，未粘贴")
        output.append(row)
    return output


def _groups(rows):
    target_sources = defaultdict(set)
    for i, row in enumerate(rows):
        for j, _ in row:
            target_sources[j].add(i)
    seen = set()
    for start in range(len(rows)):
        if start in seen:
            continue
        stack, sources, targets = [start], [], set()
        seen.add(start)
        while stack:
            i = stack.pop()
            sources.append(i)
            for j, _ in rows[i]:
                if j not in targets:
                    targets.add(j)
                    for other in sorted(target_sources[j]):
                        if other not in seen:
                            seen.add(other)
                            stack.append(other)
        if len(sources) != len(targets):
            raise MatchError("网格片无法一一对应，未粘贴")
        yield sorted(sources, key=lambda i: (len(rows[i]), i)), sorted(targets)


def _assign(rows, target_indices, costs=None, different_from=None):
    """Minimum cost bijection, optionally requiring a different selected RGB result."""
    bit = {j: 1 << i for i, j in enumerate(target_indices)}

    @lru_cache(None)
    def visit(i, used, different):
        if i == len(rows):
            return (0., ()) if different or different_from is None else (np.inf, ())
        best = (np.inf, ())
        for k, (j, _, output) in enumerate(rows[i]):
            flag = bit[j]
            if used & flag:
                continue
            changed = different or (different_from is not None and output != different_from[j])
            rest, path = visit(i + 1, used | flag, changed)
            score = rest + (0. if costs is None else costs[i][k])
            if score < best[0]:
                best = score, (k,) + path
                if costs is None:
                    break
        return best

    return visit(0, 0, False)


def _local_predictor(a, b, anchors, size):
    if len(anchors) < 8:
        raise MatchError("可用于区分重复网格的确定对应点不足，未粘贴")
    source, target = map(list, zip(*anchors.items()))
    x, y = a.points[source], b.points[target]
    spread = np.linalg.norm(np.ptp(y, axis=0))
    count = min(size, len(x))
    if not spread:
        raise MatchError("可用于区分重复网格的确定对应点不足，未粘贴")

    @lru_cache(None)
    def predict(v):
        delta = x - a.points[v]
        sq = np.einsum("ij,ij->i", delta, delta)
        take = np.argpartition(sq, count - 1)[:count]
        scale = np.sqrt(sq[take].max())
        if not scale:
            raise MatchError("无法根据附近顶点对齐重复网格，未粘贴")
        w = np.exp(-sq[take] / (scale * scale))
        design = np.column_stack((delta[take] / scale, np.ones(count)))
        coeff, _, rank, _ = np.linalg.lstsq(design * w[:, None], y[take] * w[:, None], rcond=1e-8)
        # Rank 3 is sufficient for planar geometry only if the query lies in
        # the anchor plane; otherwise its prediction would be unconstrained.
        origin = np.array([0., 0., 0., 1.])
        as_combo = np.linalg.lstsq(design.T, origin, rcond=1e-8)[0]
        if rank < 3 or np.linalg.norm(design.T @ as_combo - origin) > 1e-5:
            raise MatchError("附近的确定对应点不足以区分重复网格，未粘贴")
        return coeff[3]
    return predict, spread


def _choose(a, b, candidates, colors, selected, mask):
    groups = list(_groups(candidates))
    anchors = {}
    for source, _ in groups:
        for i in source:
            if len(candidates[i]) == 1:
                anchors.update(candidates[i][0][1])
    predictors = None
    mapping = {}
    for source, target in groups:
        choices, outputs = [], defaultdict(set)
        for i in source:
            row = []
            for j, vm in candidates[i]:
                out = tuple(sorted((w, tuple(c for c, enabled in zip(colors[v], mask) if enabled))
                                   for v, w in vm.items() if v in colors and w in selected))
                outputs[j].add(out)
                row.append((j, vm, out))
            choices.append(row)
        if len(target) > 12:
            raise MatchError("重复网格片过多，无法可靠匹配，未粘贴")
        _, winner = _assign(choices, target)
        if not winner:
            raise MatchError("网格片无法一一对应，未粘贴")
        if any(len(o) > 1 for o in outputs.values()):
            signature = None
            if predictors is None:
                predictors = [_local_predictor(a, b, anchors, n) for n in (24, 64)]
            for predict, spread in predictors:
                costs = [[float(np.mean([np.sum((predict(v) - b.points[w]) ** 2)
                                         for v, w in vm.items()])) for _, vm, _ in row]
                         for row in choices]
                best, winner = _assign(choices, target, costs)
                picked = {row[k][0]: row[k][2] for row, k in zip(choices, winner)}
                alternative, next_path = _assign(choices, target, costs, picked)
                if next_path:
                    # Large residuals on components both paths agree on do
                    # not make the swapped components harder to distinguish.
                    shared = sum(row[k] for row, k, alt in zip(costs, winner, next_path) if k == alt)
                    best, alternative = max(0., best - shared), max(0., alternative - shared)
                if (signature is not None and signature != picked or
                        np.isfinite(alternative) and alternative <= 4 * best + len(target) * (spread * 1e-5) ** 2):
                    raise MatchError("存在多套会贴出不同颜色的对应关系，未粘贴")
                signature = picked
        for row, k in zip(choices, winner):
            mapping.update(row[k][1])
    return mapping


def build_patch(source, target, mask):
    """Validate a bijection over all faces before returning selected channel writes."""
    if len(source["points"]) != len(target["points"]) or len(source["faces"]) != len(target["faces"]):
        raise MatchError("两个模型的顶点数或面数不同，未粘贴")
    a, b = _Mesh(source), _Mesh(target)
    if not a.faces or Counter(map(len, a.faces)) != Counter(map(len, b.faces)):
        raise MatchError("两个模型的面结构不同，未粘贴")
    colors = {int(v) - 1: tuple(c) for v, c in source["colors"]}
    selected = {v - 1 for v in target["selected"]}
    use_uv = a.uv is not None and b.uv is not None
    if use_uv:
        ca, cb = [Counter(zip(m.arity, m.uv)) for m in (a, b)]
        use_uv = sum((ca & cb).values()) >= min(0.8 * len(a.points), len(a.points) - 1)
    if use_uv:
        try:
            candidates = _options(a, b, _refine(a, b, True))
            list(_groups(candidates))
        except MatchError:
            use_uv = False
    if not use_uv:
        candidates = _options(a, b, _refine(a, b, False))
    mapping = _choose(a, b, candidates, colors, selected, mask)
    if (len(mapping) != len(a.points) or len(set(mapping.values())) != len(b.points)
            or {_cycle(tuple(mapping[v] for v in f)) for f in a.faces} != b.face_index.keys()):
        raise MatchError("无法核对全部面的连接，未粘贴")
    return [[w + 1, list(colors[v])] for v, w in mapping.items() if v in colors and w in selected]


def run_match_operation(client, operation, clipboard=None, mask=None, channel=0):
    """Run off the UI thread; keep the last successful topology copy in the card."""
    try:
        def call(action, **params):
            return client.call(action, timeout=90, connect_timeout=1, execution_timeout=85, **params)

        if operation == "paste":
            if not clipboard or clipboard.get("channel", 0) != channel:
                raise MatchError("请先复制当前通道的拓扑选区")
            if channel == -2:
                mask = [True, True, True]
        response = call("vertex_color_match_snapshot", source=operation == "copy", channel=channel)
        data = response.get("data") or {}
        if not response.get("ok") or data.get("warning"):
            return response
        if operation == "copy":
            return response
        patch = build_patch(clipboard, data, mask)
        if not patch:
            return {"ok": True, "data": {"warning": f"目标选区没有对应的已复制颜色，跳过 {len(data['selected'])} 个顶点"}}
        response = call("vertex_color_match_apply", token=data["token"], patch=patch, mask=mask)
        if response.get("ok") and response.get("data"):
            response["data"]["source"] = clipboard["object"]
        return response
    except MatchError as exc:
        return {"ok": True, "data": {"warning": str(exc)}}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
