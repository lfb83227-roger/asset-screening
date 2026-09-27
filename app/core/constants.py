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

# ============================================================ 资产大类（双轨制）
# 物权（property）：买的是资产本身 → 关心"值多少、能不能拿到手、还要付多少"。
# 债权（debt）    ：买的是收钱的权利 → 关心"能不能收回、收回多快"。
# 两条轨道使用完全不同的评分维度与否决规则，详见 core/scoring_property.py
# 与 core/scoring_debt.py。
ASSET_CLASSES: dict[str, str] = {
    "property": "物权（资产本身）",
    "debt": "债权（收钱的权利）",
}

# ============================================================ 担保顺位（债权）
GUARANTEE_RANKS: dict[str, str] = {
    "first": "首封 / 一顺位抵押",
    "second": "二顺位及以后抵押",
    "other": "其他担保方式",
    "none": "无担保 / 纯信用债权",
    "unknown": "未载明",
}

# ============================================================ 执行进展（债权）
EXECUTION_STAGES: dict[str, str] = {
    "settled": "已回款 / 已执结",
    "auctioning": "抵押物已挂拍",
    "executing": "执行中",
    "judged": "已判决未申请执行",
    "litigating": "诉讼 / 仲裁中",
    "failed": "终本 / 流拍未能处置",
    "unknown": "未载明",
}

# ============================================================ 债务人偿付能力（债权）
DEBTOR_SOLVENCY: dict[str, str] = {
    "good": "有足额可供执行财产",
    "fair": "有部分可供执行财产",
    "poor": "基本无偿付能力",
    "bankrupt": "已破产 / 已进入清算",
    "unknown": "未载明",
}

# ============================================================ 债权凭证完整性
DEBT_DOC_LEVELS: dict[str, str] = {
    "full": "判决书 + 合同 + 借据凭证齐全",
    "partial": "部分材料缺失（如需补充送达证明）",
    "weak": "仅借条 / 权属模糊",
    "unknown": "未载明",
}

# ============================================================ 标的状态
ASSET_STATUSES: dict[str, str] = {
    "pending": "待评估",
    "scored": "已评分",
    "vetoed": "已否决",
    "archived": "已归档",
}

# ============================================================ 双轨权重
# 物权（业务确认：价格 30 / 清场占有 30 / 持有成本 15 / 租金 15 / 区位 10）
# * 价格 vs 清场占有同为最大权重 —— 前者是"买得便宜"，后者是"拿得到手"，
#   实务中任一环节出问题都会让整笔投资失败，故并重。
# * 原「产权司法 20」按业务要求拆成两块：能拿到手的难度（清场占有 30）
#   与拿到手还要付多少钱（持有成本 15），互不重复计入。
DEFAULT_WEIGHTS_PROPERTY: dict[str, float] = {
    "price": 30.0,      # 评估基准：双基准交叉验证 + 估值时点新鲜度
    "clearance": 30.0,  # 清场与占有：占用类型、案外人占用、清场难度、查封
    "holding_cost": 15.0,  # 持有成本：税费 + 各类欠费
    "rent": 15.0,       # 租金现金流：净租售比（算法已按业务口径修正）
    "location": 10.0,   # 区位流通性
}

DIMENSION_LABELS_PROPERTY: dict[str, str] = {
    "price": "评估基准维度",
    "clearance": "清场与占有维度",
    "holding_cost": "持有成本维度",
    "rent": "租金现金流维度",
    "location": "区位流通性维度",
}

# 债权（业务确认：覆盖倍数 30 / 顺位 25 / 执行进展 20 / 债务人 15 / 凭证 10）
DEFAULT_WEIGHTS_DEBT: dict[str, float] = {
    "coverage": 30.0,     # 抵押物覆盖倍数（第一命门）
    "rank": 25.0,         # 担保顺位（是否首封）
    "execution": 20.0,    # 执行进展
    "solvency": 15.0,     # 债务人偿付能力
    "documentation": 10.0,  # 债权凭证完整性
}

