"""领域常量：枚举、默认参数、种子规则。

设计原则（对应 PRD 验收标准第 3、6 条）：
* 所有评分/风控参数集中在此作为**出厂默认值**，运行时由数据库 SysConfig 覆盖，
  后台可视化修改，无需改代码。
* 引擎只认这些常量 + 配置，不依赖任何随机数，因此同一输入必然得到同一结果。
"""
from __future__ import annotations

# ============================================================ 资产类型
ASSET_TYPES: dict[str, str] = {
    "residential": "住宅",
    "commercial": "商业物业",
    "industrial": "工业厂房",
    "land": "土地",
    "equipment": "设备",
    "other": "其他",
}

# ============================================================ 采集来源（PRD 二：五大数据源）
PLATFORMS: dict[str, str] = {
    "alipaimai": "阿里拍卖司法平台",
    "jd_auction": "京东司法拍卖平台",
    "lawsuit_assets": "全国诉讼资产网",
    "ggzy": "地方公共资源交易中心",
    "amc": "AMC公开挂牌资产",
    "manual": "手工录入 / 批量导入",
}

# ============================================================ 土地性质
LAND_NATURES: dict[str, str] = {
    "granted": "出让",
    "allocated": "划拨",
    "collective": "集体",
    "unknown": "未载明",
}

# ============================================================ 租赁状态
LEASE_STATUSES: dict[str, str] = {
    "none": "无租赁",
    "normal": "普通租赁",
    "long_term": "长期有效租约",
    "sale_not_break": "买卖不破租赁",
    "unknown": "未载明",
}

# ============================================================ 报废/闲置状态（一票否决 V5）
SCRAP_STATUSES: dict[str, str] = {
    "normal": "正常可用",
    "equipment_scrapped": "设备老旧报废",
    "land_no_value": "土地无开发价值",
    "property_unusable": "物业无法正常使用",
}

# ============================================================ 合规等级（权属补充维度）
COMPLIANCE_LEVELS: dict[str, str] = {
    "full": "证载用途与现状一致、手续齐全",
    "partial": "存在轻度不一致但可补正",
    "none": "手续缺失 / 无法补正",
}

# ============================================================ 城市能级
CITY_TIERS: dict[str, str] = {
    "tier1": "一线城市",
    "new_tier1": "新一线城市",
    "tier2": "二线城市",
    "tier3": "三线城市",
    "tier4": "四线城市",
    "tier5": "五线及以下",
}

# ============================================================ 拍卖轮次
AUCTION_ROUNDS: dict[str, str] = {
    "first": "一拍",
    "second": "二拍",
    "third": "三拍/变卖",
    "unknown": "未载明",
}

# ============================================================ 标的等级
GRADES: dict[str, str] = {
    "A": "A类优质标的",
    "B": "B类观察标的",
    "C": "C类淘汰标的",
}

# ============================================================ 标的状态
ASSET_STATUSES: dict[str, str] = {
    "pending": "待评估",
    "scored": "已评分",
    "vetoed": "已否决",
    "archived": "已归档",
}

# ============================================================ 五维权重（出厂默认，PRD 模块3）
DEFAULT_WEIGHTS: dict[str, float] = {
    "price": 30.0,      # 价格折价维度
    "rent": 30.0,       # 净租售比现金流维度（核心）
    "legal": 20.0,      # 产权司法风险维度
    "location": 15.0,   # 区位流通性维度
    "ownership": 5.0,   # 权属合规补充维度
}

DIMENSION_LABELS: dict[str, str] = {
    "price": "价格折价维度",
    "rent": "净租售比现金流维度",
    "legal": "产权司法风险维度",
    "location": "区位流通性维度",
    "ownership": "权属合规补充维度",
}

# ============================================================ 分级阈值（PRD 模块4）
DEFAULT_GRADE_THRESHOLDS: dict[str, float] = {"A": 80.0, "B": 60.0}

