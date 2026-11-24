# -*- coding: utf-8 -*-
"""Read-only cross-sections for triangle/quad strips and tubes, Python 2.7."""
from __future__ import unicode_literals
from collections import Counter, defaultdict, deque


def face_edges(face):
    return [tuple(sorted((face[i], face[(i + 1) % len(face)]))) for i in range(len(face))]


def vertices(edges):
    return set(v for edge in edges for v in edge)


def graph_of(faces):
    graph = defaultdict(set)
    for face in faces:
        for a, b in face_edges(face):
            graph[a].add(b)
            graph[b].add(a)
    return graph


def depths(graph, roots):
    result = {v: 0 for v in roots}
    queue = deque(roots)
    while queue:
        vertex = queue.popleft()
        for neighbor in graph[vertex]:
            if neighbor not in result:
                result[neighbor] = result[vertex] + 1
                queue.append(neighbor)
    return result


def check_chain(edges):
    """Validate one open chain or closed ring; return True for a ring."""
    if not edges or len(edges) != len(set(edges)) or any(a == b for a, b in edges):
        raise ValueError('横线为空或包含重复边，请重新选择完整横线')
    graph = graph_of(edges)
    ends = [v for v in graph if len(graph[v]) == 1]
    closed = not ends and len(graph) >= 3
    if (len(ends) != 2 and not closed) or any(len(n) > 2 for n in graph.values()):
        raise ValueError('请选择一条连续、无分叉的完整横线或闭合边环')
    if len(depths(graph, [next(iter(graph))])) != len(graph):
        raise ValueError('横线包含不相连的部分，请重新选择')
    return closed


def complete_row(faces, selected):
    """Keep explicit chains; extend a single edge through unambiguous quads."""
    check_chain(selected)
    if len(selected) != 1:
        return list(selected)
    seed = selected[0]
    edge_faces = defaultdict(list)
    for index, face in enumerate(faces):
        for edge in set(face_edges(face)):
            edge_faces[edge].append(index)
    if seed not in edge_faces:
        raise ValueError('横线包含失效边，请重新选择')
    # Ignore other elements even if they share a geometric vertex.
    element, pending = set(), list(edge_faces[seed])
    while pending:
        index = pending.pop()
        if index in element:
            continue
        element.add(index)
        for edge in face_edges(faces[index]):
            pending.extend(i for i in edge_faces[edge] if i not in element)
    incident, vertex_faces = defaultdict(set), defaultdict(set)
    for index in element:
        for edge in face_edges(faces[index]):
            for v in edge:
                incident[v].add(edge)
                vertex_faces[v].add(index)
    boundary = vertices([e for e, ids in edge_faces.items() if len(ids) == 1 and ids[0] in element])
    path = list(seed)
    edges = set([seed])
    message = '单条边无法唯一确定横排方向，请选择完整横线或闭合边环后记录'
    for left in (True, False):
        while True:
            v, previous = (path[0], path[1]) if left else (path[-1], path[-2])
            incoming = tuple(sorted((v, previous)))
            if any(len(faces[i]) != 4 or len(set(faces[i])) != 4 for i in vertex_faces[v]):
                raise ValueError(message)
            if any(len(edge_faces[e]) > 2 for e in incident[v]):
                raise ValueError(message)
            adjacent = set(e for i in edge_faces[incoming] for e in face_edges(faces[i]) if v in e)
            options = incident[v] - adjacent
            if not options and v in boundary:
                break
            if len(options) != 1:
                raise ValueError(message)
            edge = next(iter(options))
            other = edge[0] if edge[1] == v else edge[1]
            if other == (path[-1] if left else path[0]):
                edges.add(edge)
                check_chain(list(edges))
                return sorted(edges)
            if other in path:
                raise ValueError(message)
            edges.add(edge)
            if left:
                path.insert(0, other)
            else:
                path.append(other)
    check_chain(list(edges))
    return sorted(edges)


