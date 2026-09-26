"""运输路线接口：维护路线方案，并固化里程/耗时/过路费口径。

口径取值规则在 services/route_rules.py，业务流转在 services/route.py。
除既有的登记、查询、状态流转、导出外，新增：

- GET  /api/route/rules/versions        查看口径版本
- POST /api/route/recalculate           规则改动后既有记录整体重新算一遍
- POST /api/route/{id}/recalculate      单条按当前口径试算（不覆盖录入依据）
- POST /api/route/{id}/rebaseline       复核确认后把试算结果固化为新依据
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services import route_rules
from app.services.route import RouteService

router = APIRouter(prefix="/api/route", tags=["运输路线"])

service = RouteService()

LIST_FIELDS = ["路线编号", "出发地", "目的地", "途经节点", "车型类别", "预计里程", "预计耗时", "过路费用", "路线状态"]
STATUSES = ["可用", "不可用", "备选", "已废弃"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按路线编号/路线指纹/起讫地检索"),
    status: str | None = Query(default=None, description="可用、不可用、备选、已废弃"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按路线编号、指纹与状态过滤运输路线列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/rules/versions")
def rule_versions() -> dict[str, Any]:
    """查看里程/耗时/过路费口径的历史版本与当前生效版本。"""
    return {
        "current": route_rules.CURRENT_RULE_VERSION,
        "vehicle_categories": list(route_rules.VEHICLE_CATEGORIES),
        "versions": route_rules.list_rule_versions(),
    }


@router.get("/rules/nodes")
def rule_nodes() -> dict[str, Any]:
    """返回路网里程库中已知节点，供录入途经节点时提示与校验。"""
    return {"nodes": sorted(route_rules.KNOWN_NODES)}


@router.post("/recalculate")
def recalculate_all() -> ActionResult:
    """规则改动以后把既有运输路线记录全部按当前口径重新算一遍。

    重算结果单列（recalc_*），不覆盖录入时冻结的依据，等待复核人确认重新定基。
    """
    summary = service.recalculate_all()
    message = (
        f"已按口径 {summary['version']} 重新计算 {summary['updated']} 条记录；"
        f"{summary['stale']} 条录入口径落后于当前版本，新判偏离 {summary['new_deviations']} 条、"
        f"新取不到参考值 {summary['no_reference']} 条。"
        "各记录录入时依据保持不变，待复核确认后重新定基。"
    )
    return ActionResult(ok=True, message=message, entry=summary)


@router.post("/rules/activate", response_model=ActionResult)
def activate_rule(payload: EntryPayload) -> ActionResult:
    """规则改动后发布某版口径：切换生效版本，规则本身版本化、只增不改。"""
    version = str(payload.values.get("version") or "").strip()
    if not version:
        return ActionResult(ok=False, message="缺少要发布的口径版本号 version")
    info, message = service.activate_rule_version(version)
    if info is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=info)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出运输路线清单：返回当前全量数据（含冻结依据与待确认重算结果）。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "route", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条路线方案明细，含录入时冻结的里程依据与最近试算结果。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"路线方案 {entry_id} 不存在或已归档")
    return entry


@router.post("/preview", response_model=ActionResult)
def preview_entry(payload: EntryPayload) -> ActionResult:
    """登记前试算：不写库，按口径返回预计里程/耗时/过路费与依据，供即时预览。"""
    entry, missing = service.preview(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    if entry.get("frozen_ok") is False:
        return ActionResult(
            ok=True,
            message=f"按当前口径未生成参考值：{entry.get('frozen_reason')}",
            entry=entry,
        )
    tail = entry.get("frozen_deviation_reason") if entry.get("frozen_deviated") else "口径试算通过"
    return ActionResult(ok=True, message=str(tail), entry=entry)


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条路线方案：按起讫点与途经节点自动算口径；缺必填字段或无法取参考值时说明原因。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    if entry.get("frozen_ok") is False:
        return ActionResult(
            ok=True,
            message=f"路线方案已登记，但未生成里程/耗时/过路费参考值：{entry.get('frozen_reason')}",
            entry=entry,
        )
    if entry.get("frozen_deviated"):
        return ActionResult(
            ok=True,
            message=f"路线方案已登记，但里程超出合理上限：{entry.get('frozen_deviation_reason')}",
            entry=entry,
        )
    return ActionResult(ok=True, message="路线方案已登记，里程/耗时/过路费已按口径自动填入", entry=entry)


@router.post("/{entry_id}/recalculate", response_model=ActionResult)
def recalculate_one(entry_id: int) -> ActionResult:
    """按当前生效口径重新试算单条记录；录入时冻结的那份依据不变。"""
    entry, message = service.recalculate(entry_id)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/rebaseline", response_model=ActionResult)
def rebaseline_one(entry_id: int) -> ActionResult:
    """复核人确认后，把新口径试算结果固化为里程依据，原依据留痕。"""
    entry, message = service.rebaseline(entry_id)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条路线方案执行启用路线、停用路线、废弃路线；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