# ============================================================ 打分锚点表
# 格式：[输入值, 得分]，按输入值升序，区间内线性插值，两端钳制。
# 后台可直接编辑这张表 —— 这是"结果可复现"的关键：规则是数据，不是代码。

# 价格折价率 → 得分（满分 30）
DEFAULT_PRICE_ANCHORS: list[list[float]] = [
    [-9.0, 0.0],    # 负折价（溢价）→ 0 分
    [0.0, 0.0],     # 无折价 → 0 分（PRD：高价无折价标的低分）
    [0.10, 9.0],
    [0.20, 15.0],
    [0.30, 21.0],
    [0.40, 27.0],
    [0.50, 30.0],
    [9.0, 30.0],
]

# 净租售比 → 得分（满分 30）
# PRD：≥5% 满分；4%-5% 梯度扣分；＜4% 大幅扣分预警
DEFAULT_RENT_ANCHORS: list[list[float]] = [
    [-9.0, 0.0],
    [0.0, 0.0],
    [0.02, 6.0],
    [0.03, 12.0],
    [0.04, 18.0],   # 4% 恰好是"大幅扣分"的分界
    [0.045, 24.0],
    [0.05, 30.0],
    [9.0, 30.0],
]

# 土地剩余年限 → 得分（满分 2，属权属合规维度）
DEFAULT_LAND_YEAR_ANCHORS: list[list[float]] = [
    [0.0, 0.0],
    [10.0, 0.2],
    [20.0, 0.7],
    [30.0, 1.2],
    [40.0, 1.6],
    [70.0, 2.0],
]

# ============================================================ 成本与测算系数（PRD 四）
# 净租售比 =（预估年毛租金 - 年度税费 - 年度修缮运维费 - 空置损耗预留）÷ 标的成交预估价值
DEFAULT_COST_PARAMS: dict[str, float] = {
    "property_tax_rate": 0.12,        # 房产税（从租计征）占年毛租金比例
    "land_use_tax_per_sqm": 6.0,      # 土地使用税（元/㎡/年），区域均值兜底
    "maintenance_ratio": 0.05,        # 年度修缮运维费占年毛租金比例
    "vacancy_ratio": 0.10,            # 空置损耗预留（PRD：统一预留 10%）
    "deal_value_ratio": 1.0,          # 成交预估价值 = 起拍价 × 该系数（法拍惯例按底价成交）
}

# 无区域基准数据时，按资产类型的租金兜底值（元/㎡/月）
DEFAULT_RENT_PER_SQM_MONTH: dict[str, float] = {
    "residential": 30.0,
    "commercial": 55.0,
    "industrial": 18.0,
    "land": 6.0,
    "equipment": 0.0,
    "other": 20.0,
}

# ============================================================ 产权司法风险扣分系数（PRD 维度3，满分 20）
DEFAULT_LEGAL_PENALTY: dict[str, float] = {
    "mortgage_per": 2.0,   "mortgage_cap": 6.0,
    "seal_per": 4.0,       "seal_cap": 12.0,
    "lawsuit_per": 1.5,    "lawsuit_cap": 6.0,
    "dispute_per": 1.0,    "dispute_cap": 3.0,
}

# ============================================================ 区位流通性（满分 15）
DEFAULT_LOCATION_PARAMS: dict[str, object] = {
    "city_tier_scores": {
        "tier1": 6.0, "new_tier1": 5.0, "tier2": 4.0,
        "tier3": 3.0, "tier4": 2.0, "tier5": 1.0,
    },
    "industry_support_max": 3.0,   # 产业配套成熟度 0~1 → 0~3
    "rental_demand_max": 3.0,      # 区域出租需求热度 0~1 → 0~3
    "turnover_max": 3.0,           # 同类资产转手成交率 0~1 → 0~3
}