DIMENSION_LABELS_DEBT: dict[str, str] = {
    "coverage": "抵押物覆盖倍数维度",
    "rank": "担保顺位维度",
    "execution": "执行进展维度",
    "solvency": "债务人偿付能力维度",
    "documentation": "债权凭证完整性维度",
}

# 兼容旧代码的默认权重（= 物权）
DEFAULT_WEIGHTS: dict[str, float] = DEFAULT_WEIGHTS_PROPERTY
DIMENSION_LABELS: dict[str, str] = DIMENSION_LABELS_PROPERTY

# ============================================================ 分级阈值（PRD 模块4）
DEFAULT_GRADE_THRESHOLDS: dict[str, float] = {"A": 80.0, "B": 60.0}

# ============================================================ 打分锚点表
# 格式：[输入值, 得分]，按输入值升序，区间内线性插值，两端钳制。
# 后台可直接编辑这张表 —— 这是"结果可复现"的关键：规则是数据，不是代码。

# ---------------------------------------------------------- 物权·评估基准（满分 30）
# 双基准交叉验证：分别算"对司法评估价折价"与"对市场可比价折价"，
# 取两者加权折价率后查此表；两值分歧过大则触发置信度降级（见 COST_PARAMS）。
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

# ---------------------------------------------------------- 物权·清场与占有（满分 30）
# 按"清场难度等级"查表（等级由占用类型 + 可否清场综合判定，见 scoring_property）
DEFAULT_CLEARANCE_ANCHORS: list[list[float]] = [
    [0.0, 30.0],    # 空置无占用，可直接交付
    [1.0, 24.0],    # 有占用但腾退意愿明确 / 已承诺限期腾退
    [2.0, 15.0],    # 普通租赁占用，需协商解约
    [3.0, 6.0],     # 案外人占用 / 租约存疑
    [4.0, 0.0],     # 无法清场（长期租约、买卖不破租赁）
]

# ---------------------------------------------------------- 物权·持有成本（满分 15）
# 输入 = 欠费与税费总额 ÷ 标的成交预估价值（成本占比），占比越高分越低
DEFAULT_HOLDING_COST_ANCHORS: list[list[float]] = [
    [0.0, 15.0],
    [0.01, 13.0],
    [0.03, 10.5],
    [0.05, 7.5],
    [0.10, 3.0],
    [0.20, 0.0],
    [9.0, 0.0],
]

# ---------------------------------------------------------- 债权·覆盖倍数（满分 30）
# 覆盖倍数 = 抵押物评估价值 ÷ 债权本息总额
DEFAULT_COVERAGE_ANCHORS: list[list[float]] = [
    [0.0, 0.0],
    [0.5, 3.0],     # 覆盖不足一半，回收堪忧
    [1.0, 12.0],    # 勉强覆盖本金
    [1.5, 20.0],
    [2.0, 26.0],
    [3.0, 30.0],    # 覆盖 3 倍以上，安全垫充足
    [9.0, 30.0],
]

# 净租售比 → 得分（满分 30，债权不适用，仅保留供旧配置兼容）
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

# ---------------------------------------------------------- 债权·担保顺位（满分 25）
DEBT_RANK_SCORES: dict[str, float] = {
    "first": 25.0,     # 首封/一顺位：处置价款最先受偿
    "second": 12.0,    # 二顺位：需前顺位清偿后才有余额
    "other": 8.0,
    "none": 2.0,       # 无担保，纯信用
    "unknown": 5.0,    # 未载明从严
}

# ---------------------------------------------------------- 债权·执行进展（满分 20）
DEBT_EXECUTION_SCORES: dict[str, float] = {
    "settled": 20.0,      # 已回款
    "auctioning": 17.0,   # 抵押物已挂拍，回款路径最短
    "executing": 14.0,    # 执行中
    "judged": 10.0,       # 已判决未申请执行
    "litigating": 6.0,    # 诉讼中，周期长
    "failed": 2.0,        # 终本/流拍，处置受阻
    "unknown": 6.0,       # 未载明从严
}

