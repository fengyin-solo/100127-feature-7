"""里程口径计算的单元测试：解析、参考值、超限提示与同编号不同途经的区分。"""
from __future__ import annotations

import unittest

from app.services import route_profile
from app.services.route_profile import compute_reference, parse_waypoints


class ParseWaypointsTest(unittest.TestCase):
    def test_missing_waypoints(self):
        nodes, error = parse_waypoints("")
        self.assertIsNone(nodes)
        self.assertIn("途经节点缺失", error)

    def test_blank_waypoints(self):
        nodes, error = parse_waypoints("   ")
        self.assertIsNone(nodes)
        self.assertIn("途经节点缺失", error)

    def test_empty_segment(self):
        nodes, error = parse_waypoints("南京>>济南")
        self.assertIsNone(nodes)
        self.assertIn("格式不正确", error)

    def test_illegal_characters(self):
        nodes, error = parse_waypoints("南京,济南")
        self.assertIsNone(nodes)
        self.assertIn("格式不正确", error)

    def test_valid_waypoints(self):
        nodes, error = parse_waypoints("南京>徐州>济南")
        self.assertEqual(nodes, ["南京", "徐州", "济南"])
        self.assertEqual(error, "")


class ComputeReferenceTest(unittest.TestCase):
    def compute(self, **overrides):
        params = {
            "route_no": "ROUT-0001",
            "origin": "上海",
            "destination": "北京",
            "waypoints_raw": "南京>徐州>济南",
            "vehicle_class": "大型冷藏车",
        }
        params.update(overrides)
        return compute_reference(**params)

    def test_reference_values(self):
        result = self.compute()
        self.assertTrue(result.ok)
        self.assertEqual(result.distance_km, 1400)  # 300+350+320+430
        self.assertEqual(result.duration_hours, 22.5)  # 1400/65 + 3*20分钟
        self.assertEqual(result.toll_yuan, 1330.0)  # 1400 * 0.95
        self.assertFalse(result.over_limit)

    def test_basis_snapshot_keeps_version_and_key(self):
        result = self.compute()
        self.assertEqual(result.basis["口径版本"], route_profile.PROFILE_VERSION)
        self.assertEqual(result.basis["口径键"], "ROUT-0001|上海>南京>徐州>济南>北京")
        self.assertEqual(len(result.basis["区段明细"]), 4)
        self.assertEqual(result.basis["车型类别"], "大型冷藏车")

    def test_same_route_no_distinguishes_waypoints(self):
        north = self.compute(waypoints_raw="南京>徐州>济南")
        south = self.compute(waypoints_raw="杭州>合肥>郑州", vehicle_class="中型冷藏车")
        self.assertTrue(south.ok)
        self.assertNotEqual(north.basis["口径键"], south.basis["口径键"])
        self.assertEqual(south.distance_km, 1750)  # 180+320+560+690
        self.assertEqual(south.toll_yuan, 1225.0)  # 1750 * 0.70

    def test_over_limit_warns_with_detour_reason(self):
        result = self.compute(
            route_no="ROUT-0003", origin="苏州", destination="杭州",
            waypoints_raw="南京>合肥", vehicle_class="小型冷藏车",
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.distance_km, 710)  # 220+170+320
        self.assertTrue(result.over_limit)
        self.assertIn("超出合理上限", result.over_limit_note)
        self.assertIn("偏离原因", result.over_limit_note)
        self.assertEqual(result.basis["参考路径"], ["苏州", "杭州"])  # 直达 150 公里
        self.assertEqual(result.basis["合理上限公里"], 210)

    def test_missing_waypoints_gives_no_reference(self):
        result = self.compute(waypoints_raw="")
        self.assertFalse(result.ok)
        self.assertIn("途经节点缺失", result.reason)
        self.assertIsNone(result.distance_km)

    def test_bad_format_gives_no_reference(self):
        result = self.compute(waypoints_raw="南京，济南")
        self.assertFalse(result.ok)
        self.assertIn("格式不正确", result.reason)

    def test_unknown_segment_gives_no_reference(self):
        result = self.compute(origin="广州", waypoints_raw="南京")
        self.assertFalse(result.ok)
        self.assertIn("未维护里程", result.reason)

    def test_same_origin_and_destination_rejected(self):
        result = self.compute(destination="上海")
        self.assertFalse(result.ok)
        self.assertIn("相同", result.reason)

    def test_duplicate_nodes_rejected(self):
        result = self.compute(waypoints_raw="南京>徐州>南京")
        self.assertFalse(result.ok)
        self.assertIn("重复", result.reason)

    def test_default_vehicle_class_when_missing(self):
        result = self.compute(vehicle_class=None)
        self.assertTrue(result.ok)
        self.assertEqual(result.toll_yuan, round(1400 * 0.70, 2))
        self.assertTrue(any("默认车型" in note for note in result.notes))
        self.assertEqual(result.basis["车型类别"], "中型冷藏车")


if __name__ == "__main__":
    unittest.main()