# ============================================================ 权属合规补充（满分 5）
DEFAULT_OWNERSHIP_PARAMS: dict[str, object] = {
    "land_years_max": 2.0,                 # 土地剩余年限，见 DEFAULT_LAND_YEAR_ANCHORS
    "no_implicit_coownership_score": 1.5,  # 无隐性共有产权提示
    "compliance_scores": {"full": 1.5, "partial": 0.75, "none": 0.0},  # 资产合规性
}

# ============================================================ 标签阈值
DEFAULT_TAG_THRESHOLDS: dict[str, float] = {
    "high_discount": 0.30,   # 高折价线
    "low_discount": 0.10,    # 低折价线
    "rent_pass": 0.05,       # 净租售比达标线
    "rent_warn": 0.04,       # 净租售比预警线
    "seal_many": 2,          # 多轮查封起点
    "mortgage_many": 2,      # 多抵押起点
    "lawsuit_many": 3,       # 涉诉较多起点
    "land_years_short": 20,  # 年限不足线
    "deadline_days": 7,      # 临近截止天数
    "turnover_poor": 0.30,   # 区域流动性差线
    "big_arrears": 500000,   # 大额欠费预警总额
}

# ============================================================ 标签登记表
# code / 名称 / 类型 / 判定说明（判定逻辑实现在 core/tags.py，可开关注）
TAG_REGISTRY: list[dict[str, str]] = [
    # ---- 优势标签（PRD 模块5）
    {"code": "high_discount", "name": "高折价", "kind": "advantage",
     "desc": "折价率 ≥ 高折价线（默认 30%）"},
    {"code": "rent_pass", "name": "净租售达标", "kind": "advantage",
     "desc": "净租售比 ≥ 达标线（默认 5%）"},
    {"code": "clean_judicial", "name": "无抵押无查封", "kind": "advantage",
     "desc": "抵押数量为 0 且轮候查封为 0"},
    {"code": "clean_title", "name": "产权清晰", "kind": "advantage",
     "desc": "非集体/划拨用地，可办不动产登记且无转让限制"},
    {"code": "good_location", "name": "区位优质", "kind": "advantage",
     "desc": "区位流通性维度得分率 ≥ 80%"},
    {"code": "granted_land", "name": "出让用地", "kind": "advantage",
     "desc": "土地性质为出让"},
    {"code": "no_lease", "name": "无租赁", "kind": "advantage",
     "desc": "公告载明无租赁"},
    {"code": "vacant", "name": "空置可交付", "kind": "advantage",
     "desc": "公告载明空置、可正常交付"},
    {"code": "low_judicial_risk", "name": "司法风险低", "kind": "advantage",
     "desc": "产权司法风险维度得分率 ≥ 80%"},

    # ---- 风险标签（PRD 模块5）
    {"code": "has_lease", "name": "有租赁", "kind": "risk",
     "desc": "存在租赁状态（普通/长期/买卖不破租赁）"},
    {"code": "occupied", "name": "有占用", "kind": "risk",
     "desc": "公告载明被占用"},
    {"code": "arrears", "name": "欠税提示", "kind": "risk",
     "desc": "存在欠税 / 欠缴费用记录"},
    {"code": "many_seals", "name": "多轮查封", "kind": "risk",
     "desc": "轮候查封数量 ≥ 多轮线（默认 2）"},
    {"code": "land_years_short", "name": "年限不足", "kind": "risk",
     "desc": "土地剩余年限 < 年限不足线（默认 20 年）"},
    {"code": "low_discount", "name": "低折价", "kind": "risk",
     "desc": "折价率 < 低折价线（默认 10%）"},
    {"code": "rent_fail", "name": "净租售比不达标", "kind": "risk",
     "desc": "净租售比 < 预警线（默认 4%），现金流不成立"},
    {"code": "many_mortgages", "name": "多抵押", "kind": "risk",
     "desc": "抵押数量 ≥ 多抵押线（默认 2）"},
    {"code": "many_lawsuits", "name": "涉诉较多", "kind": "risk",
     "desc": "涉诉案件数 ≥ 涉诉线（默认 3）"},
    {"code": "collective_land", "name": "集体用地", "kind": "risk",
     "desc": "土地性质为集体用地"},
    {"code": "allocated_land", "name": "划拨用地", "kind": "risk",
     "desc": "土地性质为划拨用地"},
    {"code": "transfer_restricted", "name": "限制转让", "kind": "risk",
     "desc": "公告载明资产限制转让"},
    {"code": "no_registration", "name": "无法办证", "kind": "risk",
     "desc": "无法办理不动产登记"},
    {"code": "big_arrears", "name": "大额欠费", "kind": "risk",
     "desc": "欠税+闲置费+工程欠款 总计 ≥ 大额线（默认 50 万）"},
    {"code": "coownership_dispute", "name": "共有产权争议", "kind": "risk",
     "desc": "存在共有产权无法统一确权"},
    {"code": "irreversible_seal", "name": "不可解除查封", "kind": "risk",
     "desc": "存在不可解除的限制性查封"},
    {"code": "scrapped", "name": "资产闲置报废", "kind": "risk",
     "desc": "设备报废 / 土地无开发价值 / 物业无法使用"},
    {"code": "deadline_soon", "name": "临近截止", "kind": "risk",
     "desc": "距挂牌截止日 < 临近天数（默认 7 天）"},
    {"code": "poor_liquidity", "name": "区域流动性差", "kind": "risk",
     "desc": "同类资产转手成交率 < 流动性差线（默认 30%）"},
    {"code": "implicit_coownership", "name": "隐性共有产权提示", "kind": "risk",
     "desc": "公告存在隐性共有产权提示"},
    {"code": "procedure_incomplete", "name": "手续不全", "kind": "risk",
     "desc": "合规等级为手续缺失 / 无法补正"},
]

