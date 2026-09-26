"""运输路线里程口径：区段里程表、车型费率表与参考值计算规则。

口径固化在这一份文件里：规则要改就改这里的常量并递增 PROFILE_VERSION，
既有路线记录通过"重算口径"动作或批量重算接口按新版本刷新。
每条记录计算时会留下一份"里程依据"快照，复核人看到的始终是录入时那份，
不随后续规则改动而变化。
"""
from __future__ import annotations

import heapq
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# 口径版本：规则（里程表、费率、时速、上限系数）有任何调整都要递增
PROFILE_VERSION = "v1"

# 预计耗时 = 里程 ÷ 平均时速 + 途经节点数 × 单点停靠分钟
AVG_SPEED_KMH = 65.0
STOP_MINUTES_PER_NODE = 20

# 合理里程上限 = 起终点最短参考里程 × 上限系数，超出即提示偏离
DETOUR_LIMIT_RATIO = 1.4

# 过路费费率（元/公里），按车型类别
TOLL_RATES: dict[str, float] = {
    "小型冷藏车": 0.45,
    "中型冷藏车": 0.70,
    "大型冷藏车": 0.95,
    "重型挂车": 1.30,
}
DEFAULT_VEHICLE_CLASS = "中型冷藏车"

# 区段里程表（公里）：相邻节点间的高速里程，无向
SEGMENT_KM: dict[tuple[str, str], int] = {
    ("上海", "苏州"): 100,
    ("上海", "南京"): 300,
    ("上海", "杭州"): 180,
    ("上海", "合肥"): 470,
    ("苏州", "无锡"): 50,
    ("苏州", "南京"): 220,
    ("苏州", "杭州"): 150,
    ("无锡", "南京"): 170,
    ("南京", "合肥"): 170,
    ("南京", "徐州"): 350,
    ("南京", "济南"): 620,
    ("杭州", "合肥"): 320,
    ("杭州", "金华"): 180,
    ("合肥", "徐州"): 300,
    ("合肥", "郑州"): 560,
    ("徐州", "济南"): 320,
    ("济南", "北京"): 430,
    ("郑州", "北京"): 690,
    ("金华", "南昌"): 260,
    ("南昌", "长沙"): 340,
    ("长沙", "广州"): 670,
}

# 途经节点格式：节点1>节点2>…，单节点 2~12 位中文、字母、数字或间隔号
NODE_PATTERN = re.compile(r"^[一-鿿A-Za-z0-9·]{2,12}$")
WAYPOINT_FORMAT_HINT = "途经节点需按「节点1>节点2」填写，单节点为 2~12 位中文、字母或数字"


@dataclass
class ReferenceResult:
    """一次口径计算的结果：要么给出参考值与依据快照，要么给出未生成原因。"""

    ok: bool
    reason: str = ""
    distance_km: int | None = None
    duration_hours: float | None = None
    toll_yuan: float | None = None
    over_limit: bool = False
    over_limit_note: str = ""
    notes: list[str] = field(default_factory=list)
    basis: dict[str, Any] = field(default_factory=dict)


def parse_waypoints(raw: Any) -> tuple[list[str] | None, str]:
    """解析途经节点；缺失或格式不对时返回原因，由调用方决定不生成参考值。"""
    text = str(raw or "").strip()
    if not text:
        return None, "途经节点缺失，无法确定计算路径"
    parts = [part.strip() for part in text.split(">")]
    for index, part in enumerate(parts):
        if not part:
            return None, f"途经节点格式不正确：第 {index + 1} 段为空（存在多余的「>」），{WAYPOINT_FORMAT_HINT}"
        if not NODE_PATTERN.match(part):
            return None, f"途经节点格式不正确：「{part}」不符合要求，{WAYPOINT_FORMAT_HINT}"
    return parts, ""


def segment_km(node_a: str, node_b: str) -> int | None:
    """查相邻节点区段里程；里程表无向，两个方向都试。"""
    km = SEGMENT_KM.get((node_a, node_b))
    if km is None:
        km = SEGMENT_KM.get((node_b, node_a))
    return km


def shortest_reference(origin: str, destination: str) -> tuple[int | None, list[str]]:
    """起终点在里程表中的最短路径，作为"合理里程"的参考基准。"""
    graph: dict[str, list[tuple[str, int]]] = {}
    for (node_a, node_b), km in SEGMENT_KM.items():
        graph.setdefault(node_a, []).append((node_b, km))
        graph.setdefault(node_b, []).append((node_a, km))
    if origin not in graph or destination not in graph:
        return None, []
    best: dict[str, float] = {origin: 0.0}
    prev: dict[str, str] = {}
    heap: list[tuple[float, str]] = [(0.0, origin)]
    while heap:
        dist, node = heapq.heappop(heap)
        if dist > best.get(node, float("inf")):
            continue
        if node == destination:
            break
        for nxt, weight in graph[node]:
            candidate = dist + weight
            if candidate < best.get(nxt, float("inf")):
                best[nxt] = candidate
                prev[nxt] = node
                heapq.heappush(heap, (candidate, nxt))
    if destination not in best:
        return None, []
    path = [destination]
    while path[-1] != origin:
        path.append(prev[path[-1]])
    path.reverse()
    return int(best[destination]), path


