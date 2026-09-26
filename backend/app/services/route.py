"""运输路线业务规则：里程/耗时/过路费口径的固化、冻结、重算与状态流转。

口径计算本身在 ``route_rules`` 里（纯函数、可独立测试）；本服务负责：

- 录入时按出发地、目的地、有序途经节点与车型类别算出预计里程/耗时/过路费，
  并把当时的「计算依据」原样冻结（frozen_*）；
- 里程超合理上限时给出偏离标记与原因；途经节点缺失/格式不对时不生成参考值；
- 同一「路线编号」允许挂不同途经节点，用路线指纹区分；
- 规则版本升级后，老记录可整体或单条「重新计算」；重算结果单列（recalc_*），
  不覆盖录入时那份依据；复核人确认后可「重新定基」，把当前版本固化为新依据，
  并把上一份依据留痕到口径变更历史。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services import route_rules

MODULE = "route"
REQUIRED_FIELDS = ["路线编号", "出发地", "目的地"]
OPTIONAL_INPUT_FIELDS = ["途经节点", "车型类别"]
STATUS_ORDER = ["可用", "不可用", "备选", "已废弃"]
ACTION_RULES = {"启用路线": "可用", "停用路线": "不可用", "废弃路线": "已废弃"}
NEGATIVE_ACTIONS = ["停用路线"]

# 列表/明细里对外暴露的口径字段名（中文，与既有列保持一致）
DISPLAY_FIELDS = [
    "路线编号", "出发地", "目的地", "途经节点", "车型类别",
    "预计里程", "预计耗时", "过路费用", "路线状态",
    "口径版本", "路线指纹", "里程偏离", "参考值说明",
]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _store_rows() -> list[dict[str, Any]]:
    # 延迟导入：seed 在装载阶段会 import 本模块，顶层再 import store 会形成
    # store → seed → route → store 的循环导入。
    from app.store import store

    return store.rows(MODULE)


def _store_find(entry_id: int) -> dict[str, Any] | None:
    from app.store import store

    return store.find(MODULE, entry_id)


def _apply_calculation(entry: dict[str, Any], result: route_rules.CalculationResult, *, prefix: str) -> None:
    """把一次计算结果写到 entry 的某个字段组（frozen_ 或 recalc_）。"""

    def put(suffix: str, value: Any) -> None:
        entry[f"{prefix}{suffix}"] = value

    if result.ok:
        put("ok", True)
        put("reason", result.reason)
        put("distance_km", result.distance_km)
        put("duration_hours", result.duration_hours)
        put("duration_text", result.duration_text)
        put("toll_fee", result.toll_fee)
        put("deviated", result.deviated)
        put("deviation_reason", result.deviation_reason)
        put("version", result.basis.get("口径版本"))
        put("basis", result.basis)
    else:
        put("ok", False)
        put("reason", result.reason)
        put("distance_km", None)
        put("duration_hours", None)
        put("duration_text", "")
        put("toll_fee", None)
        put("deviated", False)
        put("deviation_reason", "")
        put("version", None)
        put("basis", None)


def _build_entry(values: dict[str, Any], *, entry_id: int, version: str | None = None) -> dict[str, Any]:
    """构造一条路线记录：必填三项 + 口径计算 + 录入时冻结。"""
    origin = str(values.get("出发地") or "").strip()
    destination = str(values.get("目的地") or "").strip()
    raw_waypoints = values.get("途经节点")
    category = str(values.get("车型类别") or "").strip()

    entry: dict[str, Any] = {
        "id": entry_id,
        "路线编号": str(values.get("路线编号") or "").strip(),
        "出发地": origin,
        "目的地": destination,
        "途经节点": "" if raw_waypoints is None else str(raw_waypoints),
        "车型类别": category,
        "路线指纹": route_rules.route_signature(origin, destination, raw_waypoints),
        "status": STATUS_ORDER[0],
        "pending": True,
        "abnormal": False,
        "录入时间": _now(),
        "口径变更历史": [],
    }

    result = route_rules.calculate(origin, destination, raw_waypoints, category, version=version)
    _apply_calculation(entry, result, prefix="frozen_")
    # 录入时当前值就是冻结值；重算前 recalc_* 留空。
    entry["recalc_ok"] = None
    entry["recalc_reason"] = ""
    entry["recalc_distance_km"] = None
    entry["recalc_duration_hours"] = None
    entry["recalc_duration_text"] = ""
    entry["recalc_toll_fee"] = None
    entry["recalc_deviated"] = False
    entry["recalc_deviation_reason"] = ""
    entry["recalc_version"] = None
    entry["recalc_basis"] = None
    entry["recalc_time"] = None
    entry["stale"] = False
    entry["basis_time"] = entry["录入时间"]
    _sync_derived(entry)
    return entry


def _sync_derived(entry: dict[str, Any]) -> None:
    """把冻结依据同步到对外展示的中文字段，并联动 abnormal/pending。"""
    # 对外读字段以「录入时冻结」为准，保证复核人与录入人看到同一份口径。
    entry["路线编号"] = entry.get("路线编号")
    entry["口径版本"] = entry.get("frozen_version") or "—"
    entry["预计里程"] = (
        f"{entry['frozen_distance_km']} km" if entry.get("frozen_distance_km") is not None else None
    )
    entry["预计耗时"] = entry.get("frozen_duration_text") or None
    entry["过路费用"] = entry.get("frozen_toll_fee")
    if not entry.get("frozen_ok"):
        entry["里程偏离"] = "无参考值"
    elif entry.get("frozen_deviated"):
        entry["里程偏离"] = "偏离"
    else:
        entry["里程偏离"] = "正常"
    entry["参考值说明"] = _build_note(entry)
    # 偏离在看板上计入异常量，便于调度/复核关注。
    entry["abnormal"] = bool(entry.get("frozen_deviated"))
    entry["pending"] = bool(entry.get("stale")) or entry.get("status") != STATUS_ORDER[-1]


def _build_note(entry: dict[str, Any]) -> str:
    """汇总这条路线当前要提示给人的一句话（偏离/无参考值/待重算）。"""
    if not entry.get("frozen_ok"):
        return f"未生成参考值：{entry.get('frozen_reason', '途经节点缺失或格式不对')}"
    parts: list[str] = []
    if entry.get("frozen_deviated"):
        parts.append(entry.get("frozen_deviation_reason", "里程超出合理上限"))
    elif entry.get("frozen_reason"):
        parts.append(entry["frozen_reason"])
    if entry.get("stale"):
        parts.append(
            f"口径已升级到 {route_rules.CURRENT_RULE_VERSION}，该记录录入时为 "
            f"{entry.get('frozen_version')}，已按新口径试算，待复核人确认重新定基"
        )
    return "；".join(parts)


class RouteService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = _store_rows()
        if keyword:
            rows = [
                row
                for row in rows
                if keyword in str(row.get("路线编号", ""))
                or keyword in str(row.get("路线指纹", ""))
                or keyword in str(row.get("出发地", ""))
                or keyword in str(row.get("目的地", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return _store_find(entry_id)

    def preview(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """登记前试算：不落库，返回按口径计算的结果与依据，供前端即时预览。"""
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        entry = _build_entry(values, entry_id=0)
        return entry, []

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = _store_rows()
        entry_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
        entry = _build_entry(values, entry_id=entry_id)
        rows.append(entry)
        return entry, []

    # -- 规则版本 ---------------------------------------------------------

    def activate_rule_version(self, version: str) -> tuple[dict[str, Any] | None, str]:
        """规则改动后发布新版本：只切换生效指针，规则本身版本化、只增不改。

        切换后不自动改写老记录的里程——老记录保留录入时依据，由 recalculate_all
        统一重新算一遍并交复核确认。
        """
        try:
            rule = route_rules.get_rule(version)
        except KeyError as exc:
            return None, str(exc)
        previous = route_rules.CURRENT_RULE_VERSION
        if version == previous:
            return {"version": version, "previous": previous}, f"口径 {version} 本就是当前生效版本"
        route_rules.CURRENT_RULE_VERSION = version
        affected = sum(1 for row in _store_rows() if row.get("frozen_version") != version)
        return (
            {"version": rule.version, "previous": previous, "affected": affected},
            f"口径已由 {previous} 切换到 {version}（{rule.note}）；{affected} 条既有记录口径落后，"
            "请执行「全部重新计算」并交复核确认重新定基",
        )

    def recalculate(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """用当前生效口径重新试算一条记录；只写 recalc_*，不动录入时冻结依据。"""
        entry = _store_find(entry_id)
        if entry is None:
            return None, f"路线方案 {entry_id} 不存在或已归档"
        result = route_rules.calculate(
            entry["出发地"], entry["目的地"], entry["途经节点"], entry["车型类别"]
        )
        _apply_calculation(entry, result, prefix="recalc_")
        entry["recalc_time"] = _now()
        entry["stale"] = entry.get("frozen_version") != route_rules.CURRENT_RULE_VERSION
        _sync_derived(entry)
        if not result.ok:
            return entry, f"按新口径试算未生成参考值：{result.reason}"
        return entry, f"已按口径 {route_rules.CURRENT_RULE_VERSION} 试算，录入时依据保持不变，待复核确认"

    def recalculate_all(self) -> dict[str, Any]:
        """规则改动后把既有路线记录全部按当前口径重新算一遍。"""
        rows = _store_rows()
        updated = 0
        deviations = 0
        no_value = 0
        stale = 0
        for entry in rows:
            was_deviated = bool(entry.get("frozen_deviated"))
            had_value = entry.get("frozen_ok") is True
            self.recalculate(int(entry["id"]))
            updated += 1
            # 只统计「本次口径升级后新出现」的偏离与新取不到参考值的记录。
            if entry.get("recalc_deviated") and not was_deviated:
                deviations += 1
            if entry.get("recalc_ok") is False and had_value:
                no_value += 1
            if entry.get("stale"):
                stale += 1
        return {
            "total": len(rows),
            "updated": updated,
            "new_deviations": deviations,
            "no_reference": no_value,
            "stale": stale,
            "version": route_rules.CURRENT_RULE_VERSION,
        }

    def rebaseline(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """复核确认：把最近一次重算结果固化为新依据，旧依据留痕，保证可追溯。"""
        entry = _store_find(entry_id)
        if entry is None:
            return None, f"路线方案 {entry_id} 不存在或已归档"
        if entry.get("recalc_ok") is None:
            return None, "该记录尚未按新口径试算，请先执行重新计算"
        if entry.get("recalc_ok") is False:
            return None, f"新口径未能生成参考值，不能重新定基：{entry.get('recalc_reason')}"

        history = entry.setdefault("口径变更历史", [])
        history.append({
            "时间": entry.get("basis_time"),
            "原口径版本": entry.get("frozen_version"),
            "原里程km": entry.get("frozen_distance_km"),
            "原耗时": entry.get("frozen_duration_text"),
            "原过路费": entry.get("frozen_toll_fee"),
            "原依据": entry.get("frozen_basis"),
            "替换时间": _now(),
        })
        for suffix in (
            "ok", "reason", "distance_km", "duration_hours", "duration_text",
            "toll_fee", "deviated", "deviation_reason", "version", "basis",
        ):
            entry[f"frozen_{suffix}"] = entry.get(f"recalc_{suffix}")
        entry["basis_time"] = entry.get("recalc_time") or _now()
        # 清空待确认的重算区
        entry["recalc_ok"] = None
        entry["recalc_reason"] = ""
        entry["recalc_distance_km"] = None
        entry["recalc_duration_hours"] = None
        entry["recalc_duration_text"] = ""
        entry["recalc_toll_fee"] = None
        entry["recalc_deviated"] = False
        entry["recalc_deviation_reason"] = ""
        entry["recalc_version"] = None
        entry["recalc_basis"] = None
        entry["recalc_time"] = None
        entry["stale"] = False
        _sync_derived(entry)
        return entry, f"已重新定基为口径 {entry['frozen_version']}，原录入依据已留痕，可在口径变更历史中查看"

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = _store_find(entry_id)
        if entry is None:
            return None, f"路线方案 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于运输路线可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        _sync_derived(entry)
        return entry, f"路线方案已{action}"
