"""评分引擎单元测试 —— 不依赖数据库。

覆盖点：
* 锚点插值的数学正确性（这是全部档位打分的唯一实现）
* V1~V5 五条一票否决规则，含结构化字段与关键词两条路径
* 关键词否定护栏（"不属于无法清退情形" 不得误判）
* 净租售比公式逐项对账
* 五维得分与总分、分级的计算
* **结果可复现**：同一输入重复评估两次，结果必须逐字节一致（PRD 验收标准 3）
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import constants as C  # noqa: E402
from app.core.pipeline import evaluate  # noqa: E402
from app.core.rental import compute_rent  # noqa: E402
from app.core.ruleset import RuleSet, interp  # noqa: E402
from app.core.veto import scan_keywords  # noqa: E402


# ==================================================================== 测试夹具
def make_ruleset() -> RuleSet:
    """用出厂默认构造一个 RuleSet，等价于全新初始化后的数据库状态。"""
    return RuleSet(
        weights=copy.deepcopy(C.DEFAULT_WEIGHTS),
        grade_thresholds=copy.deepcopy(C.DEFAULT_GRADE_THRESHOLDS),
        price_anchors=copy.deepcopy(C.DEFAULT_PRICE_ANCHORS),
        rent_anchors=copy.deepcopy(C.DEFAULT_RENT_ANCHORS),
        land_year_anchors=copy.deepcopy(C.DEFAULT_LAND_YEAR_ANCHORS),
        cost_params=copy.deepcopy(C.DEFAULT_COST_PARAMS),
        legal_penalty=copy.deepcopy(C.DEFAULT_LEGAL_PENALTY),
        location_params=copy.deepcopy(C.DEFAULT_LOCATION_PARAMS),
        ownership_params=copy.deepcopy(C.DEFAULT_OWNERSHIP_PARAMS),
        tag_thresholds=copy.deepcopy(C.DEFAULT_TAG_THRESHOLDS),
        tag_enabled={t["code"]: True for t in C.TAG_REGISTRY},
        default_rent_per_sqm_month=copy.deepcopy(C.DEFAULT_RENT_PER_SQM_MONTH),
        veto_rules=copy.deepcopy(C.VETO_RULE_SEED),
        disclaimer="测试免责声明",
    )


BENCH_SHENZHEN = {
    "rent_per_sqm_month": 30.0, "land_use_tax_per_sqm": 6.0, "property_tax_rate": 0.12,
    "vacancy_ratio": 0.10, "maintenance_ratio": 0.05, "city_tier": "tier1",
    "industry_support_ratio": 0.9, "rental_demand_ratio": 0.9, "turnover_ratio": 0.8,
    "_source": "exact", "_matched": "测试市/测试区/工业厂房",
}


class Stub:
    """最小标的替身，字段与 Asset 对齐。"""

    def __init__(self, **kw):
        defaults = dict(
            title="测试标的", asset_type="industrial", source_platform="manual",
            province=None, city="测试市", district="测试区", address=None,
            area_sqm=None, land_area_sqm=None, start_price=None,
            appraisal_price=None, market_price=None, deposit=None,
            land_nature="granted", land_remaining_years=40.0, compliance_level="full",
            registration_ok=None, transfer_restricted=None,
            can_supplement_procedure=None, implicit_coownership=None,
            mortgage_count=0, seal_count=0, lawsuit_count=0, dispute_freq=0,
            co_ownership_dispute=False, irreversible_seal=False,
            lease_status="unknown", occupied=None, can_clear=None, occupancy_note=None,
            scrap_status="normal",
            tax_owed=0.0, land_idle_fee=0.0, construction_arrears=0.0,
            property_fee_owed=0.0,
            city_tier=None, industry_support_ratio=None, rental_demand_ratio=None,
            turnover_ratio=None, rent_per_sqm_month=None,
            annual_gross_rent_override=None, raw_text=None,
        )
        defaults.update(kw)
        self._d = defaults

    def __getattr__(self, item):
        try:
            return self._d[item]
        except KeyError as exc:
            raise AttributeError(item) from exc

    @property
    def total_arrears(self) -> float:
        return (self._d["tax_owed"] + self._d["land_idle_fee"]
                + self._d["construction_arrears"] + self._d["property_fee_owed"])

    @property
    def reference_price(self):
        return self._d["market_price"] or self._d["appraisal_price"]

    @property
    def discount_rate(self):
        ref, start = self.reference_price, self._d["start_price"]
        if not ref or not start or ref <= 0:
            return None
        return (ref - start) / ref

    @property
    def deadline_days_left(self):
        return None


RS = make_ruleset()


# ==================================================================== 1. 插值
def test_interp_linear():
    anchors = [[0.0, 0.0], [0.1, 10.0], [0.2, 20.0]]
    assert interp(anchors, 0.0) == 0.0
    assert interp(anchors, 0.05) == 5.0          # 中点线性插值
    assert interp(anchors, 0.1) == 10.0
    assert interp(anchors, 0.15) == 15.0
    assert interp(anchors, 0.2) == 20.0
    assert interp(anchors, -5.0) == 0.0          # 左端钳制
    assert interp(anchors, 99.0) == 20.0         # 右端钳制


def test_interp_unsorted_input():
    """锚点乱序输入也应得到正确结果（后台手工编辑 JSON 时很容易乱序）。"""
    assert interp([[0.2, 20.0], [0.0, 0.0], [0.1, 10.0]], 0.05) == 5.0


# ==================================================================== 2. 否决
def test_veto_v1_field_collective():
    a = Stub(land_nature="collective")
    hits = [h["code"] for h in evaluate(a, RS, BENCH_SHENZHEN).veto_hits]
    assert "V1" in hits, "集体用地必须命中 V1"


def test_veto_v1_allocated_requires_condition():
    """划拨用地只有在"无法补办手续"时才算硬缺陷。"""
    assert not evaluate(Stub(land_nature="allocated"), RS, BENCH_SHENZHEN).veto_hits
    a = Stub(land_nature="allocated", can_supplement_procedure=False)
    assert "V1" in [h["code"] for h in evaluate(a, RS, BENCH_SHENZHEN).veto_hits]


def test_veto_v2_lease_status():
    for status in ("long_term", "sale_not_break"):
        a = Stub(lease_status=status)
        assert "V2" in [h["code"] for h in evaluate(a, RS, BENCH_SHENZHEN).veto_hits], status
    # 普通租赁 + 可清场 → 不否决
    assert not evaluate(Stub(lease_status="normal", occupied=True, can_clear=True),
                        RS, BENCH_SHENZHEN).veto_hits


def test_veto_v3_amount_thresholds():
    assert not evaluate(Stub(tax_owed=99999), RS, BENCH_SHENZHEN).veto_hits
    assert "V3" in [h["code"] for h in
                    evaluate(Stub(tax_owed=100000), RS, BENCH_SHENZHEN).veto_hits]
    # 欠费总额触发
    a = Stub(tax_owed=200000, land_idle_fee=150000, construction_arrears=200000)
    assert a.total_arrears == 550000
    assert "V3" in [h["code"] for h in evaluate(a, RS, BENCH_SHENZHEN).veto_hits]


def test_veto_v4_and_v5():
    assert "V4" in [h["code"] for h in evaluate(
        Stub(co_ownership_dispute=True), RS, BENCH_SHENZHEN).veto_hits]
    assert "V4" in [h["code"] for h in evaluate(
        Stub(irreversible_seal=True), RS, BENCH_SHENZHEN).veto_hits]
    assert "V5" in [h["code"] for h in evaluate(
        Stub(scrap_status="equipment_scrapped"), RS, BENCH_SHENZHEN).veto_hits]


def test_keyword_negation_guard():
    """核心回归用例：否定表述不得被误判为一票否决。"""
    hits, negated = scan_keywords("该厂房不属于无法清退情形，可正常腾退。", ["无法清退"])
    assert hits == [], "『不属于无法清退』必须被否定护栏拦下"
    assert negated and negated[0]["keyword"] == "无法清退"

    hits2, _ = scan_keywords("该厂房无法清退，占用情况严重。", ["无法清退"])
    assert hits2 == ["无法清退"], "肯定表述必须命中"


def test_veto_keyword_from_raw_text():
    a = Stub(raw_text="该标的存在买卖不破租赁，承租方租期至2039年。")
    assert "V2" in [h["code"] for h in evaluate(a, RS, BENCH_SHENZHEN).veto_hits]


def test_veto_zero_score_and_grade_c():
    r = evaluate(Stub(land_nature="collective"), RS, BENCH_SHENZHEN)
    assert r.status == "vetoed" and r.grade == "C" and r.total_score == 0.0
    assert r.score_detail.get("vetoed") is True
    assert not r.score_detail.get("dimensions"), "否决标的不应产出维度得分"


# ==================================================================== 3. 租金测算
def test_rent_formula_arithmetic():
    """手工对账净租售比公式，确保每一项都算对。"""
    a = Stub(area_sqm=1000.0, land_area_sqm=1000.0,
             rent_per_sqm_month=30.0, start_price=1_000_000.0)
    d = compute_rent(a, RS, BENCH_SHENZHEN)

    gross = 30.0 * 1000 * 12                      # 360,000
    prop_tax = gross * 0.12                       # 43,200
    land_tax = 6.0 * 1000                         # 6,000
    maint = gross * 0.05                          # 18,000
    vacancy = gross * 0.10                        # 36,000
    net = gross - prop_tax - land_tax - maint - vacancy   # 256,800
    ratio = net / 1_000_000

    assert d["computable"] is True
    assert abs(d["gross_annual_rent"] - gross) < 0.01
    assert abs(d["annual_property_tax"] - prop_tax) < 0.01
    assert abs(d["annual_land_tax"] - land_tax) < 0.01
    assert abs(d["annual_maintenance"] - maint) < 0.01
    assert abs(d["vacancy_reserve"] - vacancy) < 0.01
    assert abs(d["net_annual_income"] - net) < 0.01
    assert abs(d["net_rent_ratio"] - ratio) < 1e-9
    assert d["net_rent_ratio_pct"] == f"{ratio * 100:.2f}%"


def test_rent_vacancy_default_is_10pct():
    """PRD 明确要求空置损耗统一预留 10%。"""
    a = Stub(area_sqm=1000.0, rent_per_sqm_month=30.0, start_price=1_000_000.0)
    d = compute_rent(a, RS, BENCH_SHENZHEN)
    assert d["params_used"]["vacancy_ratio"] == 0.10
    assert abs(d["vacancy_reserve"] - d["gross_annual_rent"] * 0.10) < 0.01


def test_rent_gross_override_wins():
    a = Stub(area_sqm=1000.0, rent_per_sqm_month=30.0, start_price=1_000_000.0,
             annual_gross_rent_override=500_000.0)
    d = compute_rent(a, RS, BENCH_SHENZHEN)
    assert d["gross_annual_rent"] == 500_000.0
    assert d["rent_source"] == "人工指定年毛租金"


def test_rent_not_computable_without_price():
    a = Stub(area_sqm=1000.0, rent_per_sqm_month=30.0)   # 没有起拍价/评估价
    d = compute_rent(a, RS, BENCH_SHENZHEN)
    assert d["computable"] is False and "成交预估价值" in d["reason"]


# ==================================================================== 4. 五维评分
def test_price_dimension_anchors():
    """折价率 40% 应落在锚点 27 分上。"""
    a = Stub(area_sqm=1000.0, start_price=600_000.0, appraisal_price=1_000_000.0)
    r = evaluate(a, RS, BENCH_SHENZHEN)
    assert r.status == "scored"
    assert abs(r.score_detail["dimensions"]["price"]["score"] - 27.0) < 0.01


def test_price_zero_when_no_discount():
    a = Stub(area_sqm=1000.0, start_price=1_000_000.0, appraisal_price=1_000_000.0)
    r = evaluate(a, RS, BENCH_SHENZHEN)
    assert r.score_detail["dimensions"]["price"]["score"] == 0.0


def test_rent_dimension_meets_prd_thresholds():
    """PRD：≥5% 满分；4% 大幅扣分；<4% 继续大幅下滑。"""
    anchors = C.DEFAULT_RENT_ANCHORS
    assert interp(anchors, 0.05) == 30.0
    assert interp(anchors, 0.04) == 18.0
    assert interp(anchors, 0.03) == 12.0
    assert interp(anchors, 0.06) == 30.0


def test_legal_dimension_clean_is_full_marks():
    r = evaluate(Stub(area_sqm=1000.0, start_price=1_000_000.0,
                      appraisal_price=1_000_000.0), RS, BENCH_SHENZHEN)
    assert r.score_detail["dimensions"]["legal"]["score"] == 20.0


def test_legal_dimension_penalty_caps():
    a = Stub(area_sqm=1000.0, start_price=1_000_000.0, appraisal_price=1_000_000.0,
             mortgage_count=99, seal_count=99, lawsuit_count=99, dispute_freq=99)
    r = evaluate(a, RS, BENCH_SHENZHEN)
    assert r.score_detail["dimensions"]["legal"]["score"] == 0.0


def test_total_and_grade_boundaries():
    """分级阈值边界：80 → A，60 → B，59.99 → C。"""
    from app.core.scoring import grade_of
    assert grade_of(80.0, RS) == "A"
    assert grade_of(79.99, RS) == "B"
    assert grade_of(60.0, RS) == "B"
    assert grade_of(59.99, RS) == "C"


def test_dimension_weights_sum_matches_total():
    a = Stub(area_sqm=1000.0, rent_per_sqm_month=30.0,
             start_price=600_000.0, appraisal_price=1_000_000.0)
    r = evaluate(a, RS, BENCH_SHENZHEN)
    dims = r.score_detail["dimensions"]
    assert abs(sum(d["score"] for d in dims.values()) - r.total_score) < 0.011


# ==================================================================== 5. 标签
def test_tags_advantage_and_risk():
    a = Stub(area_sqm=1000.0, rent_per_sqm_month=30.0,
             start_price=400_000.0, appraisal_price=1_000_000.0,
             lease_status="normal", land_remaining_years=15.0)
    r = evaluate(a, RS, BENCH_SHENZHEN)
    adv = {t["code"] for t in r.advantage_tags}
    risk = {t["code"] for t in r.risk_tags}
    assert "high_discount" in adv        # 折价 60%
    assert "clean_judicial" in adv       # 无抵押无查封
    assert "has_lease" in risk
    assert "land_years_short" in risk


# ==================================================================== 6. 可复现性
def test_result_is_reproducible():
    """PRD 验收标准 3：结果可复现 —— 同一输入必须得到完全一致的结果。"""
    a = Stub(area_sqm=1000.0, land_area_sqm=1000.0, rent_per_sqm_month=30.0,
             start_price=600_000.0, appraisal_price=1_000_000.0,
             lease_status="normal", mortgage_count=1, seal_count=1,
             land_remaining_years=25.0)
    r1 = evaluate(a, RS, BENCH_SHENZHEN)
    r2 = evaluate(a, RS, BENCH_SHENZHEN)
    assert r1.total_score == r2.total_score
    assert r1.grade == r2.grade
    assert r1.score_detail == r2.score_detail
    assert r1.rent_detail == r2.rent_detail
    assert r1.advantage_tags == r2.advantage_tags
    assert r1.risk_tags == r2.risk_tags
    assert r1.conclusion == r2.conclusion


def test_params_snapshot_is_serializable():
    import json
    r = evaluate(Stub(area_sqm=1000.0, start_price=1_000_000.0), RS, BENCH_SHENZHEN)
    json.dumps(r.params_snapshot)          # 不抛异常即可


# ==================================================================== 运行器
def _run_all() -> int:
    import traceback

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
            traceback.print_exc(limit=2)
    print(f"\n引擎测试：{passed} 通过 / {len(failed)} 失败 / 共 {len(tests)}")
    return 1 if failed else 0


if __name__ == "__main__":
    print("=" * 70)
    print("评分引擎单元测试")
    print("=" * 70)
    raise SystemExit(_run_all())
