from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ui.vertex_color_match import build_patch, MatchError, run_match_operation


MASK = [True, True, True]


def pair():
    source = {"points": [(0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0)],
              "faces": [[1, 2, 3, 4]], "uv": [[(0, 0), (1, 0), (1, 1), (0, 1)]],
              "colors": [(1, (.12345, .2, .3)), (2, (.8, .4, .7))], "object": "Source"}
    target = {"points": [(14, -3, 0), (10, -1, 0), (14, -1, 0), (10, -3, 0)],
              "faces": [[3, 2, 4, 1]], "uv": [[(1, 1), (0, 1), (0, 0), (1, 0)]],
              "selected": [4, 3], "token": "test target"}
    return source, target


class VertexColorMatchTests(unittest.TestCase):
    def test_reindexed_scaled_translated_mesh_pastes_only_the_selection_overlap(self):
        source, target = pair()
        target["uv"][0][2] = (.005, 0)
        self.assertEqual(build_patch(source, target, MASK), [[4, [.12345, .2, .3]]])

    def test_ambiguity_in_whether_to_write_is_rejected_even_if_colors_are_equal(self):
        source, target = pair()
        source["uv"] = target["uv"] = None
        source["colors"] = [(1, (.15, .3, .8)), (2, (.15, .3, .8))]
        with self.assertRaises(MatchError):
            build_patch(source, target, MASK)
        source["colors"] = [(i, (.15, .3, .8)) for i in range(1, 5)]
        self.assertEqual(dict(build_patch(source, target, MASK)), {3: [.15, .3, .8], 4: [.15, .3, .8]})

    def test_no_overlap_never_calls_max_apply(self):
        source, target = pair()
        target["selected"] = [2, 3]
        calls = []

        class Client:
            def call(self, action, **kwargs):
                calls.append(action)
                return {"ok": True, "data": target}

        result = run_match_operation(Client(), "paste", source, MASK)
        self.assertTrue(result["ok"])
        self.assertIn("warning", result["data"])
        self.assertEqual(calls, ["vertex_color_match_snapshot"])

    def test_alpha_uses_its_own_channel_and_ignores_rgb_selection(self):
        source, target = pair()
        source["channel"] = -2
        target["channel"] = -2
        calls = []

        class Client:
            def call(self, action, **kwargs):
                calls.append((action, kwargs))
                return {"ok": True, "data": target if action.endswith("snapshot") else {"vertices": 1}}

        client = Client()
        result = run_match_operation(client, "paste", source, [False] * 3, channel=-2)
        self.assertEqual(result["data"]["vertices"], 1)
        self.assertEqual(calls[0][1]["channel"], -2)
        self.assertEqual(calls[1][1]["mask"], MASK)
        self.assertEqual(calls[1][1]["patch"], [[4, [.12345, .2, .3]]])
        calls.clear()
        mismatch = run_match_operation(client, "paste", source, MASK, channel=0)
        self.assertIn("warning", mismatch["data"])
        self.assertEqual(calls, [])
