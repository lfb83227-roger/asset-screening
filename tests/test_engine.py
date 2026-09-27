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
    """最小标的替身，字段与 Asset 对齐（含双轨制的物权 / 债权字段）。"""

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
            property_fee_owed=0.0, utility_owed=0.0, heating_owed=0.0,
            transfer_tax_estimate=0.0, owner_is_company=None,
            city_tier=None, industry_support_ratio=None, rental_demand_ratio=None,
            turnover_ratio=None, rent_per_sqm_month=None,
            annual_gross_rent_override=None, raw_text=None,
            # ---- 双轨制
            asset_class="property",
            appraisal_at=None, appraisal_refreshed_price=None,
            market_comp_source=None, market_comp_count=None,
            debt_principal=None, debt_interest=None, collateral_value=None,
            debt_start_price=None,
            guarantee_rank="unknown", execution_stage="unknown",
            debtor_solvency="unknown", debt_doc_level="unknown",
            debt_transferable=None, debt_limitation_ok=None, competing_claims=0,
        )
        defaults.update(kw)
        self._d = defaults

    def __getattr__(self, item):
        try:
            return self._d[item]
        except KeyError as exc:
            raise AttributeError(item) from exc

    @property
    def is_debt(self) -> bool:
        return (self._d["asset_class"] or "property") == "debt"

    @property
    def total_arrears(self) -> float:
        return (self._d["tax_owed"] + self._d["land_idle_fee"]
                + self._d["construction_arrears"] + self._d["property_fee_owed"]
                + self._d["utility_owed"] + self._d["heating_owed"])

    @property
    def effective_appraisal(self):
        return self._d["appraisal_refreshed_price"] or self._d["appraisal_price"]

    @property
    def appraisal_age_months(self):
        return None

    @property
    def appraisal_stale(self) -> bool:
        return False

    @property
    def reference_price(self):
        return self._d["market_price"] or self.effective_appraisal

    @property
    def discount_rate(self):
        ref, start = self.reference_price, self._d["start_price"]
        if not ref or not start or ref <= 0:
            return None
        return (ref - start) / ref

    @property
    def discount_vs_appraisal(self):
        p, start = self.effective_appraisal, self._d["start_price"]
        if not p or not start or p <= 0:
            return None
        return (p - start) / p

    @property
    def discount_vs_market(self):
        m, start = self._d["market_price"], self._d["start_price"]
        if not m or not start or m <= 0:
            return None
        return (m - start) / m

    @property
    def benchmark_divergence(self):
        a, m = self.discount_vs_appraisal, self.discount_vs_market
        if a is None or m is None:
            return None
        return abs(a - m)

    # ---- 债权属性
    @property
    def debt_total_claim(self) -> float:
        return float(self._d["debt_principal"] or 0) + float(self._d["debt_interest"] or 0)

    @property
    def guarantee_coverage(self):
        claim = self.debt_total_claim
        if not self._d["collateral_value"] or claim <= 0:
            return None
        return float(self._d["collateral_value"]) / claim

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
    """业务确认：V2 清场风险改为**重点扣分项**，不再一票否决。

    长期租约 / 买卖不破租赁仍会命中 V2，但落在 penalty_hits 而非 veto_hits，
    同时清场维度会被锁到 4 级并额外重罚。
    """
    for status in ("long_term", "sale_not_break"):
        r = evaluate(Stub(lease_status=status), RS, BENCH_SHENZHEN)
        assert "V2" in [h["code"] for h in r.penalty_hits], status
        assert "V2" not in [h["code"] for h in r.veto_hits], "V2 不应再一票否决"
        assert r.status == "scored", "命中 V2 仍应进入量化打分"
    # 普通租赁 + 可清场 → 既不否决也不重罚
    r2 = evaluate(Stub(lease_status="normal", occupied=True, can_clear=True),
                  RS, BENCH_SHENZHEN)
    assert not r2.veto_hits and "V2" not in [h["code"] for h in r2.penalty_hits]