def compute_reference(
    *,
    route_no: str,
    origin: str,
    destination: str,
    waypoints_raw: Any,
    vehicle_class: str | None,
    now: datetime | None = None,
) -> ReferenceResult:
    """按口径计算预计里程、预计耗时与过路费参考值。

    途经节点缺失、格式不对或区段未维护里程时不生成参考值（ok=False），
    reason 里写明原因；计算成功时 basis 为可留档复核的里程依据快照。
    """
    origin = str(origin or "").strip()
    destination = str(destination or "").strip()
    notes: list[str] = []

    waypoints, error = parse_waypoints(waypoints_raw)
    if waypoints is None:
        return ReferenceResult(ok=False, reason=f"{error}，未生成参考值")
    if origin == destination:
        return ReferenceResult(ok=False, reason="出发地与目的地相同，无法构成运输路径，未生成参考值")

    path = [origin, *waypoints, destination]
    duplicated = sorted({node for node in path if path.count(node) > 1})
    if duplicated:
        return ReferenceResult(
            ok=False,
            reason=f"节点序列存在重复：{'、'.join(duplicated)}，请检查出发地、目的地与途经节点，未生成参考值",
        )

    legs: list[dict[str, Any]] = []
    missing_legs: list[str] = []
    for node_a, node_b in zip(path, path[1:]):
        km = segment_km(node_a, node_b)
        if km is None:
            missing_legs.append(f"{node_a}>{node_b}")
        else:
            legs.append({"区段": f"{node_a}>{node_b}", "里程公里": km})
    if missing_legs:
        return ReferenceResult(
            ok=False,
            reason=f"区段 {'、'.join(missing_legs)} 未维护里程，未生成参考值",
        )

    distance_km = int(sum(int(leg["里程公里"]) for leg in legs))
    stop_minutes = STOP_MINUTES_PER_NODE * len(waypoints)
    duration_hours = round(distance_km / AVG_SPEED_KMH + stop_minutes / 60, 1)

    vehicle = str(vehicle_class or "").strip()
    if not vehicle:
        vehicle = DEFAULT_VEHICLE_CLASS
        notes.append(f"车型类别未填写，过路费按默认车型 {DEFAULT_VEHICLE_CLASS} 计")
    elif vehicle not in TOLL_RATES:
        notes.append(f"车型类别「{vehicle}」不在费率表内，过路费按默认车型 {DEFAULT_VEHICLE_CLASS} 计")
        vehicle = DEFAULT_VEHICLE_CLASS
    rate = TOLL_RATES[vehicle]
    toll_yuan = round(distance_km * rate, 2)

    reference_km, reference_path = shortest_reference(origin, destination)
    over_limit = False
    over_limit_note = ""
    limit_km: int | None = None
    if reference_km is not None:
        limit_km = int(round(reference_km * DETOUR_LIMIT_RATIO))
        if distance_km > limit_km:
            detour = distance_km - reference_km
            over_limit = True
            over_limit_note = (
                f"预计里程 {distance_km} 公里超出合理上限 {limit_km} 公里"
                f"（参考路径 {'>'.join(reference_path)} 约 {reference_km} 公里 × {DETOUR_LIMIT_RATIO}）；"
                f"偏离原因：途经 {len(waypoints)} 个节点，较参考路径绕行 {detour} 公里，"
                "请确认途经节点是否必要或调整走参考路径"
            )

    moment = (now or datetime.now()).isoformat(timespec="seconds")
    basis = {
        "口径版本": PROFILE_VERSION,
        "口径键": f"{route_no}|{'>'.join(path)}",
        "节点序列": path,
        "区段明细": legs,
        "里程公里": distance_km,
        "平均时速公里": AVG_SPEED_KMH,
        "途经停靠分钟": stop_minutes,
        "耗时构成": f"{distance_km} 公里 ÷ {AVG_SPEED_KMH:g} 公里/小时 + {len(waypoints)} 个途经节点 × {STOP_MINUTES_PER_NODE} 分钟",
        "车型类别": vehicle,
        "费率元每公里": rate,
        "参考路径": reference_path,
        "参考里程公里": reference_km,
        "上限系数": DETOUR_LIMIT_RATIO,
        "合理上限公里": limit_km,
        "计算时间": moment,
    }
    return ReferenceResult(
        ok=True,
        distance_km=distance_km,
        duration_hours=duration_hours,
        toll_yuan=toll_yuan,
        over_limit=over_limit,
        over_limit_note=over_limit_note,
        notes=notes,
        basis=basis,
    )


def profile_summary() -> dict[str, Any]:
    """当前口径规则说明，给前端展示与表单提示用。"""
    return {
        "版本": PROFILE_VERSION,
        "平均时速公里": AVG_SPEED_KMH,
        "途经停靠分钟": STOP_MINUTES_PER_NODE,
        "上限系数": DETOUR_LIMIT_RATIO,
        "费率表": TOLL_RATES,
        "默认车型": DEFAULT_VEHICLE_CLASS,
        "途经节点格式": WAYPOINT_FORMAT_HINT,
    }
