"""运输路线口径引擎：里程、耗时、过路费的取值规则集中在这里。

调度员以前手填里程与耗时，口径因人而异。这里把取值规则固化成纯函数：

1. 里程按「出发地 → 各途经节点（按顺序）→ 目的地」逐段相加；
2. 耗时 = 里程 ÷ 车型类别对应的平均时速（含装卸/休整预留）；
3. 过路费按车型类别对应的费率与里程分档给出参考值；
4. 里程超过「出发地直达目的地合理里程」的上限倍数时，标记偏离并说明原因；
5. 口径必须能区分同一路线编号下不同的途经节点——节点顺序不同就是不同路线；
6. 规则改动后老记录要能重算；同时录入那一刻的依据会被原样冻结，复核人看到的
   永远是录入时那份，不会被后续规则改动悄悄改写。

规则全部版本化：每次调整都登记到 RULE_VERSIONS，计算结果会带上所用版本号，
并生成一份可追溯的「计算依据」。这个文件只依赖标准库，方便单独测试。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# 基础口径数据
# ---------------------------------------------------------------------------

# 路网节点：相邻节点之间的标准里程（公里）。键用 frozenset，里程不区分方向。
# 真实项目里这张表来自里程库/地图服务；这里内置一份可直接演示的路网。
SEGMENT_DISTANCE: dict[frozenset[str], float] = {}


def _add_edge(a: str, b: str, km: float) -> None:
    SEGMENT_DISTANCE[frozenset((a, b))] = float(km)


# 干线
_add_edge("上海", "苏州", 100)
_add_edge("上海", "无锡", 133)
_add_edge("上海", "南京", 300)
_add_edge("苏州", "无锡", 50)
_add_edge("苏州", "常州", 95)
_add_edge("苏州", "南京", 220)
_add_edge("无锡", "常州", 45)
_add_edge("常州", "南京", 130)
_add_edge("南京", "合肥", 175)
_add_edge("南京", "蚌埠", 200)
_add_edge("合肥", "武汉", 390)
_add_edge("蚌埠", "郑州", 380)
_add_edge("郑州", "西安", 510)
_add_edge("上海", "杭州", 180)
_add_edge("杭州", "宁波", 155)
_add_edge("宁波", "温州", 270)
_add_edge("苏州", "杭州", 160)
_add_edge("无锡", "杭州", 220)
_add_edge("合肥", "蚌埠", 150)
# 主要起讫点的直达（高速）里程，仅用于「里程合理上限」的偏离比对，
# 不参与逐段累加；逐段里程一律按出发地→途经节点→目的地相加。
_add_edge("上海", "武汉", 834)
_add_edge("上海", "西安", 1380)

# 城配 / 园区节点
_add_edge("上海", "昆山冷链园", 60)
_add_edge("昆山冷链园", "苏州", 45)
_add_edge("上海", "青浦分拨中心", 40)
_add_edge("青浦分拨中心", "昆山冷链园", 70)
_add_edge("青浦分拨中心", "苏州", 70)
_add_edge("苏州", "苏州物流园", 20)
_add_edge("南京", "江宁分拨中心", 25)
_add_edge("杭州", "杭州冷链园", 18)

#: 路网里全部已知节点。
KNOWN_NODES: frozenset[str] = frozenset(
    {node for pair in SEGMENT_DISTANCE for node in pair}
)


# ---------------------------------------------------------------------------
# 版本化规则
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RuleSet:
    """某一版口径：时速、过路费费率、里程合理上限都按版本冻结。"""

    version: str
    note: str
    # 车型类别 -> 平均时速（公里/小时，含途中装卸与休整预留）
    speed_kmh: dict[str, float]
    # 车型类别 -> (里程阈值公里, 阈值内单价 元/公里, 超出部分单价 元/公里)
    toll_rate: dict[str, tuple[float, float, float]]
    # 里程合理上限 = 直达里程 × 倍数
    distance_upper_factor: float
    # 途中午休/休整的固定预留（小时）
    rest_hours: float


#: 规则版本表：新版本往尾部追加，永远只新增不改写，老记录据此可重算。
RULE_VERSIONS: list[RuleSet] = [
    RuleSet(
        version="v1.0",
        note="首版口径：小型60/中型50/重型42 km/h，过路费0.55/0.72/0.92元每公里，超直达1.6倍判偏离",
        speed_kmh={"小型冷藏车": 60.0, "中型冷藏车": 50.0, "重型冷藏车": 42.0},
        toll_rate={
            # 300 公里以内按基础单价；超出部分按长途单价（多数省份阶梯下浮）
            "小型冷藏车": (300.0, 0.55, 0.45),
            "中型冷藏车": (300.0, 0.72, 0.60),
            "重型冷藏车": (300.0, 0.92, 0.78),
        },
        distance_upper_factor=1.6,
        rest_hours=1.0,
    ),
    RuleSet(
        version="v1.1",
        note="更新口径：干线时速上调（65/55/46），重型车过路费微调，偏离阈值放宽到1.8倍",
        speed_kmh={"小型冷藏车": 65.0, "中型冷藏车": 55.0, "重型冷藏车": 46.0},
        toll_rate={
            "小型冷藏车": (300.0, 0.55, 0.45),
            "中型冷藏车": (300.0, 0.70, 0.58),
            "重型冷藏车": (300.0, 0.90, 0.75),
        },
        distance_upper_factor=1.8,
        rest_hours=1.0,
    ),
]

#: 当前生效版本：规则改动只需把指针指向新版本，老记录仍保留各自录入时的版本。
CURRENT_RULE_VERSION = "v1.1"

#: 允许选择的车型类别（与车辆档案口径对齐）。
VEHICLE_CATEGORIES: tuple[str, ...] = ("小型冷藏车", "中型冷藏车", "重型冷藏车")

#: 途经节点允许的分隔符：中文顿号/逗号、英文逗号、分号、斜杠、空白、箭头。
NODE_SEPARATORS = "、，,;；/／>→⟶ \t\r\n"


def get_rule(version: str) -> RuleSet:
    """按版本号取规则；找不到时给可读错误而不是静默回退到最新版。"""
    for rule in RULE_VERSIONS:
        if rule.version == version:
            return rule
    raise KeyError(f"口径版本「{version}」不存在，可用版本：{', '.join(r.version for r in RULE_VERSIONS)}")


def current_rule() -> RuleSet:
    return get_rule(CURRENT_RULE_VERSION)


def list_rule_versions() -> list[dict[str, Any]]:
    """给前端/管理端展示有哪些口径版本。"""
    return [
        {
            "version": rule.version,
            "note": rule.note,
            "active": rule.version == CURRENT_RULE_VERSION,
            "speed_kmh": dict(rule.version == rule.version and rule.speed_kmh),
        }
        for rule in RULE_VERSIONS
    ]


# ---------------------------------------------------------------------------
# 节点解析与校验
# ---------------------------------------------------------------------------

def parse_waypoints(raw: Any) -> list[str]:
    """把「上海、苏州 → 南京」这类文本切成有序、去空的节点列表。"""
    if raw is None:
        return []
    text = str(raw).strip()
    if not text:
        return []
    for sep in NODE_SEPARATORS:
        text = text.replace(sep, "\n")
    return [token.strip() for token in text.split("\n") if token.strip()]


@dataclass
class ValidationResult:
    """途经节点校验结论：ok 为真才允许生成参考值。"""

    ok: bool
    reason: str = ""
    nodes: list[str] = field(default_factory=list)
    unknown_nodes: list[str] = field(default_factory=list)
    duplicate: bool = False


def validate_waypoints(origin: str, destination: str, raw_waypoints: Any) -> ValidationResult:
    """校验途经节点。

    - 缺失或空白：不生成参考值，并明确说明「途经节点缺失」；
    - 解析后为空串/格式无法识别：同上，说明格式问题；
    - 节点不在路网里：不生成参考值，指出未知节点；
    - 节点重复（绕回已到节点）：提示但不阻断，仍可生成参考值。
    """
    origin = str(origin or "").strip()
    destination = str(destination or "").strip()
    if not origin or not destination:
        return ValidationResult(False, "出发地或目的地缺失，无法确定起讫点")

    if raw_waypoints is None or str(raw_waypoints).strip() == "":
        return ValidationResult(False, "途经节点缺失：未填写任何途经节点，按口径不生成里程/耗时/过路费参考值")

    nodes = parse_waypoints(raw_waypoints)
    if not nodes:
        return ValidationResult(
            False,
            "途经节点格式不对：只识别到分隔符而没有有效节点名称，请用「、」或「→」分隔，例如 上海、苏州、南京",
        )

    # 任意一段含空白内部字符（如「上海 南京」未加分隔）这里已被拆开；
    # 再检查是否存在看起来未分隔的粘连（两个已知城市名直接相连）。
    chain = [origin, *nodes, destination]
    unknown = [node for node in chain if node not in KNOWN_NODES]
    if unknown:
        return ValidationResult(
            False,
            f"途经节点格式不对或节点不在路网里程库中：{'、'.join(dict.fromkeys(unknown))}；"
            f"按口径不生成参考值，请核对节点名称或补全里程",
            nodes=nodes,
            unknown_nodes=list(dict.fromkeys(unknown)),
        )

    duplicate = len(set(chain)) != len(chain)
    reason = ""
    if duplicate:
        reason = "途经节点存在重复（路线绕回已到节点），里程会被重复累计，请确认是否符合实际"
    return ValidationResult(True, reason, nodes=nodes, duplicate=duplicate)


def _segment_km(a: str, b: str) -> float | None:
    return SEGMENT_DISTANCE.get(frozenset((a, b)))


def _direct_km(origin: str, destination: str) -> float | None:
    """出发地直达目的地的里程；路网未直接连通时返回 None。"""
    return _segment_km(origin, destination)


# ---------------------------------------------------------------------------
# 计算
# ---------------------------------------------------------------------------

def _round1(value: float) -> float:
    return round(value + 1e-9, 1)


def _round2(value: float) -> float:
    return round(value + 1e-9, 2)


def calc_toll(rule: RuleSet, category: str, distance_km: float) -> float:
    """过路费按车型类别与里程分档：阈值内基础单价，超出部分长途单价。"""
    threshold, base_price, long_price = rule.toll_rate[category]
    if distance_km <= threshold:
        amount = distance_km * base_price
    else:
        amount = threshold * base_price + (distance_km - threshold) * long_price
    return _round2(amount)


@dataclass
class CalculationResult:
    """一次口径计算的完整结果。"""

    ok: bool
    reason: str = ""
    warnings: list[str] = field(default_factory=list)
    distance_km: float | None = None
    duration_hours: float | None = None
    duration_text: str = ""
    toll_fee: float | None = None
    deviated: bool = False
    deviation_reason: str = ""
    basis: dict[str, Any] = field(default_factory=dict)


def _format_duration(hours: float) -> str:
    """把小时数渲染成「X 小时 Y 分钟」，便于调度员直读。"""
    total_minutes = int(round(hours * 60))
    h, m = divmod(total_minutes, 60)
    if h and m:
        return f"{h}小时{m}分钟"
    if h:
        return f"{h}小时"
    return f"{m}分钟"


def calculate(
    origin: str,
    destination: str,
    raw_waypoints: Any,
    category: str,
    *,
    version: str | None = None,
) -> CalculationResult:
    """按口径计算预计里程、预计耗时、过路费参考值。

    返回的 basis 是可序列化的计算依据快照，应在录入时原样存档；复核时展示这份
    存档而不是用最新规则重算，从而保证「复核人看到的里程依据与录入时一致」。
    """
    origin = str(origin or "").strip()
    destination = str(destination or "").strip()
    category = str(category or "").strip()

    if not origin or not destination:
        return CalculationResult(False, "出发地或目的地缺失，无法按口径计算")
    if not category:
        return CalculationResult(False, "车型类别缺失：过路费用与耗时均按车型类别取值，请先选择车型类别")
    try:
        rule = get_rule(version) if version else current_rule()
    except KeyError as exc:
        return CalculationResult(False, str(exc))
    if category not in rule.speed_kmh:
        return CalculationResult(
            False,
            f"车型类别「{category}」不在口径范围内，可选：{'、'.join(rule.speed_kmh)}",
        )

    validation = validate_waypoints(origin, destination, raw_waypoints)
    if not validation.ok:
        # 途经节点缺失/格式不对：不生成任何参考值，并把原因带回去。
        return CalculationResult(False, validation.reason)

    chain = [origin, *validation.nodes, destination]
    segments: list[dict[str, Any]] = []
    total = 0.0
    for start, end in zip(chain, chain[1:]):
        km = _segment_km(start, end)
        if km is None:
            return CalculationResult(
                False,
                f"途经节点格式不对：里程库中没有「{start} ↔ {end}」这段的标准里程，"
                f"按口径不生成参考值，请核对途经顺序或补全该段里程",
            )
        total += km
        segments.append({"from": start, "to": end, "km": _round1(km)})

    distance_km = _round1(total)
    speed = rule.speed_kmh[category]
    duration_hours = _round1(total / speed + rule.rest_hours)
    toll_fee = calc_toll(rule, category, distance_km)

    warnings: list[str] = []
    if validation.duplicate and validation.reason:
        warnings.append(validation.reason)

    # 里程偏离判定：实际路径超过「直达 × 上限倍数」即视为超出合理范围。
    deviated = False
    deviation_reason = ""
    direct = _direct_km(origin, destination)
    if direct is None:
        warnings.append(
            f"路网未维护「{origin} ↔ {destination}」的直达里程，无法做上限偏离比对，仅给出累加结果"
        )
    else:
        upper = _round1(direct * rule.distance_upper_factor)
        if distance_km > upper:
            deviated = True
            extra = _round1(distance_km - direct)
            deviation_reason = (
                f"预计里程 {distance_km}km 超出合理上限 {upper}km"
                f"（{origin}直达{destination}为{_round1(direct)}km，口径{rule.version}允许"
                f"{rule.distance_upper_factor:g}倍）；实际比直达多绕行 {extra}km，"
                f"请确认途经节点是否存在绕行、拼载或路线编号选错"
            )

    basis = {
        "口径版本": rule.version,
        "出发地": origin,
        "目的地": destination,
        "途经节点": validation.nodes,
        "车型类别": category,
        "节点链": chain,
        "分段里程": segments,
        "预计里程km": distance_km,
        "平均时速kmh": speed,
        "休整预留h": rule.rest_hours,
        "预计耗时h": duration_hours,
        "过路费规则": {
            "阈值km": rule.toll_rate[category][0],
            "阈值内单价": rule.toll_rate[category][1],
            "超出单价": rule.toll_rate[category][2],
        },
        "过路费用": toll_fee,
        "直达里程km": _round1(direct) if direct is not None else None,
        "偏离上限km": upper if direct is not None else None,
        "是否偏离": deviated,
    }

    return CalculationResult(
        ok=True,
        reason="；".join(warnings),
        warnings=warnings,
        distance_km=distance_km,
        duration_hours=duration_hours,
        duration_text=_format_duration(duration_hours),
        toll_fee=toll_fee,
        deviated=deviated,
        deviation_reason=deviation_reason,
        basis=basis,
    )


def route_signature(origin: str, destination: str, raw_waypoints: Any) -> str:
    """同一线路编号下区分不同途经节点的指纹：起讫点 + 有序节点。

    节点顺序不同（上海→苏州→无锡 与 上海→无锡→苏州）指纹不同，
    从而同一「路线编号」可以挂多套里程/耗时口径。
    """
    nodes = parse_waypoints(raw_waypoints)
    chain = [str(origin or "").strip(), *nodes, str(destination or "").strip()]
    return " > ".join(chain)