def test_veto_v3_amount_thresholds():
    """业务确认：V3 大额欠费改为**重点扣分项**，不再一票否决。"""
    assert not evaluate(Stub(tax_owed=99999), RS, BENCH_SHENZHEN).penalty_hits
    r = evaluate(Stub(tax_owed=100000), RS, BENCH_SHENZHEN)
    assert "V3" in [h["code"] for h in r.penalty_hits]
    assert "V3" not in [h["code"] for h in r.veto_hits], "V3 不应再一票否决"
    assert r.status == "scored"
    # 欠费总额触发
    a = Stub(tax_owed=200000, land_idle_fee=150000, construction_arrears=200000)
    assert a.total_arrears == 550000
    r2 = evaluate(a, RS, BENCH_SHENZHEN)
    assert "V3" in [h["code"] for h in r2.penalty_hits]


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
    """关键词路径也应遵守双轨处置：V2 关键词命中 → 扣分项而非否决。"""
    a = Stub(raw_text="该标的存在买卖不破租赁，承租方租期至2039年。")
    r = evaluate(a, RS, BENCH_SHENZHEN)
    assert "V2" in [h["code"] for h in r.penalty_hits]
    assert "V2" not in [h["code"] for h in r.veto_hits]


def test_veto_zero_score_and_grade_c():
    r = evaluate(Stub(land_nature="collective"), RS, BENCH_SHENZHEN)
    assert r.status == "vetoed" and r.grade == "C" and r.total_score == 0.0
    assert r.score_detail.get("vetoed") is True
    assert not r.score_detail.get("dimensions"), "否决标的不应产出维度得分"


# ==================================================================== 3. 租金测算
def test_rent_formula_arithmetic():
    """手工对账净租售比公式（业务修正版：含租金开票税点）。

    净 = 毛租金 − 房产税(企业名下) − 租金开票税点 − 土地使用税 − 修缮 − 空置预留
    """
    a = Stub(area_sqm=1000.0, land_area_sqm=1000.0,
             rent_per_sqm_month=30.0, start_price=1_000_000.0)
    d = compute_rent(a, RS, BENCH_SHENZHEN)

    gross = 30.0 * 1000 * 12                      # 360,000
    prop_tax = gross * 0.12                       # 43,200
    rent_vat = gross * 0.05                       # 18,000
    land_tax = 6.0 * 1000                         # 6,000
    maint = gross * 0.05                          # 18,000
    vacancy = gross * 0.10                        # 36,000
    net = gross - prop_tax - rent_vat - land_tax - maint - vacancy   # 238,800
    ratio = net / 1_000_000

    assert d["computable"] is True
    assert abs(d["gross_annual_rent"] - gross) < 0.01
    assert abs(d["annual_property_tax"] - prop_tax) < 0.01
    assert abs(d["annual_rent_vat"] - rent_vat) < 0.01
    assert abs(d["annual_land_tax"] - land_tax) < 0.01
    assert abs(d["annual_maintenance"] - maint) < 0.01
    assert abs(d["vacancy_reserve"] - vacancy) < 0.01
    assert abs(d["net_annual_income"] - net) < 0.01
    assert abs(d["net_rent_ratio"] - ratio) < 1e-9
    assert d["net_rent_ratio_pct"] == f"{ratio * 100:.2f}%"


def test_rent_owner_is_company_switch():
    """个人名下住宅免征从租房产税 → 房产税项不计扣，净收益更高。"""
    kw = dict(area_sqm=1000.0, land_area_sqm=1000.0,
              rent_per_sqm_month=30.0, start_price=1_000_000.0)
    company = compute_rent(Stub(**kw, owner_is_company=True), RS, BENCH_SHENZHEN)
    personal = compute_rent(Stub(**kw, owner_is_company=False), RS, BENCH_SHENZHEN)
    assert company["annual_property_tax"] > 0
    assert personal["annual_property_tax"] == 0.0
    assert personal["net_annual_income"] > company["net_annual_income"]


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