TAG_BY_CODE: dict[str, dict[str, str]] = {t["code"]: t for t in TAG_REGISTRY}

# ============================================================ 一票否决规则种子（PRD 模块2）
# 判定模型：结构化字段条件（field_conditions） OR 关键词命中（keywords）。
# V3 默认关闭关键词命中 —— 单凭"欠税"二字会误杀，必须配合金额阈值，
# 该偏好可在后台开关，理由是工程判断而非业务裁剪。
VETO_RULE_SEED: list[dict] = [
    {
        "code": "V1",
        "name": "产权硬缺陷",
        "priority": 10,
        "enabled": True,
        "keyword_enabled": True,
        "description": "集体用地、划拨用地无法补手续、资产限制转让、无法办理不动产登记",
        "field_conditions": [
            {"field": "land_nature", "op": "eq", "value": "collective",
             "label": "土地性质为集体用地"},
            {"field": "land_nature", "op": "eq", "value": "allocated",
             "and": [{"field": "can_supplement_procedure", "op": "is_false"}],
             "label": "划拨用地且无法补办手续"},
            {"field": "transfer_restricted", "op": "is_true",
             "label": "公告载明资产限制转让"},
            {"field": "registration_ok", "op": "is_false",
             "label": "无法办理不动产登记"},
        ],
        "keywords": ["集体用地", "集体土地", "集体所有土地", "划拨用地无法补办",
                     "无法办理不动产登记", "不能办理产权登记", "限制转让", "禁止转让"],
    },
    {
        "code": "V2",
        "name": "清场风险极高",
        "priority": 20,
        "enabled": True,
        "keyword_enabled": True,
        "description": "公告标注长期有效租约、买卖不破租赁、无法清退占用",
        "field_conditions": [
            {"field": "lease_status", "op": "in", "value": ["long_term", "sale_not_break"],
             "label": "存在长期有效租约或买卖不破租赁"},
            {"field": "occupied", "op": "is_true",
             "and": [{"field": "can_clear", "op": "is_false"}],
             "label": "被占用且无法清场"},
        ],
        "keywords": ["买卖不破租赁", "长期有效租约", "长期租赁合同", "租期二十年",
                     "无法清退", "拒不腾退", "无法清场", "占用无法清场"],
    },
    {
        "code": "V3",
        "name": "大额欠费风险",
        "priority": 30,
        "enabled": True,
        "keyword_enabled": False,   # 见上方说明：必须靠金额阈值，避免误杀
        "description": "存在大额欠税、土地闲置费、工程欠款，大概率吞噬资产收益",
        "field_conditions": [
            {"field": "tax_owed", "op": "gte", "value": 100000,
             "label": "欠税额 ≥ 10 万元"},
            {"field": "land_idle_fee", "op": "gte", "value": 50000,
             "label": "土地闲置费 ≥ 5 万元"},
            {"field": "construction_arrears", "op": "gte", "value": 100000,
             "label": "工程欠款 ≥ 10 万元"},
            {"field": "total_arrears", "op": "gte", "value": 500000,
             "label": "欠费总额 ≥ 50 万元"},
        ],
        "keywords": ["大额欠税", "欠缴土地闲置费", "拖欠工程款", "巨额欠费"],
    },
    {
        "code": "V4",
        "name": "权属争议",
        "priority": 40,
        "enabled": True,
        "keyword_enabled": True,
        "description": "共有产权无法统一确权、存在不可解除的限制性查封",
        "field_conditions": [
            {"field": "co_ownership_dispute", "op": "is_true",
             "label": "共有产权无法统一确权"},
            {"field": "irreversible_seal", "op": "is_true",
             "label": "存在不可解除的限制性查封"},
        ],
        "keywords": ["共有产权无法确权", "权属争议", "权属不明", "产权存在争议",
                     "无法分割", "不可解除的查封"],
    },
    {
        "code": "V5",
        "name": "资产闲置报废",
        "priority": 50,
        "enabled": True,
        "keyword_enabled": True,
        "description": "设备老旧报废、土地无开发价值、物业无法正常使用",
        "field_conditions": [
            {"field": "scrap_status", "op": "in",
             "value": ["equipment_scrapped", "land_no_value", "property_unusable"],
             "label": "资产处于报废 / 无价值 / 不可用状态"},
        ],
        "keywords": ["已报废", "设备报废", "无开发价值", "无法正常使用", "危房", "倒塌"],
    },
]