def complete_row_from_reference(faces, seed, reference):
    """Resolve a seed's contour from the other recorded row, then validate it."""
    check_chain(reference)
    reference = set(reference)
    if seed in reference:
        raise ValueError('当前边位于另一端横线上，请选择不同的起止位置')
    edge_faces = defaultdict(list)
    for index, face in enumerate(faces):
        for edge in set(face_edges(face)):
            edge_faces[edge].append(index)
    if seed not in edge_faces or any(e not in edge_faces for e in reference):
        raise ValueError('参考横线已失效，请重新记录')
    dual = defaultdict(set)
    for edge, adjacent in edge_faces.items():
        if edge not in reference:
            for a in adjacent:
                dual[a].update(b for b in adjacent if b != a)
    candidates, seen = set(), set()
    for root in edge_faces[seed]:
        if root in seen:
            continue
        component = set(depths(dual, [root]))
        seen.update(component)
        graph = graph_of([faces[i] for i in component])
        if not vertices(reference) <= set(graph):
            continue
        levels = depths(graph, vertices(reference))
        if levels[seed[0]] != levels[seed[1]] or levels[seed[0]] == 0:
            continue
        level = levels[seed[0]]
        row = set(edge for i in component for edge in face_edges(faces[i])
                  if levels[edge[0]] == level and levels[edge[1]] == level)
        try:
            row_weights(faces, list(reference), list(row))
        except ValueError:
            continue
        candidates.add(tuple(sorted(row)))
    if len(candidates) != 1:
        raise ValueError('结合另一端仍无法唯一识别横排，请选择完整横线或闭合边环后记录')
    return list(next(iter(candidates)))


