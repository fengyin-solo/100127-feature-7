"""运输路线接口/服务测试：自动填值、偏离提示、重算、重新定基与复核一致性。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import route_rules


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_list_and_seed_route_variants(client):
    data = client.get("/api/route").json()
    assert data["total"] >= 7
    # 同一编号 ROUT-0001 有不同途经节点
    variants = [r for r in data["items"] if r["路线编号"] == "ROUT-0001"]
    assert len(variants) == 2
    assert variants[0]["路线指纹"] != variants[1]["路线指纹"]
    assert variants[0]["预计里程"] != variants[1]["预计里程"]


def test_create_autofills_reference(client):
    r = client.post("/api/route", json={"values": {
        "路线编号": "RT-A", "出发地": "上海", "目的地": "郑州",
        "途经节点": "南京、蚌埠", "车型类别": "重型冷藏车",
    }}).json()
    assert r["ok"] is True
    e = r["entry"]
    assert e["预计里程"] == "880.0 km"
    assert e["过路费用"] is not None and e["口径版本"] == route_rules.CURRENT_RULE_VERSION
    assert "分段里程" in e["frozen_basis"]


def test_create_deviation_explains(client):
    r = client.post("/api/route", json={"values": {
        "路线编号": "RT-B", "出发地": "上海", "目的地": "南京",
        "途经节点": "杭州、苏州、无锡、常州", "车型类别": "中型冷藏车",
    }}).json()
    assert r["ok"] is True
    assert "超出合理上限" in r["message"]
    assert r["entry"]["里程偏离"] == "偏离"
    assert "绕行" in r["entry"]["frozen_deviation_reason"]


def test_create_missing_waypoints_no_reference(client):
    r = client.post("/api/route", json={"values": {
        "路线编号": "RT-C", "出发地": "上海", "目的地": "宁波",
        "途经节点": "", "车型类别": "小型冷藏车",
    }}).json()
    e = r["entry"]
    assert e["预计里程"] is None and e["过路费用"] is None
    assert "途经节点缺失" in e["参考值说明"]


def test_create_bad_format_no_reference(client):
    r = client.post("/api/route", json={"values": {
        "路线编号": "RT-D", "出发地": "上海", "目的地": "温州",
        "途经节点": "杭州、月亮湖站", "车型类别": "重型冷藏车",
    }}).json()
    assert r["entry"]["预计里程"] is None
    assert "月亮湖站" in r["entry"]["参考值说明"]


def test_detail_basis_is_frozen(client):
    """复核人看到的里程依据必须与录入时那份一致。"""
    created = client.post("/api/route", json={"values": {
        "路线编号": "RT-E", "出发地": "上海", "目的地": "郑州",
        "途经节点": "南京、蚌埠", "车型类别": "重型冷藏车",
    }}).json()["entry"]
    rid = created["id"]
    before = json.dumps(client.get(f"/api/route/{rid}").json()["frozen_basis"], ensure_ascii=False)
    # 做一次重算，录入依据快照不变
    client.post(f"/api/route/{rid}/recalculate")
    after = json.dumps(client.get(f"/api/route/{rid}").json()["frozen_basis"], ensure_ascii=False)
    assert before == after


def test_recalc_then_rebaseline_keeps_history(client, monkeypatch):
    # 找一条录入于旧口径 v1.0 的种子记录（id=5）
    rid = 5
    before = client.get(f"/api/route/{rid}").json()
    assert before["frozen_version"] == "v1.0"
    client.post(f"/api/route/recalculate")
    mid = client.get(f"/api/route/{rid}").json()
    assert mid["stale"] is True
    assert mid["recalc_version"] == route_rules.CURRENT_RULE_VERSION
    # 对外字段在定基前仍是录入时口径
    assert mid["口径版本"] == "v1.0"

    r = client.post(f"/api/route/{rid}/rebaseline").json()
    assert r["ok"] is True
    after = client.get(f"/api/route/{rid}").json()
    assert after["frozen_version"] == route_rules.CURRENT_RULE_VERSION
    assert after["stale"] is False
    assert len(after["口径变更历史"]) == 1
    assert after["口径变更历史"][0]["原口径版本"] == "v1.0"


def test_rebaseline_without_recalc_rejected(client):
    # 新建一条记录、不做重算，直接定基应被拦下
    rid = client.post("/api/route", json={"values": {
        "路线编号": "RT-NR", "出发地": "上海", "目的地": "郑州",
        "途经节点": "南京、蚌埠", "车型类别": "重型冷藏车",
    }}).json()["entry"]["id"]
    r = client.post(f"/api/route/{rid}/rebaseline").json()
    assert r["ok"] is False
    assert "重新计算" in r["message"]


def test_recalculate_all_reports_and_preserves(client):
    r = client.post("/api/route/recalculate").json()
    assert r["ok"] is True
    assert r["entry"]["updated"] == r["entry"]["total"]
    # 重算不覆盖录入依据
    one = client.get("/api/route/1").json()
    assert one["预计里程"] is not None


def test_activate_version_then_recalc(client):
    # 切到 v1.0 让一批记录落后，再切回 v1.1，验证激活与受影响数量
    r = client.post("/api/route/rules/activate", json={"values": {"version": "v1.0"}}).json()
    assert r["ok"] is True and r["entry"]["version"] == "v1.0"
    assert r["entry"]["affected"] >= 0
    client.post("/api/route/rules/activate", json={"values": {"version": "v1.1"}})
    assert route_rules.CURRENT_RULE_VERSION == "v1.1"


def test_activate_unknown_version_rejected(client):
    r = client.post("/api/route/rules/activate", json={"values": {"version": "nope"}}).json()
    assert r["ok"] is False


def test_rules_versions_endpoint(client):
    data = client.get("/api/route/rules/versions").json()
    assert data["current"] == route_rules.CURRENT_RULE_VERSION
    assert {v["version"] for v in data["versions"]} == {"v1.0", "v1.1"}


def test_preview_does_not_persist(client):
    before = client.get("/api/route").json()["total"]
    r = client.post("/api/route/preview", json={"values": {
        "路线编号": "SHOULD-NOT-SAVE", "出发地": "上海", "目的地": "武汉",
        "途经节点": "苏州、南京、合肥", "车型类别": "中型冷藏车",
    }}).json()
    assert r["ok"] is True
    assert r["entry"]["frozen_distance_km"] == 885.0
    assert client.get("/api/route").json()["total"] == before
    assert client.get("/api/route", params={"keyword": "SHOULD-NOT-SAVE"}).json()["total"] == 0


def test_missing_required_field(client):
    r = client.post("/api/route", json={"values": {"路线编号": "RT-X", "目的地": "温州"}}).json()
    assert r["ok"] is False and "出发地" in r["message"]


def test_404(client):
    assert client.get("/api/route/99999").status_code == 404