# ============================================================ 区域大数据基准种子
# 对应 PRD 四-2「税费、租金调用对应区域大数据均值」。
# 生产环境应由数据团队定期刷新；一期内置样本城市，后台可手工维护。
REGION_BENCHMARK_SEED: list[dict] = [
    # 城市, 区县, 资产类型, 租金(元/㎡/月), 土地使用税(元/㎡/年), 房产税率, 空置率, 维保费率, 城市能级, 产业配套, 出租需求, 转手成交率
    {"province": "广东省", "city": "深圳市", "district": "南山区", "asset_type": "residential",
     "rent_per_sqm_month": 78.0, "land_use_tax_per_sqm": 18.0, "property_tax_rate": 0.12,
     "vacancy_ratio": 0.08, "maintenance_ratio": 0.04, "city_tier": "tier1",
     "industry_support_ratio": 0.95, "rental_demand_ratio": 0.94, "turnover_ratio": 0.88},
    {"province": "广东省", "city": "深圳市", "district": "南山区", "asset_type": "commercial",
     "rent_per_sqm_month": 165.0, "land_use_tax_per_sqm": 18.0, "property_tax_rate": 0.12,
     "vacancy_ratio": 0.10, "maintenance_ratio": 0.05, "city_tier": "tier1",
     "industry_support_ratio": 0.95, "rental_demand_ratio": 0.92, "turnover_ratio": 0.82},
    {"province": "广东省", "city": "广州市", "district": "天河区", "asset_type": "residential",
     "rent_per_sqm_month": 62.0, "land_use_tax_per_sqm": 16.0, "property_tax_rate": 0.12,
     "vacancy_ratio": 0.09, "maintenance_ratio": 0.04, "city_tier": "tier1",
     "industry_support_ratio": 0.93, "rental_demand_ratio": 0.92, "turnover_ratio": 0.86},
    {"province": "广东省", "city": "东莞市", "district": "松山湖", "asset_type": "industrial",
     "rent_per_sqm_month": 26.0, "land_use_tax_per_sqm": 8.0, "property_tax_rate": 0.12,
     "vacancy_ratio": 0.10, "maintenance_ratio": 0.05, "city_tier": "new_tier1",
     "industry_support_ratio": 0.90, "rental_demand_ratio": 0.86, "turnover_ratio": 0.68},
    {"province": "江苏省", "city": "苏州市", "district": "工业园区", "asset_type": "industrial",
     "rent_per_sqm_month": 30.0, "land_use_tax_per_sqm": 9.0, "property_tax_rate": 0.12,
     "vacancy_ratio": 0.09, "maintenance_ratio": 0.05, "city_tier": "new_tier1",
     "industry_support_ratio": 0.93, "rental_demand_ratio": 0.88, "turnover_ratio": 0.72},
    {"province": "江苏省", "city": "南京市", "district": "建邺区", "asset_type": "commercial",
     "rent_per_sqm_month": 96.0, "land_use_tax_per_sqm": 12.0, "property_tax_rate": 0.12,
     "vacancy_ratio": 0.11, "maintenance_ratio": 0.05, "city_tier": "new_tier1",
     "industry_support_ratio": 0.88, "rental_demand_ratio": 0.85, "turnover_ratio": 0.74},
    {"province": "河南省", "city": "郑州市", "district": "金水区", "asset_type": "residential",
     "rent_per_sqm_month": 34.0, "land_use_tax_per_sqm": 10.0, "property_tax_rate": 0.12,
     "vacancy_ratio": 0.11, "maintenance_ratio": 0.05, "city_tier": "new_tier1",
     "industry_support_ratio": 0.82, "rental_demand_ratio": 0.83, "turnover_ratio": 0.70},
    {"province": "河南省", "city": "郑州市", "district": "金水区", "asset_type": "commercial",
     "rent_per_sqm_month": 72.0, "land_use_tax_per_sqm": 10.0, "property_tax_rate": 0.12,
     "vacancy_ratio": 0.13, "maintenance_ratio": 0.05, "city_tier": "new_tier1",
     "industry_support_ratio": 0.82, "rental_demand_ratio": 0.80, "turnover_ratio": 0.66},
    {"province": "河南省", "city": "洛阳市", "district": "涧西区", "asset_type": "industrial",
     "rent_per_sqm_month": 14.0, "land_use_tax_per_sqm": 5.0, "property_tax_rate": 0.12,
     "vacancy_ratio": 0.14, "maintenance_ratio": 0.06, "city_tier": "tier3",
     "industry_support_ratio": 0.62, "rental_demand_ratio": 0.58, "turnover_ratio": 0.42},
    {"province": "河南省", "city": "洛阳市", "district": "洛龙区", "asset_type": "land",
     "rent_per_sqm_month": 4.5, "land_use_tax_per_sqm": 4.0, "property_tax_rate": 0.12,
     "vacancy_ratio": 0.18, "maintenance_ratio": 0.03, "city_tier": "tier3",
     "industry_support_ratio": 0.60, "rental_demand_ratio": 0.52, "turnover_ratio": 0.38},
    {"province": "四川省", "city": "成都市", "district": "高新区", "asset_type": "commercial",
     "rent_per_sqm_month": 88.0, "land_use_tax_per_sqm": 11.0, "property_tax_rate": 0.12,
     "vacancy_ratio": 0.12, "maintenance_ratio": 0.05, "city_tier": "new_tier1",
     "industry_support_ratio": 0.87, "rental_demand_ratio": 0.86, "turnover_ratio": 0.73},
    {"province": "四川省", "city": "成都市", "district": "龙泉驿区", "asset_type": "industrial",
     "rent_per_sqm_month": 22.0, "land_use_tax_per_sqm": 7.0, "property_tax_rate": 0.12,
     "vacancy_ratio": 0.13, "maintenance_ratio": 0.05, "city_tier": "new_tier1",
     "industry_support_ratio": 0.80, "rental_demand_ratio": 0.76, "turnover_ratio": 0.62},
    {"province": "山东省", "city": "青岛市", "district": "黄岛区", "asset_type": "industrial",
     "rent_per_sqm_month": 18.0, "land_use_tax_per_sqm": 6.0, "property_tax_rate": 0.12,
     "vacancy_ratio": 0.14, "maintenance_ratio": 0.06, "city_tier": "new_tier1",
     "industry_support_ratio": 0.74, "rental_demand_ratio": 0.70, "turnover_ratio": 0.55},
    {"province": "辽宁省", "city": "沈阳市", "district": "铁西区", "asset_type": "industrial",
     "rent_per_sqm_month": 12.0, "land_use_tax_per_sqm": 4.0, "property_tax_rate": 0.12,
     "vacancy_ratio": 0.18, "maintenance_ratio": 0.06, "city_tier": "tier2",
     "industry_support_ratio": 0.58, "rental_demand_ratio": 0.50, "turnover_ratio": 0.35},
    {"province": "黑龙江省", "city": "鹤岗市", "district": "向阳区", "asset_type": "residential",
     "rent_per_sqm_month": 9.0, "land_use_tax_per_sqm": 2.0, "property_tax_rate": 0.12,
     "vacancy_ratio": 0.25, "maintenance_ratio": 0.07, "city_tier": "tier5",
     "industry_support_ratio": 0.20, "rental_demand_ratio": 0.18, "turnover_ratio": 0.12},
]