def test_clearance_dimension_levels():
    """清场与占有维度：空置满分 30，长期租约锁 4 级 → 0 分。"""
    clean = evaluate(Stub(area_sqm=1000.0, start_price=1_000_000.0,
                          appraisal_price=1_000_000.0, occupied=False),
                     RS, BENCH_SHENZHEN)
    assert clean.score_detail["dimensions"]["clearance"]["score"] == 30.0

    leased = evaluate(Stub(area_sqm=1000.0, start_price=1_000_000.0,
                           appraisal_price=1_000_000.0, lease_status="long_term"),
                      RS, BENCH_SHENZHEN)
    assert leased.score_detail["dimensions"]["clearance"]["score"] == 0.0


def test_clearance_seal_penalty():
    """轮候查封每轮扣 1.5 分，封顶 6 分。"""
    a = Stub(area_sqm=1000.0, start_price=1_000_000.0,
             appraisal_price=1_000_000.0, occupied=False, seal_count=4)
    r = evaluate(a, RS, BENCH_SHENZHEN)
    assert abs(r.score_detail["dimensions"]["clearance"]["score"] - 24.0) < 0.01


def test_holding_cost_dimension():
    """持有成本维度：成本占比越低分越高，占比 20%+ 归零。"""
    cheap = evaluate(Stub(area_sqm=1000.0, start_price=1_000_000.0,
                          appraisal_price=1_000_000.0, tax_owed=0.0),
                     RS, BENCH_SHENZHEN)
    assert cheap.score_detail["dimensions"]["holding_cost"]["score"] == 15.0

    costly = evaluate(Stub(area_sqm=1000.0, start_price=1_000_000.0,
                           appraisal_price=1_000_000.0, tax_owed=250_000.0),
                      RS, BENCH_SHENZHEN)
    assert costly.score_detail["dimensions"]["holding_cost"]["score"] == 0.0


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


# ==================================================================== 7. 债权轨道
def _debt(**kw) -> Stub:
    """构造一个债权标的（默认各项中性）。"""
    base = dict(
        asset_class="debt", start_price=600_000.0,
        debt_principal=1_000_000.0, debt_interest=200_000.0,
        collateral_value=1_500_000.0, debt_start_price=600_000.0,
        guarantee_rank="first", execution_stage="executing",
        debtor_solvency="fair", debt_doc_level="full",
        debt_transferable=True, debt_limitation_ok=True, competing_claims=0,
    )
    base.update(kw)
    return Stub(**base)


def test_debt_track_uses_debt_dimensions():
    """债权标的分流到债权五维，物权维度不得出现。"""
    r = evaluate(_debt(), RS, BENCH_SHENZHEN)
    assert r.status == "scored"
    dims = r.score_detail["dimensions"]
    assert set(dims) == {"coverage", "rank", "execution", "solvency", "documentation"}
    assert r.score_detail["asset_class"] == "debt"
    assert not (dims.keys() & {"price", "clearance", "holding_cost", "rent", "location"})
    assert r.rent_detail == {}, "债权轨道不应产出租金测算"


def test_debt_has_no_veto():
    """业务确认：债权不适用一票否决，任何情形都应进入量化打分。"""
    # 极端不利：无担保 + 覆盖不足 + 终本 + 已破产 + 凭证薄弱
    a = _debt(guarantee_rank="none", collateral_value=100_000.0,
              execution_stage="failed", debtor_solvency="bankrupt",
              debt_doc_level="weak", competing_claims=9)
    r = evaluate(a, RS, BENCH_SHENZHEN)
    assert r.veto_hits == [], "债权不得产生一票否决"
    assert r.status == "scored"
    assert r.total_score < 30.0, "极端不利债权应当得分很低"


def test_debt_coverage_is_decisive():
    """覆盖倍数是第一命门：覆盖越充分，coverage 维度得分越高。"""
    under = evaluate(_debt(collateral_value=600_000.0), RS, BENCH_SHENZHEN)
    over = evaluate(_debt(collateral_value=3_600_000.0), RS, BENCH_SHENZHEN)
    assert under.score_detail["dimensions"]["coverage"]["score"] < \
        over.score_detail["dimensions"]["coverage"]["score"]
    # 覆盖 3 倍 → 锚点满分 30
    assert over.score_detail["dimensions"]["coverage"]["score"] == 30.0