def row_weights(faces, start, finish):
    """Return vertex fractions and face indices; coordinates/UVs are not inputs."""
    closed = check_chain(start)
    mixed = check_chain(finish) != closed
    reverse = mixed and not closed
    if reverse:
        start, finish = finish, start
        closed = True
    start, finish = set(start), set(finish)
    sv, tv = vertices(start), vertices(finish)
    if sv & tv:
        raise ValueError('起始横线和结束横线相同或重叠，请重新记录')
    edge_faces = defaultdict(list)
    for index, face in enumerate(faces):
        # Retain all connectivity first, then validate the selected element.
        for edge in set(face_edges(face)):
            edge_faces[edge].append(index)
    if any(edge not in edge_faces for edge in start | finish):
        raise ValueError('横线已失效，请重新记录起始和结束横线')
    def face_graph(blocked):
        graph = defaultdict(set)
        for edge, adjacent in edge_faces.items():
            if edge not in blocked and len(adjacent) >= 2:
                # Keep all incidences so non-manifold edges remain detectable.
                for other in adjacent[1:]:
                    graph[adjacent[0]].add(other)
                    graph[other].add(adjacent[0])
        return graph
    element = set(depths(face_graph(set()), [edge_faces[next(iter(start))][0]]))
    if any(not any(index in element for index in edge_faces[edge]) for edge in start | finish):
        raise ValueError('起始和结束横线必须位于同一个元素（连通片），请重新记录')
    for index in element:
        face = faces[index]
        if len(face) not in (3, 4):
            raise ValueError('所选横线所在元素包含 %s 边面，当前仅支持三角面和四边面' % len(face))
        if len(face) != len(set(face)):
            raise ValueError('所选横线所在元素存在顶点重复的退化面，无法可靠识别横排')
    if any(len(adjacent) > 2 and adjacent[0] in element for adjacent in edge_faces.values()):
        raise ValueError('所选横线所在元素包含非流形边，无法可靠识别横排')
    cut = start | finish
    dual = face_graph(cut)
    unseen, candidates = set(element), []
    while unseen:
        root = unseen.pop()
        component, todo = set([root]), [root]
        while todo:
            for other in dual[todo.pop()]:
                if other in unseen:
                    unseen.remove(other)
                    component.add(other)
                    todo.append(other)
        keys = set(edge for index in component for edge in face_edges(faces[index]))
        if start <= keys and finish <= keys:
            candidates.append(component)
    if len(candidates) != 1:
        raise ValueError('两条横线之间没有唯一的连续区域，请检查是否同一面片或存在多条路线')
    region = candidates[0]
    counts = Counter(edge for index in region for edge in face_edges(faces[index]))
    if any(count > 2 for count in counts.values()):
        raise ValueError('两条横线之间包含非流形边，无法可靠识别横排')
    if any(counts[edge] != 1 for edge in cut):
        raise ValueError('横线未横跨面片完整宽度，请重新记录完整横线')
    boundary = set(edge for edge in counts if counts[edge] == 1)
    side_edges = boundary - cut
    if closed and not mixed and boundary != cut:
        raise ValueError('两条闭合边环之间存在缺面、额外边界或不完整连接，无法识别横环')
    if (not closed or mixed) and any(len(edge_faces[edge]) != 1 for edge in side_edges):
        raise ValueError('区域侧边界不完整，无法可靠识别横排')
    graph = graph_of([faces[index] for index in sorted(region)])
    # An open strip is a disk (Euler 1), a tube interval is an annulus (0).
    if len(graph) - len(counts) + len(region) != (0 if closed else 1):
        raise ValueError('两条横线之间包含缺面或内部孔洞，无法识别横排')
    border_graph = graph_of(list(boundary))
    if (any(len(n) != 2 for n in border_graph.values()) or
            (not closed and len(depths(border_graph, [next(iter(border_graph))])) != len(border_graph))):
        raise ValueError('区域边界存在分叉或断裂，无法识别横排')
    if mixed:
        start_border = set(depths(border_graph, [next(iter(sv))]))
        other_border = set(border_graph) - start_border
        if (start_border != sv or not tv <= other_border or not other_border or
                set(depths(border_graph, [next(iter(other_border))])) != other_border):
            raise ValueError('开放横线与闭环之间的边界不完整，无法可靠识别横排')
        if check_chain(list(side_edges)):
            raise ValueError('开放横线的两端没有连接至同一连续边界')
        ends = set(v for v, ns in graph_of(list(side_edges)).items() if len(ns) == 1)
        if ends != set(v for v, ns in graph_of(list(finish)).items() if len(ns) == 1):
            raise ValueError('开放横线未完整横跨边界，请重新选择')
    side_graph = graph_of(list(side_edges))
    sides, unseen = [], set() if mixed else set(side_graph)
    while unseen:
        component = set(depths(side_graph, [unseen.pop()]))
        unseen.difference_update(component)
        check_chain([edge for edge in side_edges if edge[0] in component])
        if len(component & sv) != 1 or len(component & tv) != 1:
            raise ValueError('侧边界没有正确连接首尾横线，请检查横线选区')
        sides.append(component)
    if not closed and len(sides) != 2:
        raise ValueError('目标面片不是具有两条侧边界的连续条带')
    ds, dt = depths(graph, sv), depths(graph, tv)
    distance = min(ds[v] for v in tv)
    if (distance <= 0 or any(ds[v] != distance for v in tv) or
            (not mixed and any(dt[v] != distance for v in sv))):
        raise ValueError('首尾横排层级不一致，请检查横排是否完整或已被破坏')
    if (mixed and max(ds.values()) != distance) or (not mixed and any(ds[v] + dt[v] != distance for v in graph)):
        raise ValueError('存在缺口、额外插点或不规则连接，无法从两端确认一致的横排')
    rows = [set() for unused in range(distance + 1)]
    layer_edges = [[] for unused in range(distance + 1)]
    for v in graph:
        rows[ds[v]].add(v)
    for edge in counts:
        if ds[edge[0]] == ds[edge[1]]:
            layer_edges[ds[edge[0]]].append(edge)
    opened = False
    for level, row in enumerate(rows):
        row_closed = check_chain(layer_edges[level])
        if ((not mixed and row_closed != closed) or (mixed and opened and row_closed) or
                vertices(layer_edges[level]) != row or
                any(len(side & row) != 1 for side in sides)):
            raise ValueError('中间横排不连续或存在分叉，未执行渐变')
        opened = opened or not row_closed
    if set(layer_edges[0]) != start or set(layer_edges[-1]) != finish:
        raise ValueError('首尾横线不完整，请选择完整横线或闭合边环')
    for index in region:
        face = faces[index]
        levels = [ds[v] for v in face]
        kinds = set(levels)
        if (len(kinds) != 2 or max(kinds) - min(kinds) != 1 or
                (len(face) == 4 and any(levels.count(k) != 2 for k in kinds))):
            raise ValueError('面连接没有沿相邻横排排列，未执行渐变')
    return dict(rows=len(rows), faces=region,
                values={v: (distance - ds[v] if reverse else ds[v]) / float(distance) for v in graph})
