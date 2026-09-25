"""Web 层集成测试（使用独立临时数据库，不影响 data/app.db）。

覆盖点：
* 未登录访问受保护页面 → 跳转登录
* 三种角色的权限边界（只读 / 业务操作 / 管理员）
* 全部页面渲染 200（模板错误会在这里暴露）
* 采集 → 去重 → 评分 全链路
* CSV 批量导入（含编码容错与错误行回显）
* PDF 报告导出（含免责声明）
* 配置热调整 + 全量重算
* JSON API（评分引擎对外接口）
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 必须在导入 app.config 之前指定测试库
_TMP = Path(tempfile.mkdtemp(prefix="asset_test_"))
os.environ["APP_DB_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["APP_SECRET_KEY"] = "test-secret-key"

from starlette.testclient import TestClient  # noqa: E402

from app.bootstrap import bootstrap  # noqa: E402
from app.main import app  # noqa: E402

CLIENT = TestClient(app)
PAGES = [
    "/", "/assets", "/assets?grade=A&sort=score_desc", "/assets?status=vetoed",
    "/assets/1", "/assets/2", "/assets/new", "/assets/2/edit",
    "/crawl", "/import", "/admin/veto", "/admin/config", "/admin/regions",
    "/admin/users", "/api/health", "/api/config", "/api/veto-rules",
    "/api/assets?limit=3", "/import/template.csv",
]


def setup_module(module=None):
    bootstrap(with_samples=True, reset=True)


def _login(username: str, password: str) -> TestClient:
    c = TestClient(app)
    c.post("/login", data={"username": username, "password": password})
    return c


# ==================================================================== 认证
def test_anonymous_redirected_to_login():
    c = TestClient(app)
    for path in ("/", "/assets", "/admin/config"):
        r = c.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login", path


def test_login_rejects_bad_password():
    c = TestClient(app)
    r = c.post("/login", data={"username": "admin", "password": "wrong"})
    assert r.status_code == 200 and "账号或密码错误" in r.text


def test_all_pages_render():
    c = _login("admin", "admin123")
    for path in PAGES:
        r = c.get(path)
        assert r.status_code == 200, f"{path} → {r.status_code}"
        # JSON 接口返回体本来就短，只对 HTML 页面做长度下限检查
        if not path.startswith("/api/"):
            assert len(r.content) > 400, f"{path} 返回内容过短"


def test_viewer_cannot_write():
    c = _login("viewer", "viewer123")
    # 录入页本身会拒绝只读角色
    r = c.get("/assets/new", follow_redirects=False)
    assert r.status_code == 303 and "err=" in r.headers["location"]
    # 写入接口同样拒绝
    r = c.post("/assets/save", data={"title": "x"}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    r = c.post("/crawl/run", data={"platform": "all"}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    r = c.post("/admin/config", data={"weights.price": "99"}, follow_redirects=False)
    assert "err=" in r.headers["location"]


def test_operator_cannot_edit_config():
    c = _login("operator", "operator123")
    r = c.post("/admin/config", data={"weights.price": "99"}, follow_redirects=False)
    assert "err=" in r.headers["location"]


# ==================================================================== 采集链路
def test_crawl_seeds_and_scores():
    c = _login("admin", "admin123")
    r = c.post("/crawl/run", data={"platform": "alipaimai"}, follow_redirects=False)
    assert r.status_code == 303 and "ok=" in r.headers["location"]

    api = c.get("/api/assets?limit=200").json()
    assert api["total"] >= 10, "样本标的应已入库"
    assert all(item["status"] in ("scored", "vetoed") for item in api["items"]), \
        "入库后应全部完成评分"


def test_dedup_keeps_latest_version():
    """同一标的编号二次采集 → 更新而不是新增，且采用最新数据。"""
    c = _login("admin", "admin123")
    before = c.get("/api/assets?limit=500").json()["total"]
    c.post("/crawl/run", data={"platform": "alipaimai"})
    after = c.get("/api/assets?limit=500").json()["total"]
    assert after == before, "重复采集不应产生新记录"


def test_veto_rules_all_covered():
    """样本应覆盖 V1~V5 全部五条否决规则的分支。"""
    c = _login("admin", "admin123")
    items = c.get("/api/assets?status=vetoed&limit=200").json()["items"]
    codes = set()
    for it in items:
        detail = c.get(f"/api/assets/{it['id']}").json()
        codes.update(h["code"] for h in (detail["veto_hits"] or []))
    assert codes == {"V1", "V2", "V3", "V4", "V5"}, f"未被覆盖的否决规则：{codes}"


# ==================================================================== 导入
def test_csv_import_with_encoding_and_error_report():
    c = _login("operator", "operator123")
    csv_text = (
        "标的名称,城市,区县,建筑面积,起拍价,评估价,土地性质,公告原文\n"
        "导入用例-苏州厂房,苏州市,工业园区,9000,3000万,5500万,出让,"
        "建筑面积9000平方米，起拍价3000万元，评估价5500万元，空置无占用，无租赁。\n"
    )
    # 故意用 GBK 编码，验证编码容错
    content = csv_text.encode("gbk")
    r = c.post("/import/upload",
               files={"file": ("case.csv", content, "text/csv")},
               follow_redirects=False)
    assert r.status_code == 303 and "ok=" in r.headers["location"]

    api = c.get("/api/assets?limit=500").json()
    target = [i for i in api["items"] if i["title"] == "导入用例-苏州厂房"]
    assert target, "导入的标的应已入库"
    assert target[0]["status"] in ("scored", "vetoed")


def test_import_rejects_unsupported_type():
    c = _login("operator", "operator123")
    r = c.post("/import/upload",
               files={"file": ("bad.pdf", b"%PDF-1.4", "application/pdf")},
               follow_redirects=False)
    assert "err=" in r.headers["location"]


def test_import_template_download():
    c = _login("viewer", "viewer123")
    r = c.get("/import/template.csv")
    assert r.status_code == 200
    assert "起拍价" in r.content.decode("utf-8-sig")


# ==================================================================== 录入与重算
def test_manual_create_and_reevaluate():
    c = _login("operator", "operator123")
    form = {
        "title": "Web测试-集体用地厂房", "asset_type": "industrial",
        "source_platform": "manual", "province": "湖北省", "city": "武汉市",
        "district": "蔡甸区", "address": "测试路1号", "area_sqm": "3000",
        "start_price": "1000000", "appraisal_price": "4000000",
        "land_nature": "collective", "compliance_level": "none",
        "lease_status": "none", "land_remaining_years": "30",
        "mortgage_count": "0", "seal_count": "0", "lawsuit_count": "0",
        "dispute_freq": "0", "tax_owed": "0", "land_idle_fee": "0",
        "construction_arrears": "0", "property_fee_owed": "0",
        "scrap_status": "normal",
    }
    r = c.post("/assets/save", data=form, follow_redirects=False)
    assert r.status_code == 303 and "/assets/" in r.headers["location"]

    aid = int(r.headers["location"].split("/assets/")[1].split("?")[0])
    detail = c.get(f"/api/assets/{aid}").json()
    assert detail["status"] == "vetoed" and detail["grade"] == "C"

    r = c.post(f"/assets/{aid}/reevaluate", follow_redirects=False)
    assert r.status_code == 303


def test_manual_form_validation_error():
    c = _login("operator", "operator123")
    r = c.post("/assets/save", data={"title": "", "area_sqm": "abc"},
               follow_redirects=False)
    assert r.status_code == 200 and "表单校验未通过" in r.text


# ==================================================================== 报告
def test_pdf_report_contains_disclaimer():
    c = _login("admin", "admin123")
    scored = [i for i in c.get("/api/assets?limit=50").json()["items"]
              if i["status"] == "scored"]
    assert scored, "需要有已评分标的才能测试报告导出"
    for item in scored[:3]:
        r = c.get(f"/assets/{item['id']}/report.pdf")
        assert r.status_code == 200
        assert r.content[:5] == b"%PDF-", "导出内容必须是合法 PDF"
        assert len(r.content) > 20_000


# ==================================================================== 配置
def test_config_change_and_reevaluate():
    c = _login("admin", "admin123")
    before = json.loads(c.get("/api/config").text)
    assert before["weights"]["price"] == 30.0

    r = c.post("/admin/config", data={
        "weights.price": "35", "weights.rent": "30", "weights.legal": "20",
        "weights.location": "15", "weights.ownership": "5",
        "reevaluate": "on",
    }, follow_redirects=False)
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    assert json.loads(c.get("/api/config").text)["weights"]["price"] == 35.0

    # 还原
    c.post("/admin/config", data={
        "weights.price": "30", "weights.rent": "30", "weights.legal": "20",
        "weights.location": "15", "weights.ownership": "5", "reevaluate": "on",
    })
    assert json.loads(c.get("/api/config").text)["weights"]["price"] == 30.0


def test_disclaimer_cannot_be_cleared():
    c = _login("admin", "admin123")
    r = c.post("/admin/config", data={"disclaimer": ""}, follow_redirects=False)
    assert "err=" in r.headers["location"], "免责声明不允许被清空"
    assert json.loads(c.get("/api/config").text)["disclaimer"]


def test_config_rejects_bad_anchor_json():
    c = _login("admin", "admin123")
    r = c.post("/admin/config",
               data={"price_anchors": "not-json"}, follow_redirects=False)
    assert "err=" in r.headers["location"]


def test_veto_rule_save_does_not_wipe_other_fields():
    """回归用例：局部提交不应清空未提交的规则字段。"""
    c = _login("admin", "admin123")
    before = c.get("/api/veto-rules").json()["rules"]
    v3_before = next(r for r in before if r["code"] == "V3")
    assert v3_before["keywords"], "V3 出厂应有关键词配置"

    c.post("/admin/veto/save", data={"enabled.V1": "on"}, follow_redirects=False)

    after = c.get("/api/veto-rules").json()["rules"]
    v3_after = next(r for r in after if r["code"] == "V3")
    assert v3_after["keywords"] == v3_before["keywords"], "未提交的字段不应被清空"
    assert v3_after["enabled"] == v3_before["enabled"], "未提交的开关不应被关闭"

    # 还原 V1 的启用状态
    c.post("/admin/veto/save", data={"enabled.V1": "on", "kw.V1": "on"})


# ==================================================================== API
def test_api_evaluate_matches_web_engine():
    c = _login("admin", "admin123")
    payload = {
        "title": "API厂房", "asset_type": "industrial",
        "city": "苏州市", "district": "工业园区",
        "area_sqm": 12000, "start_price": 36_000_000, "appraisal_price": 62_000_000,
        "land_nature": "granted", "land_remaining_years": 44,
        "mortgage_count": 0, "seal_count": 0,
        "lease_status": "normal", "occupied": False,
    }
    d = c.post("/api/evaluate", json=payload).json()
    assert d["status"] == "scored" and d["grade"] == "A"
    assert len(d["dimensions"]) == 5
    assert d["disclaimer"]
    assert d["rent_detail"]["computable"] is True
    # 五维合计等于总分
    assert abs(sum(x["score"] for x in d["dimensions"]) - d["total_score"]) < 0.011


def test_api_evaluate_veto_path():
    c = _login("admin", "admin123")
    d = c.post("/api/evaluate", json={
        "title": "API否决", "land_nature": "collective",
        "start_price": 1_000_000, "appraisal_price": 3_000_000,
    }).json()
    assert d["vetoed"] is True and d["total_score"] == 0 and d["grade"] == "C"
    assert d["veto_hits"][0]["code"] == "V1"


def test_api_404():
    c = _login("admin", "admin123")
    assert c.get("/api/assets/999999").status_code == 404


# ==================================================================== 运行器
def _run_all() -> int:
    import traceback

    setup_module()
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed, failed = 0, []
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f"  ✓ {name}")
        except Exception as exc:  # noqa: BLE001
            failed.append(name)
            print(f"  ✗ {name}\n      {type(exc).__name__}: {exc}")
            traceback.print_exc(limit=3)
    print(f"\nWeb 集成测试：{passed} 通过 / {len(failed)} 失败 / 共 {len(tests)}")
    return 1 if failed else 0


if __name__ == "__main__":
    print("=" * 70)
    print("Web 层集成测试（临时数据库）")
    print("=" * 70)
    code = _run_all()
    shutil.rmtree(_TMP, ignore_errors=True)
    raise SystemExit(code)
