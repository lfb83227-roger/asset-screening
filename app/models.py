"""ORM 模型。

表清单：
  assets            标的资产主表
  score_history     评分历史（保证"结果可复现"、可审计）
  region_benchmark  区域大数据基准（租金/税费/需求/流动性）
  veto_rules        一票否决规则（后台可增删改）
  sys_config        系统参数键值配置（权重、锚点、系数、阈值）
  crawl_logs        采集运行日志
  import_batches    批量导入批次
  users             后台用户（内部权限管理）
  operation_logs    操作日志
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


# ==================================================================== 标的资产
class Asset(Base, TimestampMixin):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uid: Mapped[str] = mapped_column(String(64), unique=True, index=True,
                                     comment="同源去重指纹")
    dedup_key: Mapped[str | None] = mapped_column(
        String(64), index=True,
        comment="跨平台去重键：同城市+地址+面积+类型的多平台挂牌视为同一标的")

    # -------------------------------------------------- 来源
    source_platform: Mapped[str] = mapped_column(String(32), index=True, default="manual")
    source_url: Mapped[str | None] = mapped_column(String(512))
    external_id: Mapped[str | None] = mapped_column(String(128), index=True)
    source_batch: Mapped[str | None] = mapped_column(String(64))

    title: Mapped[str] = mapped_column(String(512), default="")
    asset_type: Mapped[str] = mapped_column(String(24), index=True, default="other")
    land_nature: Mapped[str] = mapped_column(String(24), default="unknown")

    # -------------------------------------------------- 位置
    province: Mapped[str | None] = mapped_column(String(32), index=True)
    city: Mapped[str | None] = mapped_column(String(32), index=True)
    district: Mapped[str | None] = mapped_column(String(32), index=True)
    address: Mapped[str | None] = mapped_column(String(512))

    # -------------------------------------------------- 面积与价格
    area_sqm: Mapped[float | None] = mapped_column(Float, comment="建筑面积 ㎡")
    land_area_sqm: Mapped[float | None] = mapped_column(Float, comment="土地面积 ㎡")
    start_price: Mapped[float | None] = mapped_column(Float, comment="起拍价 元")
    appraisal_price: Mapped[float | None] = mapped_column(Float, comment="评估价 元")
    market_price: Mapped[float | None] = mapped_column(Float, comment="周边同类型成交价 元")
    deposit: Mapped[float | None] = mapped_column(Float, comment="保证金 元")
    increment: Mapped[float | None] = mapped_column(Float, comment="加价幅度 元")

    # -------------------------------------------------- 挂牌
    listed_at: Mapped[datetime | None] = mapped_column(DateTime, comment="挂牌时间")
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime, comment="截止时间")
    auction_round: Mapped[str] = mapped_column(String(16), default="unknown")
    court: Mapped[str | None] = mapped_column(String(128))
    case_no: Mapped[str | None] = mapped_column(String(128))

    # -------------------------------------------------- 权属
    registration_ok: Mapped[bool | None] = mapped_column(Boolean, comment="可否办不动产登记")
    transfer_restricted: Mapped[bool | None] = mapped_column(Boolean, comment="是否限制转让")
    can_supplement_procedure: Mapped[bool | None] = mapped_column(
        Boolean, comment="划拨地可否补办手续")
    land_remaining_years: Mapped[float | None] = mapped_column(Float, comment="土地剩余年限")
    implicit_coownership: Mapped[bool | None] = mapped_column(Boolean, comment="隐性共有产权提示")
    compliance_level: Mapped[str] = mapped_column(String(16), default="full")

    # -------------------------------------------------- 司法
    mortgage_count: Mapped[int] = mapped_column(Integer, default=0, comment="抵押数量")
    seal_count: Mapped[int] = mapped_column(Integer, default=0, comment="轮候查封数量")
    lawsuit_count: Mapped[int] = mapped_column(Integer, default=0, comment="涉诉案件数")
    dispute_freq: Mapped[int] = mapped_column(Integer, default=0, comment="司法纠纷频次")
    co_ownership_dispute: Mapped[bool] = mapped_column(Boolean, default=False)
    irreversible_seal: Mapped[bool] = mapped_column(Boolean, default=False)

    # -------------------------------------------------- 占用与租赁
    lease_status: Mapped[str] = mapped_column(String(24), default="unknown")
    occupied: Mapped[bool | None] = mapped_column(Boolean, comment="是否被占用")
    can_clear: Mapped[bool | None] = mapped_column(Boolean, comment="可否清场")
    occupancy_note: Mapped[str | None] = mapped_column(String(512))

    # -------------------------------------------------- 欠费
    tax_owed: Mapped[float] = mapped_column(Float, default=0.0, comment="欠税 元")
    land_idle_fee: Mapped[float] = mapped_column(Float, default=0.0, comment="土地闲置费 元")
    construction_arrears: Mapped[float] = mapped_column(
        Float, default=0.0, comment="工程欠款 元")
    property_fee_owed: Mapped[float] = mapped_column(Float, default=0.0, comment="物业欠费 元")

    # -------------------------------------------------- 报废
    scrap_status: Mapped[str] = mapped_column(String(24), default="normal")

    # -------------------------------------------------- 区位与租金输入
    city_tier: Mapped[str | None] = mapped_column(String(16))
    industry_support_ratio: Mapped[float | None] = mapped_column(Float, comment="产业配套 0~1")
    rental_demand_ratio: Mapped[float | None] = mapped_column(Float, comment="出租需求 0~1")
    turnover_ratio: Mapped[float | None] = mapped_column(Float, comment="转手成交率 0~1")
    rent_per_sqm_month: Mapped[float | None] = mapped_column(
        Float, comment="实测租金 元/㎡/月；留空则取区域大数据均值")
    annual_gross_rent_override: Mapped[float | None] = mapped_column(
        Float, comment="年毛租金直接指定（优先级最高）")

    # -------------------------------------------------- 原文
    raw_text: Mapped[str | None] = mapped_column(Text, comment="公告原文")
    raw_payload: Mapped[dict | None] = mapped_column(JSON, comment="适配器原始字段")

    # -------------------------------------------------- 评分结果
    status: Mapped[str] = mapped_column(String(16), index=True, default="pending")
    veto_hits: Mapped[list | None] = mapped_column(JSON)
    total_score: Mapped[float | None] = mapped_column(Float)
    grade: Mapped[str | None] = mapped_column(String(2), index=True)
    score_detail: Mapped[dict | None] = mapped_column(JSON)
    rent_detail: Mapped[dict | None] = mapped_column(JSON)
    advantage_tags: Mapped[list | None] = mapped_column(JSON)
    risk_tags: Mapped[list | None] = mapped_column(JSON)
    conclusion: Mapped[str | None] = mapped_column(Text, comment="系统初筛结论")
    scored_at: Mapped[datetime | None] = mapped_column(DateTime)
    engine_version: Mapped[str | None] = mapped_column(String(32))

    # -------------------------------------------------- 采集时间线
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)
    data_version: Mapped[int] = mapped_column(Integer, default=1)

    history: Mapped[list["ScoreHistory"]] = relationship(
        back_populates="asset", cascade="all, delete-orphan", order_by="ScoreHistory.id.desc()"
    )

    __table_args__ = (
        Index("ix_assets_city_type", "city", "asset_type"),
        Index("ix_assets_grade_score", "grade", "total_score"),
    )

    # -------------------------------------------------- 便捷属性
    @property
    def total_arrears(self) -> float:
        """一票否决 V3 用的欠费总额。"""
        return float(self.tax_owed or 0) + float(self.land_idle_fee or 0) + \
            float(self.construction_arrears or 0) + float(self.property_fee_owed or 0)

    @property
    def reference_price(self) -> float | None:
        """参考价：优先周边成交价，其次评估价（PRD 维度1）。"""
        return self.market_price or self.appraisal_price

    @property
    def discount_rate(self) -> float | None:
        ref = self.reference_price
        if not ref or not self.start_price or ref <= 0:
            return None
        return (ref - self.start_price) / ref

    @property
    def deadline_days_left(self) -> float | None:
        if not self.deadline_at:
            return None
        return (self.deadline_at - datetime.now()).total_seconds() / 86400.0


# ==================================================================== 评分历史
class ScoreHistory(Base, TimestampMixin):
    """每次评分落一条，保证结果可复现、可回溯、可对比参数调整前后的差异。"""

    __tablename__ = "score_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    total_score: Mapped[float | None] = mapped_column(Float)
    grade: Mapped[str | None] = mapped_column(String(2))
    veto_hits: Mapped[list | None] = mapped_column(JSON)
    score_detail: Mapped[dict | None] = mapped_column(JSON)
    rent_detail: Mapped[dict | None] = mapped_column(JSON)
    advantage_tags: Mapped[list | None] = mapped_column(JSON)
    risk_tags: Mapped[list | None] = mapped_column(JSON)
    engine_version: Mapped[str | None] = mapped_column(String(32))
    params_snapshot: Mapped[dict | None] = mapped_column(JSON, comment="评分时的全量参数快照")
    trigger: Mapped[str] = mapped_column(String(24), default="manual", comment="触发方式")

    asset: Mapped[Asset] = relationship(back_populates="history")


# ==================================================================== 区域基准
class RegionBenchmark(Base, TimestampMixin):
    """区域大数据均值：租金、税费、空置、维保、需求与流动性。"""

    __tablename__ = "region_benchmark"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    province: Mapped[str] = mapped_column(String(32), index=True)
    city: Mapped[str] = mapped_column(String(32), index=True)
    district: Mapped[str | None] = mapped_column(String(32), index=True)
    asset_type: Mapped[str] = mapped_column(String(24), index=True)

    rent_per_sqm_month: Mapped[float] = mapped_column(Float, default=0.0)
    land_use_tax_per_sqm: Mapped[float] = mapped_column(Float, default=0.0)
    property_tax_rate: Mapped[float] = mapped_column(Float, default=0.12)
    vacancy_ratio: Mapped[float] = mapped_column(Float, default=0.10)
    maintenance_ratio: Mapped[float] = mapped_column(Float, default=0.05)

    city_tier: Mapped[str] = mapped_column(String(16), default="tier3")
    industry_support_ratio: Mapped[float] = mapped_column(Float, default=0.5)
    rental_demand_ratio: Mapped[float] = mapped_column(Float, default=0.5)
    turnover_ratio: Mapped[float] = mapped_column(Float, default=0.5)

    data_source: Mapped[str] = mapped_column(String(64), default="内置样本")
    effective_date: Mapped[str | None] = mapped_column(String(16))

    __table_args__ = (
        UniqueConstraint("city", "district", "asset_type", name="uq_region_key"),
    )


# ==================================================================== 一票否决规则
class VetoRule(Base, TimestampMixin):
    __tablename__ = "veto_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64))
    priority: Mapped[int] = mapped_column(Integer, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    keyword_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str | None] = mapped_column(Text)
    field_conditions: Mapped[list | None] = mapped_column(JSON)
    keywords: Mapped[list | None] = mapped_column(JSON)


# ==================================================================== 系统参数
class SysConfig(Base, TimestampMixin):
    __tablename__ = "sys_config"

    key: Mapped[str] = mapped_column(String(48), primary_key=True)
    value: Mapped[dict | list | str | float | int | None] = mapped_column(JSON)
    note: Mapped[str | None] = mapped_column(String(255))


# ==================================================================== 采集日志
class CrawlLog(Base, TimestampMixin):
    __tablename__ = "crawl_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    trigger: Mapped[str] = mapped_column(String(16), default="manual", comment="manual/schedule")
    status: Mapped[str] = mapped_column(String(16), default="success")
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    created: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    skipped_dup: Mapped[int] = mapped_column(Integer, default=0)
    scored: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)


# ==================================================================== 导入批次
class ImportBatch(Base, TimestampMixin):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    filename: Mapped[str | None] = mapped_column(String(255))
    operator: Mapped[str | None] = mapped_column(String(64))
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    imported: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    error_report: Mapped[list | None] = mapped_column(JSON)


# ==================================================================== 用户
class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(64), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="viewer", comment="admin/operator/viewer")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)


class OperationLog(Base, TimestampMixin):
    __tablename__ = "operation_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str | None] = mapped_column(String(128))
    detail: Mapped[dict | None] = mapped_column(JSON)
    ip: Mapped[str | None] = mapped_column(String(64))