def test_debt_rank_scoring():
    """首位 vs 末位顺位应有明显分差。"""
    first = evaluate(_debt(guarantee_rank="first"), RS, BENCH_SHENZHEN)
    none = evaluate(_debt(guarantee_rank="none"), RS, BENCH_SHENZHEN)
    assert first.score_detail["dimensions"]["rank"]["score"] == 25.0
    assert none.score_detail["dimensions"]["rank"]["score"] == 2.0


def test_debt_competing_claims_penalty():
    """竞争者每多 1 家扣 1.5 分，封顶 6 分。"""
    a = _debt(competing_claims=10)
    r = evaluate(a, RS, BENCH_SHENZHEN)
    assert abs(r.score_detail["dimensions"]["rank"]["score"] - 19.0) < 0.01


def test_debt_execution_stage_scores():
    settled = evaluate(_debt(execution_stage="settled"), RS, BENCH_SHENZHEN)
    failed = evaluate(_debt(execution_stage="failed"), RS, BENCH_SHENZHEN)
    assert settled.score_detail["dimensions"]["execution"]["score"] == 20.0
    assert failed.score_detail["dimensions"]["execution"]["score"] == 2.0


def test_debt_weight_totals_are_100():
    r = evaluate(_debt(), RS, BENCH_SHENZHEN)
    assert r.score_detail["weights_total"] == 100.0
    dims = r.score_detail["dimensions"]
    assert abs(sum(d["weight"] for d in dims.values()) - 100.0) < 0.011


def test_debt_tags_are_debt_specific():
    a = _debt(guarantee_rank="first", execution_stage="auctioning",
              debtor_solvency="good", debt_doc_level="full")
    r = evaluate(a, RS, BENCH_SHENZHEN)
    adv = {t["code"] for t in r.advantage_tags}
    assert "d_first_rank" in adv
    assert "d_execution_advanced" in adv
    assert "d_solvent_debtor" in adv
    assert "d_docs_complete" in adv

    bad = evaluate(_debt(guarantee_rank="none", collateral_value=500_000.0,
                         execution_stage="failed", competing_claims=5,
                         debt_doc_level="weak"), RS, BENCH_SHENZHEN)
    risk = {t["code"] for t in bad.risk_tags}
    assert "d_no_guarantee" in risk
    assert "d_under_covered" in risk
    assert "d_execution_stalled" in risk
    assert "d_crowded_creditors" in risk
    assert "d_weak_docs" in risk


def test_debt_result_is_reproducible():
    a = _debt(guarantee_rank="second", competing_claims=2)
    r1 = evaluate(a, RS, BENCH_SHENZHEN)
    r2 = evaluate(a, RS, BENCH_SHENZHEN)
    assert r1.total_score == r2.total_score
    assert r1.score_detail == r2.score_detail
    assert r1.conclusion == r2.conclusion


def test_debt_conclusion_mentions_track():
    r = evaluate(_debt(), RS, BENCH_SHENZHEN)
    assert "债权轨道" in r.conclusion


def test_dual_track_weights_independent():
    """改动债权权重不得影响物权得分（两套参数完全隔离）。"""
    import copy as _copy
    from app.core.scoring import score_all as _score_all
    from app.core.benchmark import push_down_benchmark

    prop = Stub(area_sqm=1000.0, rent_per_sqm_month=30.0,
                start_price=600_000.0, appraisal_price=1_000_000.0)
    rs2 = make_ruleset()
    rs2.weights_debt = {k: 0.0 for k in rs2.weights_debt}   # 把债权权重全清零
    r_base = evaluate(prop, RS, BENCH_SHENZHEN)
    r_mod = evaluate(prop, rs2, BENCH_SHENZHEN)
    assert r_base.total_score == r_mod.total_score, "债权权重变化污染了物权得分"

    # 反向：物权权重清零不影响债权
    rs3 = make_ruleset()
    rs3.weights = {k: 0.0 for k in rs3.weights}
    d = _debt()
    d_base = evaluate(d, RS, BENCH_SHENZHEN)
    d_mod = evaluate(d, rs3, BENCH_SHENZHEN)
    assert d_base.total_score == d_mod.total_score, "物权权重变化污染了债权得分"


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
