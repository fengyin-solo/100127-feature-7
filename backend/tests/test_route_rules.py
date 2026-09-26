"""口径引擎单元测试：里程/耗时/过路费、节点校验、偏离判定、版本化。"""
from __future__ import annotations

import pytest

from app.services import route_rules as rr


def test_distance_sums_ordered_waypoints():
    """里程按 出发地→途经节点(有序)→目的地 逐段相加。"""
    res = rr.calculate("上海", "武汉", "苏州、南京、合肥", "中型冷藏车")
    # 100(沪苏) + 220(苏宁) + 175(宁合) + 390(合汉)
    assert res.ok is True
    assert res.distance_km == 885.0
    assert [s["km"] for s in res.basis["分段里程"]] == [100.0, 220.0, 175.0, 390.0]


def test_same_route_number_different_waypoints_differ():
    """同一起讫点，途经节点不同（顺序不同），里程与指纹都必须不同。"""
    a = rr.calculate("上海", "武汉", "苏州、南京、合肥", "中型冷藏车")
    b = rr.calculate("上海", "武汉", "无锡、常州、南京、合肥", "中型冷藏车")
    assert a.ok and b.ok
    assert a.distance_km != b.distance_km
    sig_a = rr.route_signature("上海", "武汉", "苏州、南京、合肥")
    sig_b = rr.route_signature("上海", "武汉", "无锡、常州、南京、合肥")
    assert sig_a != sig_b


def test_waypoint_order_matters():
    """同样的节点集合，顺序不同指纹也不同。"""
    s1 = rr.route_signature("上海", "南京", "苏州、无锡")
    s2 = rr.route_signature("上海", "南京", "无锡、苏州")
    assert s1 != s2


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_missing_waypoints_no_reference(raw):
    """途经节点缺失时不生成参考值，并说明原因。"""
    res = rr.calculate("上海", "武汉", raw, "中型冷藏车")
    assert res.ok is False
    assert res.distance_km is None and res.toll_fee is None and res.duration_hours is None
    assert "途经节点缺失" in res.reason


def test_only_separators_is_format_error():
    res = rr.calculate("上海", "武汉", "、，、；", "中型冷藏车")
    assert res.ok is False
    assert "格式" in res.reason


def test_unknown_node_no_reference():
    res = rr.calculate("上海", "武汉", "苏州、火星站、合肥", "中型冷藏车")
    assert res.ok is False
    assert "火星站" in res.reason
    assert res.distance_km is None


def test_missing_segment_no_reference():
    """相邻节点之间没有维护里程，视为顺序/格式问题，不生成参考值。"""
    res = rr.calculate("上海", "武汉", "南京", "中型冷藏车")  # 南京↔武汉 不相邻
    assert res.ok is False
    assert "南京" in res.reason and "武汉" in res.reason


def test_missing_category_no_reference():
    res = rr.calculate("上海", "武汉", "苏州、南京、合肥", "")
    assert res.ok is False
    assert "车型类别" in res.reason


def test_toll_by_category_and_distance():
    """过路费按车型类别与里程分档取值。"""
    # 小型 105km <= 300 阈值，单价 0.55
    small = rr.calculate("上海", "苏州", "昆山冷链园", "小型冷藏车")
    assert small.toll_fee == round(105 * 0.55, 2)
    # 重型长距离超过 300km 阈值，分段计价
    heavy = rr.calculate("上海", "西安", "南京、蚌埠、郑州", "重型冷藏车")
    assert heavy.ok is True
    threshold, base, longp = rr.current_rule().toll_rate["重型冷藏车"]
    km = heavy.distance_km
    assert heavy.toll_fee == round(threshold * base + (km - threshold) * longp, 2)


def test_duration_uses_category_speed():
    res = rr.calculate("上海", "武汉", "苏州、南京、合肥", "中型冷藏车")
    rule = rr.current_rule()
    assert res.duration_hours == round(885 / rule.speed_kmh["中型冷藏车"] + rule.rest_hours, 1)
    assert "小时" in res.duration_text


def test_deviation_flagged_with_reason():
    """里程超出合理上限必须偏离标记，并给出包含数值与原因的说明。"""
    res = rr.calculate("上海", "南京", "杭州、苏州、无锡、常州", "中型冷藏车")
    assert res.ok is True
    assert res.deviated is True
    assert "超出合理上限" in res.deviation_reason
    assert "绕行" in res.deviation_reason


def test_non_deviated_normal_route():
    res = rr.calculate("上海", "武汉", "苏州、南京、合肥", "中型冷藏车")
    assert res.deviated is False
    assert res.deviation_reason == ""


def test_rule_versions_are_frozen_and_distinct():
    """不同版本同一路线结果不同，且老版本仍可取用（版本化、只增不改）。"""
    v10 = rr.calculate("上海", "武汉", "苏州、南京、合肥", "中型冷藏车", version="v1.0")
    v11 = rr.calculate("上海", "武汉", "苏州、南京、合肥", "中型冷藏车", version="v1.1")
    assert v10.ok and v11.ok
    assert v10.duration_hours != v11.duration_hours  # 时速不同
    assert v10.basis["口径版本"] == "v1.0"
    assert v11.basis["口径版本"] == "v1.1"


def test_unknown_version_rejected():
    res = rr.calculate("上海", "武汉", "苏州", "中型冷藏车", version="v9.9")
    assert res.ok is False and "v9.9" in res.reason


def test_duplicate_node_warns_but_still_calculates():
    res = rr.calculate("上海", "杭州", "昆山冷链园、苏州", "小型冷藏车")
    # 上海->昆山->苏州->杭州 链条中节点不重复；改用构造重复
    res2 = rr.calculate("上海", "杭州", "杭州、宁波", "小型冷藏车")  # 目的地与途经重复
    assert res2.ok is True
    assert res2.warnings


def test_basis_is_serializable_snapshot():
    import json

    res = rr.calculate("上海", "武汉", "苏州、南京、合肥", "中型冷藏车")
    dumped = json.dumps(res.basis, ensure_ascii=False)
    again = json.loads(dumped)
    assert again["预计里程km"] == res.distance_km
    assert again["分段里程"][0] == {"from": "上海", "to": "苏州", "km": 100.0}
