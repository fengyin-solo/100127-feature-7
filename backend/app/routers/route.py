"""运输路线接口：维护路线方案，里程口径试算、登记、重算与状态流转。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services import route_profile
from app.services.route import RouteService

router = APIRouter(prefix="/api/route", tags=["运输路线"])

service = RouteService()

LIST_FIELDS = ["路线编号", "出发地", "目的地", "途经节点", "车型类别", "预计里程", "预计耗时", "过路费用", "口径版本", "路线状态"]
STATUSES = ["可用", "不可用", "备选", "已废弃"]


@router.get("/profile")
def get_profile() -> dict[str, Any]:
    """当前里程口径规则：版本、时速、停靠、上限系数与车型费率表。"""
    return route_profile.profile_summary()


@router.post("/preview")
def preview_reference(payload: EntryPayload) -> dict[str, Any]:
    """登记前试算：按当前口径返回参考值或未生成原因，不落库。"""
    values = payload.values
    missing = [field for field in ["出发地", "目的地"] if not str(values.get(field) or "").strip()]
    if missing:
        return {"ok": False, "reason": f"缺少必填字段：{'、'.join(missing)}"}
    result = route_profile.compute_reference(
        route_no=str(values.get("路线编号") or "（未编号）"),
        origin=str(values.get("出发地") or ""),
        destination=str(values.get("目的地") or ""),
        waypoints_raw=values.get("途经节点"),
        vehicle_class=str(values.get("车型类别") or "") or None,
    )
    if not result.ok:
        return {"ok": False, "reason": result.reason}
    return {
        "ok": True,
        "预计里程": result.distance_km,
        "预计耗时": result.duration_hours,
        "过路费用": result.toll_yuan,
        "超限提示": result.over_limit_note,
        "说明": "；".join(result.notes),
        "依据": result.basis,
    }


@router.post("/recalculate")
def recalculate_entries() -> dict[str, Any]:
    """口径规则改动后批量重算：把口径版本落后的既有路线记录全部重算一遍。"""
    return service.recalculate_outdated()


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出运输路线清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "route", "total": total, "items": items}


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按路线编号检索"),
    status: str | None = Query(default=None, description="可用、不可用、备选、已废弃"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按路线编号与状态过滤运输路线列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条路线方案明细（含录入时的里程依据快照，供复核）；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"路线方案 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条路线方案：预计里程、预计耗时、过路费用按口径自动生成，缺字段时说明原因。"""
    entry, missing, note = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message=f"路线方案已登记；{note}", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条路线方案执行启用路线、停用路线、废弃路线、重算口径；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
