"""Independent interpolation references, exact-copy bit preservation, geometry matching keys."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
from ui import skin_weight_mapping as mapping
from maya_actions.skin_weight_geometry import position_key, unique_positions


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.prefix = self.directory / "mesh-0"
        self.weights = np.array([[.123456789123, .876543210877, 0., 0.],
                                 [0., .25, .75, 0.], [0., 0., .2, .8]], dtype="<f8")
        self.dq = np.array([.123456789123, .7, 1.], dtype="<f8")
        mask = self.weights > 0
        np.r_[0, np.cumsum(mask.sum(axis=1))].astype("<u4").tofile(self.prefix.with_suffix(".offsets"))
        np.nonzero(mask)[1].astype("<u4").tofile(self.prefix.with_suffix(".cols"))
        self.weights[mask].tofile(self.prefix.with_suffix(".values"))
        self.dq.tofile(self.prefix.with_suffix(".dq"))
        self.entry = dict(index=0, mesh="shape", name="body", vertices=3, target_vertices=5,
                          influences=4, nonzero=6, bones=[dict(index=i, name=f"j{i}") for i in range(4)])
        self.ids = np.array([[2,2,2],[0,0,0],[0,1,2],[2,1,0],[1,1,1]], dtype="<u4")
        self.factors = np.array([[1,0,0],[1,0,0],[.2,.3,.5],[.25,.75,0],[1,0,0]], dtype="<f8")
        self.ids.tofile(self.prefix.with_suffix(".match_ids"))
        self.factors.tofile(self.prefix.with_suffix(".match_factors"))
        self.report = dict(direct_vertices=3, interpolated_vertices=2)

    def output(self, entry):
        offsets = np.fromfile(self.prefix.with_suffix(".offsets"), "<u4")
        columns = np.fromfile(self.prefix.with_suffix(".cols"), "<u4")
        values = np.fromfile(self.prefix.with_suffix(".values"), "<f8")
        result = np.zeros((entry["vertices"], entry["influences"]), dtype="<f8")
        for row in range(len(result)):
            for pos in range(offsets[row], offsets[row+1]): result[row, columns[pos]] = values[pos]
        return result, np.fromfile(self.prefix.with_suffix(".dq"), "<f8")

    def test_exact_rows_keep_bits_and_other_rows_use_reference_interpolation_across_blocks(self):
        with patch.object(mapping, "BLOCK_VALUES", 8):
            entry = mapping.interpolate_weights(self.directory, self.entry, self.report)
        weights, dq = self.output(entry)
        expected = np.array([sum((self.weights[index] * factor for index,factor in zip(ids, factors)),
                                 np.zeros(4)) for ids,factors in zip(self.ids,self.factors)])
        expected_dq = np.array([sum(self.dq[index]*factor for index,factor in zip(ids,factors))
                               for ids,factors in zip(self.ids,self.factors)])
        np.testing.assert_allclose(weights, expected, rtol=0, atol=1e-15)
        np.testing.assert_allclose(dq, expected_dq, rtol=0, atol=1e-15)
        for target, source in ((0,2),(1,0),(4,1)):
            self.assertEqual(weights[target].tobytes(), self.weights[source].tobytes())
            self.assertEqual(dq[target].tobytes(), self.dq[source].tobytes())
        np.testing.assert_allclose(weights.sum(axis=1), 1., rtol=0, atol=1e-15)
        self.assertEqual(entry["vertices"], 5)
        self.assertEqual(entry["direct_vertices"], 3)
        self.assertFalse(list(self.directory.glob("*.mapped_*")))

    def test_unused_output_bones_are_removed(self):
        np.zeros((2,3),dtype="<u4").tofile(self.prefix.with_suffix(".match_ids"))
        np.array([[1,0,0]]*2,dtype="<f8").tofile(self.prefix.with_suffix(".match_factors"))
        entry = mapping.interpolate_weights(self.directory, dict(self.entry,target_vertices=2),
                                             dict(direct_vertices=2,interpolated_vertices=0))
        self.assertEqual([bone["index"] for bone in entry["bones"]], [0,1])
        weights,dq = self.output(entry)
        np.testing.assert_array_equal(weights, self.weights[[0,0]])

    def test_invalid_mapping_never_replaces_source_weights(self):
        original = self.prefix.with_suffix(".values").read_bytes()
        for kind in ("index", "negative", "nonfinite", "sum", "short"):
            ids,factors = self.ids.copy(),self.factors.copy()
            if kind=="index":ids[0,0]=3
            elif kind=="negative":factors[0,0]=-1
            elif kind=="nonfinite":factors[0,0]=np.nan
            elif kind=="sum":factors[0,0]=.5
            ids.tofile(self.prefix.with_suffix(".match_ids"))
            factors.tofile(self.prefix.with_suffix(".match_factors"))
            if kind=="short":self.prefix.with_suffix(".match_ids").write_bytes(b"bad")
            with self.assertRaises(ValueError):
                mapping.interpolate_weights(self.directory,self.entry,self.report)
            self.assertEqual(self.prefix.with_suffix(".values").read_bytes(),original)

    def test_position_keys_have_developer_precision_and_ambiguous_keys_fall_through(self):
        self.assertEqual(position_key((1.0004,-0.,2.1234),3),(1.,0.,2.123))
        self.assertNotEqual(position_key((1.0006,0.,2.),3),position_key((1.,0.,2.),3))
        positions=[(0.,0.,0.),(1.,0.,0.),(0.,0.,0.)]
        lookup=unique_positions(positions,3)
        self.assertIsNone(lookup[(0.,0.,0.)])
        self.assertEqual(lookup[(1.,0.,0.)],1)


if __name__=="__main__":
    unittest.main()