# ---------------------------------------------------------- 债权·债务人偿付能力（满分 15）
DEBT_SOLVENCY_SCORES: dict[str, float] = {
    "good": 15.0,
    "fair": 9.0,
    "poor": 3.0,
    "bankrupt": 0.0,
    "unknown": 5.0,
}

# ---------------------------------------------------------- 债权·凭证完整性（满分 10）
DEBT_DOC_SCORES: dict[str, float] = {
    "full": 10.0,
    "partial": 6.0,
    "weak": 2.0,
    "unknown": 3.0,
}

# ============================================================ 成本与测算系数（PRD 四）
# 净租售比按业务口径修正：
#   净租金 = 年毛租金 − 房产税(企业名下) − 租金开票税点 − 空置预留 − 修缮费
DEFAULT_COST_PARAMS: dict[str, float] = {
    "property_tax_rate": 0.12,        # 房产税（从租计征）占年毛租金比例，企业名下才有
    "rent_vat_rate": 0.05,            # 租金开票税点（增值税及附加）
    "land_use_tax_per_sqm": 6.0,      # 土地使用税（元/㎡/年），区域均值兜底
    "maintenance_ratio": 0.05,        # 年度修缮运维费占年毛租金比例
    "vacancy_ratio": 0.10,            # 空置损耗预留（PRD：统一预留 10%）
    "deal_value_ratio": 1.0,          # 成交预估价值 = 起拍价 × 该系数（法拍惯例按底价成交）
    # ---- 评估基准相关（业务：双基准交叉验证 + 估值时点新鲜度）
    "appraisal_stale_months": 6.0,    # 评估时点超此月数视为"估值陈旧"
    "benchmark_divergence_limit": 0.15,  # 双基准折价率分歧上限，超过则降权
    "divergence_confidence_factor": 0.6,  # 分歧超限时价格维度得分折减系数
    "appraisal_weight": 0.5,          # 司法评估价在综合折价中的权重（其余给市场价）
    "market_min_comp_count": 3,       # 可比案例少于此数则市场基准可信度打折
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
    "holding_cost_high": 0.05,     # 持有成本占比过高线
    "holding_cost_low_rate": 0.5,  # 持有成本维度得分率低于此值 → 打风险标签
    "appraisal_stale_months": 6,   # 估值陈旧线（月）
    "benchmark_divergent": 0.15,   # 双基准分歧线
    "market_min_comp": 3,          # 市场可比案例最少宗数
    "clearance_hard_level": 3,     # 清场困难等级线
    # ---- 债权专用阈值
    "coverage_safe": 1.5,    # 覆盖倍数安全线
    "coverage_short": 1.0,   # 覆盖倍数不足线
    "creditor_many": 3,      # 竞争债权人过多线
}

