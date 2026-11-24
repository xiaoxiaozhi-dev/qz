"""NumPy 根据原生 Maya 对应关系生成 CSR；重合点直接复制原始数值。"""
from pathlib import Path
import os

import numpy as np

from ui.skin_weights import BLOCK_VALUES


def _row_entries(offsets, rows, destination):
    counts = offsets[rows + 1].astype(np.int64) - offsets[rows].astype(np.int64)
    targets = np.repeat(destination, counts)
    first = np.repeat(offsets[rows].astype(np.int64), counts)
    group_starts = np.repeat(np.cumsum(counts) - counts, counts)
    positions = first + np.arange(len(targets)) - group_starts
    return targets, positions


def interpolate_weights(directory, entry, report):
    directory = Path(directory)
    prefix = directory / f'mesh-{entry["index"]}'
    source_count, target_count, width = entry["vertices"], entry["target_vertices"], entry["influences"]
    for suffix, length in (("match_ids", target_count * 3 * 4), ("match_factors", target_count * 3 * 8)):
        if prefix.with_suffix("." + suffix).stat().st_size != length:
            raise ValueError("最近点对应关系文件长度不匹配")
    ids = np.fromfile(prefix.with_suffix(".match_ids"), dtype="<u4").reshape(target_count, 3)
    factors = np.fromfile(prefix.with_suffix(".match_factors"), dtype="<f8").reshape(target_count, 3)
    if (ids >= source_count).any() or not (np.isfinite(factors) & (factors >= 0)).all():
        raise ValueError("最近点编号或插值比例无效")
    if not np.allclose(factors.sum(axis=1), 1., rtol=0, atol=1e-12):
        raise ValueError("最近点插值比例总和不为 1")
    offsets = np.fromfile(prefix.with_suffix(".offsets"), dtype="<u4")
    columns = np.fromfile(prefix.with_suffix(".cols"), dtype="<u4")
    values = np.fromfile(prefix.with_suffix(".values"), dtype="<f8")
    source_dq = np.fromfile(prefix.with_suffix(".dq"), dtype="<f8")
    batch_size = max(1, min(2048, BLOCK_VALUES // width))
    used, total = np.zeros(width, dtype=bool), 0
    suffixes = ("offsets", "cols", "values", "dq")
    outputs = {suffix: prefix.with_suffix(".mapped_" + suffix) for suffix in suffixes}
    try:
        with outputs["offsets"].open("wb") as offsets_file, outputs["cols"].open("wb") as cols_file, \
                outputs["values"].open("wb") as values_file, outputs["dq"].open("wb") as dq_file:
            np.array([0], dtype="<u4").tofile(offsets_file)
            for start in range(0, target_count, batch_size):
                block_ids = ids[start:start + batch_size]
                block_factors = factors[start:start + batch_size]
                dense = np.zeros((len(block_ids), width), dtype="<f8")
                dq = np.zeros(len(block_ids), dtype="<f8")
                direct = (block_factors[:, 0] == 1.) & (block_factors[:, 1:] == 0.).all(axis=1)
                direct_rows = np.flatnonzero(direct)
                if len(direct_rows):
                    source_rows = block_ids[direct_rows, 0]
                    rows, positions = _row_entries(offsets, source_rows, direct_rows)
                    dense[rows, columns[positions]] = values[positions]
                    dq[direct_rows] = source_dq[source_rows]
                interpolated_rows = np.flatnonzero(~direct)
                for corner in range(3):
                    rows_to_fill = interpolated_rows[block_factors[interpolated_rows, corner] > 0]
                    if not len(rows_to_fill):
                        continue
                    source_rows = block_ids[rows_to_fill, corner]
                    rows, positions = _row_entries(offsets, source_rows, rows_to_fill)
                    np.add.at(dense, (rows, columns[positions]), values[positions] * block_factors[rows, corner])
                    dq[rows_to_fill] += source_dq[source_rows] * block_factors[rows_to_fill, corner]
                if len(interpolated_rows):
                    dq[interpolated_rows] = np.clip(dq[interpolated_rows], 0., 1.)
                mask = dense > 0
                if not np.isfinite(dense).all() or (dense < 0).any() or not mask.any(axis=1).all():
                    raise ValueError("插值产生全零、负值或非有限权重")
                used |= mask.any(axis=0)
                out_offsets = np.cumsum(mask.sum(axis=1), dtype=np.uint64) + total
                total = int(out_offsets[-1])
                if total > 4294967295:
                    raise ValueError("插值权重数量超出文件格式上限")
                out_offsets.astype("<u4").tofile(offsets_file)
                np.nonzero(mask)[1].astype("<u4").tofile(cols_file)
                dense[mask].tofile(values_file)
                dq.tofile(dq_file)
        for suffix, path in outputs.items():
            os.replace(path, prefix.with_suffix("." + suffix))
    finally:
        for path in outputs.values():
            if path.exists():
                path.unlink()
    return dict(entry, vertices=target_count, nonzero=total,
                bones=[bone for bone in entry["bones"] if used[bone["index"]]],
                direct_vertices=report["direct_vertices"], interpolated_vertices=report["interpolated_vertices"],
                transfer_mode="closest_point")
