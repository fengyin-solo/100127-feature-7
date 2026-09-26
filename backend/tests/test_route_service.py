"""运输路线服务的集成测试：登记自动算、批量重算与复核依据一致性。"""
from __future__ import annotations

import copy
import unittest

from app.services import route_profile
from app.services.route import RouteService
from app.store import store


class RouteServiceTest(unittest.TestCase):
    def setUp(self):
        self._backup = copy.deepcopy(store.rows("route"))
        self._version = route_profile.PROFILE_VERSION
        self.service = RouteService()

    def tearDown(self):
        store.rows("route")[:] = self._backup
        route_profile.PROFILE_VERSION = self._version

    def test_create_entry_autofills_reference(self):
        entry, missing, note = self.service.create_entry({
            "路线编号": "ROUT-9001",
            "出发地": "上海",
            "目的地": "北京",
            "途经节点": "南京>徐州>济南",
            "车型类别": "大型冷藏车",
        })
        self.assertEqual(missing, [])
        self.assertEqual(entry["预计里程"], 1400)
        self.assertEqual(entry["预计耗时"], 22.5)
        self.assertEqual(entry["过路费用"], 1330.0)
        self.assertEqual(entry["口径版本"], route_profile.PROFILE_VERSION)
        self.assertEqual(entry["里程依据"]["口径键"], "ROUT-9001|上海>南京>徐州>济南>北京")
        self.assertFalse(entry["abnormal"])
        self.assertIn("自动生成", note)

    def test_create_entry_without_waypoints_keeps_empty_reference(self):
        entry, missing, note = self.service.create_entry({
            "路线编号": "ROUT-9002",
            "出发地": "上海",
            "目的地": "北京",
        })
        self.assertEqual(missing, [])
        self.assertIsNone(entry["预计里程"])
        self.assertIsNone(entry["预计耗时"])
        self.assertIsNone(entry["过路费用"])
        self.assertIsNone(entry["里程依据"])
        self.assertIn("途经节点缺失", entry["参考值说明"])
        self.assertTrue(entry["abnormal"])

    def test_recalculate_outdated_refreshes_seed_rows(self):
        summary = self.service.recalculate_outdated()
        self.assertEqual(summary["重算总数"], 5)
        self.assertEqual(summary["已生成参考值"], 4)
        self.assertEqual(summary["其中超限"], 1)
        self.assertEqual(summary["未生成参考值"], 1)
        rows = {row["路线编号"]: row for row in store.rows("route") if row["id"] in (1, 4, 5)}
        self.assertEqual(rows["ROUT-0001"]["预计里程"], 1400)
        self.assertEqual(rows["ROUT-0003"]["里程超限提示"].count("偏离原因"), 1)
        self.assertIsNone(rows["ROUT-0004"]["预计里程"])
        self.assertIn("途经节点缺失", rows["ROUT-0004"]["参考值说明"])
        # 全部重算完后没有落后版本，再算一遍应为 0 条
        self.assertEqual(self.service.recalculate_outdated()["重算总数"], 0)

    def test_same_route_no_recalculates_per_waypoints(self):
        self.service.recalculate_outdated()
        rows = [row for row in store.rows("route") if row["路线编号"] == "ROUT-0001"]
        self.assertEqual(len(rows), 2)
        distances = sorted(row["预计里程"] for row in rows)
        self.assertEqual(distances, [1400, 1750])
        keys = {row["里程依据"]["口径键"] for row in rows}
        self.assertEqual(len(keys), 2)

    def test_reviewer_sees_entry_time_basis_until_recalculated(self):
        entry, _, _ = self.service.create_entry({
            "路线编号": "ROUT-9003",
            "出发地": "上海",
            "目的地": "北京",
            "途经节点": "南京>徐州>济南",
            "车型类别": "大型冷藏车",
        })
        basis_at_entry = copy.deepcopy(entry["里程依据"])
        # 规则改动：版本号升级（模拟费率/时速调整）
        route_profile.PROFILE_VERSION = "v2"
        # 复核人打开记录，看到的仍是录入时那份依据
        stored = self.service.get_entry(entry["id"])
        self.assertEqual(stored["里程依据"], basis_at_entry)
        self.assertEqual(stored["里程依据"]["口径版本"], self._version)
        # 批量重算后依据才按新口径刷新
        summary = self.service.recalculate_outdated()
        self.assertGreaterEqual(summary["重算总数"], 1)
        self.assertEqual(self.service.get_entry(entry["id"])["里程依据"]["口径版本"], "v2")

    def test_recalc_action_can_fix_waypoints(self):
        entry, _, _ = self.service.create_entry({
            "路线编号": "ROUT-9004",
            "出发地": "上海",
            "目的地": "北京",
            "途经节点": "",
            "车型类别": "大型冷藏车",
        })
        self.assertIsNone(entry["里程依据"])
        updated, message = self.service.run_action(
            entry["id"], "重算口径", {"途经节点": "南京>徐州>济南"}
        )
        self.assertIsNotNone(updated["里程依据"])
        self.assertEqual(updated["预计里程"], 1400)
        self.assertIn("已修正 途经节点", message)

    def test_unknown_action_rejected(self):
        entry, message = self.service.run_action(1, "随便操作")
        self.assertIsNone(entry)
        self.assertIn("不属于", message)


if __name__ == "__main__":
    unittest.main()