# ============================================================ 标签登记表
# code / 名称 / 类型 / 判定说明（判定逻辑实现在 core/tags.py，可开关注）
# 前缀 p_ = 物权专用，d_ = 债权专用，无前缀 = 两者通用
TAG_REGISTRY: list[dict[str, str]] = [
    # ---- 优势标签（通用）
    {"code": "high_discount", "name": "高折价", "kind": "advantage",
     "desc": "折价率 ≥ 高折价线（默认 30%）"},
    {"code": "clean_title", "name": "产权清晰", "kind": "advantage",
     "desc": "非集体/划拨用地，可办不动产登记且无转让限制"},
    {"code": "granted_land", "name": "出让用地", "kind": "advantage",
     "desc": "土地性质为出让"},
    {"code": "clean_judicial", "name": "无抵押无查封", "kind": "advantage",
     "desc": "抵押数量为 0 且轮候查封为 0"},
    {"code": "deadline_ample", "name": "竞价期充裕", "kind": "advantage",
     "desc": "距截止日仍有充足时间（> 临近天数）"},
    {"code": "low_judicial_risk", "name": "司法状态干净", "kind": "advantage",
     "desc": "无抵押、无查封、无涉诉，产权司法状态干净"},

    # ---- 优势标签（物权）
    {"code": "rent_pass", "name": "净租售达标", "kind": "advantage",
     "desc": "净租售比 ≥ 达标线（默认 5%）"},
    {"code": "good_location", "name": "区位优质", "kind": "advantage",
     "desc": "区位流通性维度得分率 ≥ 80%"},
    {"code": "vacant", "name": "空置可交付", "kind": "advantage",
     "desc": "公告载明空置、可正常交付"},
    {"code": "no_lease", "name": "无租赁", "kind": "advantage",
     "desc": "公告载明无租赁"},
    {"code": "fresh_appraisal", "name": "估值新鲜", "kind": "advantage",
     "desc": "司法评估时点在陈旧线以内（默认 6 个月）"},
    {"code": "benchmark_consistent", "name": "双基准一致", "kind": "advantage",
     "desc": "对评估价折价与对市场价折价分歧在容许范围内"},
    {"code": "allocated_repairable", "name": "划拨可补办", "kind": "advantage",
     "desc": "划拨用地但可补办出让手续，成本可控"},

    # ---- 优势标签（债权）
    {"code": "d_over_covered", "name": "抵押充足覆盖", "kind": "advantage",
     "desc": "抵押物覆盖倍数 ≥ 安全线（默认 1.5 倍）"},
    {"code": "d_first_rank", "name": "首封一顺位", "kind": "advantage",
     "desc": "担保顺位为首封 / 一顺位抵押"},
    {"code": "d_execution_advanced", "name": "执行进展靠前", "kind": "advantage",
     "desc": "已挂拍或执行中，回款路径明确"},
    {"code": "d_solvent_debtor", "name": "债务人有偿付能力", "kind": "advantage",
     "desc": "债务人名下存在可供执行财产"},
    {"code": "d_docs_complete", "name": "债权凭证齐全", "kind": "advantage",
     "desc": "判决书、合同、借据凭证齐全"},

    # ---- 风险标签（通用）
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
    {"code": "many_mortgages", "name": "多抵押", "kind": "risk",
     "desc": "抵押数量 ≥ 多抵押线（默认 2）"},
    {"code": "many_lawsuits", "name": "涉诉较多", "kind": "risk",
     "desc": "涉诉案件数 ≥ 涉诉线（默认 3）"},
    {"code": "collective_land", "name": "集体用地", "kind": "risk",
     "desc": "土地性质为集体用地"},
    {"code": "allocated_land", "name": "划拨用地", "kind": "risk",
     "desc": "土地性质为划拨用地（可补办者仍需计补缴成本）"},
    {"code": "transfer_restricted", "name": "限制转让", "kind": "risk",
     "desc": "公告载明资产限制转让"},
    {"code": "no_registration", "name": "无法办证", "kind": "risk",
     "desc": "无法办理不动产登记"},
    {"code": "big_arrears", "name": "大额欠费", "kind": "risk",
     "desc": "欠费总额 ≥ 大额线（默认 50 万）"},
    {"code": "high_holding_cost", "name": "持有成本高", "kind": "risk",
     "desc": "税费与欠费合计占标的成交价比例 ≥ 高线（默认 5%）"},
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

    # ---- 风险标签（物权·评估基准）
    {"code": "appraisal_stale", "name": "估值陈旧", "kind": "risk",
     "desc": "司法评估时点距今超陈旧线（默认 6 个月），建议人工复评"},
    {"code": "benchmark_divergent", "name": "基准分歧大", "kind": "risk",
     "desc": "司法评估价与市场可比价折价率分歧超限，价格维度可信度下降"},
    {"code": "no_market_comp", "name": "缺可比案例", "kind": "risk",
     "desc": "无市场可比成交价或样本不足，只能用单基准估值"},
    {"code": "clearance_hard", "name": "清场困难", "kind": "risk",
     "desc": "清场难度等级 ≥ 3（案外人占用 / 无法清场）"},

    # ---- 风险标签（债权）
    {"code": "d_under_covered", "name": "抵押覆盖不足", "kind": "risk",
     "desc": "抵押物覆盖倍数 < 不足线（默认 1.0 倍），本金回收存疑"},
    {"code": "d_no_guarantee", "name": "无担保", "kind": "risk",
     "desc": "该债权无任何担保，纯信用"},
    {"code": "d_not_first_rank", "name": "非首封顺位", "kind": "risk",
     "desc": "担保顺位为二顺位及以后，需前顺位清偿后才有余额"},
    {"code": "d_execution_stalled", "name": "执行受阻", "kind": "risk",
     "desc": "终本或流拍未能处置，回款路径中断"},
    {"code": "d_crowded_creditors", "name": "竞争者众", "kind": "risk",
     "desc": "已知其他债权人数量 ≥ 竞争线（默认 3 个）"},
    {"code": "d_weak_docs", "name": "债权凭证薄弱", "kind": "risk",
     "desc": "仅有借条或权属模糊，举证存在不确定性"},
]

