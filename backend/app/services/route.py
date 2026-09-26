"""运输路线业务规则：状态流转、字段校验、里程口径计算与重算。

预计里程、预计耗时、过路费用不再由调度员手填：登记或重算时统一按
route_profile 的口径自动生成，并把里程依据快照留在记录上供复核。
"""
from __future__ import annotations

from typing import Any

from app.services import route_profile
from app.store import store

MODULE = "route"
REQUIRED_FIELDS = ["路线编号", "出发地", "目的地"]
STATUS_ORDER = ["可用", "不可用", "备选", "已废弃"]
ACTION_RULES = {"启用路线": "可用", "停用路线": "不可用", "废弃路线": "已废弃"}
NEGATIVE_ACTIONS = ["停用路线"]
RECALC_ACTION = "重算口径"
# 重算口径时允许顺带修正的字段（修正途经节点后可立刻按新节点重算）
RECALC_EDITABLE = ["出发地", "目的地", "途经节点", "车型类别"]


class RouteService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("路线编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str], str]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing, ""
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["途经节点"] = str(values.get("途经节点") or "").strip()
        entry["车型类别"] = str(values.get("车型类别") or "").strip()
        note = self._refresh_reference(entry)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = self._is_abnormal(entry)
        rows.append(entry)
        return entry, [], note

    def run_action(
        self, entry_id: int, action: str, values: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"路线方案 {entry_id} 不存在或已归档"

        if action == RECALC_ACTION:
            return self._recalculate_one(entry, values or {})

        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于运输路线可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"路线方案已{action}"

    def recalculate_outdated(self) -> dict[str, Any]:
        """规则改动后把口径版本落后于当前版本的既有记录全部重算一遍。"""
        version = route_profile.PROFILE_VERSION
        targets = [row for row in store.rows(MODULE) if row.get("口径版本") != version]
        generated = 0
        over_limit = 0
        failures: list[dict[str, Any]] = []
        for row in targets:
            self._refresh_reference(row)
            row["abnormal"] = self._is_abnormal(row)
            if row.get("里程依据"):
                generated += 1
                if row.get("里程超限提示"):
                    over_limit += 1
            else:
                failures.append({
                    "id": row.get("id"),
                    "路线编号": row.get("路线编号"),
                    "原因": row.get("参考值说明"),
                })
        return {
            "口径版本": version,
            "重算总数": len(targets),
            "已生成参考值": generated,
            "其中超限": over_limit,
            "未生成参考值": len(failures),
            "失败明细": failures,
        }

    def _recalculate_one(
        self, entry: dict[str, Any], values: dict[str, Any]
    ) -> tuple[dict[str, Any], str]:
        changed = [
            field for field in RECALC_EDITABLE
            if str(values.get(field) or "").strip()
            and str(values[field]).strip() != str(entry.get(field) or "")
        ]
        for field in changed:
            entry[field] = str(values[field]).strip()
        note = self._refresh_reference(entry)
        entry["abnormal"] = self._is_abnormal(entry)
        message = f"已按口径 {route_profile.PROFILE_VERSION} 重算：{note}"
        if changed:
            message = f"已修正 {'、'.join(changed)} 后{message}"
        return entry, message

    def _refresh_reference(self, entry: dict[str, Any]) -> str:
        """按当前口径刷新一条记录的参考值与里程依据快照，返回可读说明。"""
        result = route_profile.compute_reference(
            route_no=str(entry.get("路线编号") or ""),
            origin=str(entry.get("出发地") or ""),
            destination=str(entry.get("目的地") or ""),
            waypoints_raw=entry.get("途经节点"),
            vehicle_class=str(entry.get("车型类别") or "") or None,
        )
        entry["口径版本"] = route_profile.PROFILE_VERSION
        if result.ok:
            entry["预计里程"] = result.distance_km
            entry["预计耗时"] = result.duration_hours
            entry["过路费用"] = result.toll_yuan
            entry["里程依据"] = result.basis
            entry["里程超限提示"] = result.over_limit_note
            note = "；".join(result.notes) if result.notes else "参考值已按口径自动生成"
            if result.over_limit_note:
                note = f"{note}；{result.over_limit_note}"
        else:
            # 不生成参考值：三个字段清空并说明原因，避免继续沿用手填旧值
            entry["预计里程"] = None
            entry["预计耗时"] = None
            entry["过路费用"] = None
            entry["里程依据"] = None
            entry["里程超限提示"] = ""
            note = f"未生成参考值：{result.reason}"
        entry["参考值说明"] = note
        return note

    @staticmethod
    def _is_abnormal(entry: dict[str, Any]) -> bool:
        return bool(entry.get("里程超限提示")) or not entry.get("里程依据")