# ============================================================ 配置键
CONFIG_KEYS = {
    "weights": DEFAULT_WEIGHTS,
    "grade_thresholds": DEFAULT_GRADE_THRESHOLDS,
    "price_anchors": DEFAULT_PRICE_ANCHORS,
    "rent_anchors": DEFAULT_RENT_ANCHORS,
    "land_year_anchors": DEFAULT_LAND_YEAR_ANCHORS,
    "cost_params": DEFAULT_COST_PARAMS,
    "legal_penalty": DEFAULT_LEGAL_PENALTY,
    "location_params": DEFAULT_LOCATION_PARAMS,
    "ownership_params": DEFAULT_OWNERSHIP_PARAMS,
    "tag_thresholds": DEFAULT_TAG_THRESHOLDS,
    "tag_enabled": {t["code"]: True for t in TAG_REGISTRY},
    "default_rent_per_sqm_month": DEFAULT_RENT_PER_SQM_MONTH,
    "disclaimer": None,   # None → 用 config.DEFAULT_DISCLAIMER
}

CONFIG_GROUP_LABELS: dict[str, str] = {
    "weights": "五维评分权重",
    "grade_thresholds": "A/B/C 分级阈值",
    "price_anchors": "价格折价打分锚点",
    "rent_anchors": "净租售比打分锚点",
    "land_year_anchors": "土地剩余年限打分锚点",
    "cost_params": "租金/税费/损耗系数",
    "legal_penalty": "产权司法风险扣分系数",
    "location_params": "区位流通性子项分值",
    "ownership_params": "权属合规补充子项分值",
    "tag_thresholds": "标签触发阈值",
    "tag_enabled": "标签开关",
    "default_rent_per_sqm_month": "各类型租金兜底值",
    "disclaimer": "报告免责声明文案",
}