TAG_BY_CODE: dict[str, dict[str, str]] = {t["code"]: t for t in TAG_REGISTRY}

# ============================================================ 一票否决规则种子
# 判定模型：结构化字段条件（field_conditions） OR 关键词命中（keywords）。
#
# 双轨制（业务确认）：
#   applies_to = property → 仅物权适用；债权不做一票否决
#   action     = veto     → 命中即 0 分淘汰
#              = penalty  → 转为量化打分的重点扣分项（业务：2、3 两类不宜硬淘汰）
#
# 业务确认要点：
#   V1 产权硬缺陷 → 真正的硬伤，保留一票否决。但划拨用地需区分：
#       划拨地可补缴土地出让金（普通住宅约成交价 1%、别墅/非居住类约土地估值 30%，
#       另付土地出让金 3% 契税），因此「划拨 + 可补办手续」不再直接否决，
#       只有「无法补办手续」才否决。
#   V2 清场风险 → 改为**重点扣分**（action=penalty），在清场维度重罚而不淘汰。
#   V3 大额欠费 → 改为**重点扣分**（action=penalty），在持有成本维度重罚。
#   V4 权属争议、V5 闲置报废 → 保留一票否决（无法通过压价对冲）。
VETO_RULE_SEED: list[dict] = [
    {
        "code": "V1",
        "name": "产权硬缺陷",
        "priority": 10,
        "enabled": True,
        "keyword_enabled": True,
        "applies_to": "property",
        "action": "veto",
        "description": "集体用地、划拨用地无法补手续、资产限制转让、无法办理不动产登记",
        "field_conditions": [
            {"field": "land_nature", "op": "eq", "value": "collective",
             "label": "土地性质为集体用地"},
            {"field": "land_nature", "op": "eq", "value": "allocated",
             "and": [{"field": "can_supplement_procedure", "op": "is_false"}],
             "label": "划拨用地且无法补办手续（可补办者不否决，改按补缴成本计分）"},
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
        "applies_to": "property",
        # 业务确认：清场风险属"可压价对冲"的风险，改在清场与占有维度重罚，
        # 不直接淘汰 —— 避免错杀"租金收益已计入价格"的长租标的。
        "action": "penalty",
        "description": "公告标注长期有效租约、买卖不破租赁、无法清退占用（转重点扣分项）",
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
        "keyword_enabled": False,   # 必须靠金额阈值，避免"欠税"二字误杀
        "applies_to": "property",
        # 业务确认：欠费可通过压价 + 从处置价款中优先扣除解决，不宜一票否决。
        "action": "penalty",
        "description": "存在大额欠税、土地闲置费、工程欠款（转重点扣分项）",
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
        "applies_to": "both",
        "action": "veto",
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
        "applies_to": "property",
        "action": "veto",
        "description": "设备老旧报废、土地无开发价值、物业无法正常使用",
        "field_conditions": [
            {"field": "scrap_status", "op": "in",
             "value": ["equipment_scrapped", "land_no_value", "property_unusable"],
             "label": "资产处于报废 / 无价值 / 不可用状态"},
        ],
        "keywords": ["已报废", "设备报废", "无开发价值", "无法正常使用", "危房", "倒塌"],
    },
    {
        "code": "V6",
        "name": "债权不可转让或已过时效",
        "priority": 15,
        "enabled": True,
        "keyword_enabled": True,
        "applies_to": "debt",
        "action": "veto",
        "description": "债权依法不得转让，或诉讼时效已届满（债权轨道唯一的一票否决项）",
        "field_conditions": [
            {"field": "debt_transferable", "op": "is_false",
             "label": "该债权依法不得转让"},
            {"field": "debt_limitation_ok", "op": "is_false",
             "label": "诉讼时效已届满，丧失胜诉权"},
        ],
        "keywords": ["不得转让", "不可转让", "已过诉讼时效", "超过诉讼时效"],
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
    # ---- 物权轨道
    "weights_property": DEFAULT_WEIGHTS_PROPERTY,
    "price_anchors": DEFAULT_PRICE_ANCHORS,
    "clearance_anchors": DEFAULT_CLEARANCE_ANCHORS,
    "holding_cost_anchors": DEFAULT_HOLDING_COST_ANCHORS,
    "rent_anchors": DEFAULT_RENT_ANCHORS,
    "land_year_anchors": DEFAULT_LAND_YEAR_ANCHORS,
    # ---- 债权轨道
    "weights_debt": DEFAULT_WEIGHTS_DEBT,
    "coverage_anchors": DEFAULT_COVERAGE_ANCHORS,
    "debt_rank_scores": DEBT_RANK_SCORES,
    "debt_execution_scores": DEBT_EXECUTION_SCORES,
    "debt_solvency_scores": DEBT_SOLVENCY_SCORES,
    "debt_doc_scores": DEBT_DOC_SCORES,
    # ---- 通用
    "grade_thresholds": DEFAULT_GRADE_THRESHOLDS,
    "cost_params": DEFAULT_COST_PARAMS,
    "legal_penalty": DEFAULT_LEGAL_PENALTY,
    "location_params": DEFAULT_LOCATION_PARAMS,
    "ownership_params": DEFAULT_OWNERSHIP_PARAMS,
    "tag_thresholds": DEFAULT_TAG_THRESHOLDS,
    "tag_enabled": {t["code"]: True for t in TAG_REGISTRY},
    "default_rent_per_sqm_month": DEFAULT_RENT_PER_SQM_MONTH,
    "disclaimer": None,   # None → 用 config.DEFAULT_DISCLAIMER
}

# 兼容旧键名（历史库中可能存有 weights，装载时自动迁移到 weights_property）
LEGACY_CONFIG_ALIASES: dict[str, str] = {"weights": "weights_property"}

CONFIG_GROUP_LABELS: dict[str, str] = {
    "weights_property": "【物权】五维评分权重",
    "price_anchors": "【物权】评估基准打分锚点",
    "clearance_anchors": "【物权】清场与占有难度锚点",
    "holding_cost_anchors": "【物权】持有成本占比锚点",
    "rent_anchors": "【物权】净租售比打分锚点",
    "land_year_anchors": "土地剩余年限打分锚点",
    "weights_debt": "【债权】五维评分权重",
    "coverage_anchors": "【债权】抵押物覆盖倍数锚点",
    "debt_rank_scores": "【债权】担保顺位分值",
    "debt_execution_scores": "【债权】执行进展分值",
    "debt_solvency_scores": "【债权】债务人偿付能力分值",
    "debt_doc_scores": "【债权】凭证完整性分值",
    "grade_thresholds": "A/B/C 分级阈值",
    "cost_params": "租金/税费/损耗/评估基准系数",
    "legal_penalty": "产权司法风险扣分系数",
    "location_params": "区位流通性子项分值",
    "ownership_params": "权属合规补充子项分值",
    "tag_thresholds": "标签触发阈值",
    "tag_enabled": "标签开关",
    "default_rent_per_sqm_month": "各类型租金兜底值",
    "disclaimer": "报告免责声明文案",
}
