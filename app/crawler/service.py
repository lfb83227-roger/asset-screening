"""采集调度服务：把适配器输出落成资产并自动评分。

一轮采集的完整链路：
    适配器 fetch → normalize_record（字段归一）→ enrich_from_text（公告结构化）
    → make_uid / make_dedup_key（去重）→ upsert（新增或保留最新版本）
    → evaluate_and_persist（一票否决 + 五维评分 + 标签 + 分级）
    → CrawlLog（留痕，可审计）
"""
from __future__ import annotations

import time
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.pipeline import evaluate_and_persist
from app.core.ruleset import load_ruleset
from app.crawler.base import AdapterNotConfigured, RawListing
from app.crawler.dedup import make_dedup_key, make_uid, merge_record
from app.crawler.normalizer import enrich_from_text, normalize_record
from app.crawler.registry import all_adapters, get_adapter
from app.models import Asset, CrawlLog

# 允许写入 Asset 的字段白名单（防止把内部键写库）
_ASSET_COLUMNS: set[str] = {c.name for c in Asset.__table__.columns}


def build_record(platform: str, raw: RawListing, batch: str | None = None) -> dict:
    """RawListing → 标准字段 dict（含去重键）。"""
    data = dict(raw.payload or {})
    if raw.external_id:
        data.setdefault("标的编号", raw.external_id)
    if raw.title:
        data.setdefault("标的名称", raw.title)
    if raw.detail_url:
        data.setdefault("详情链接", raw.detail_url)
    if raw.raw_text:
        data.setdefault("公告原文", raw.raw_text)

    record = normalize_record(data, platform=platform)
    record = enrich_from_text(record)

    record["uid"] = make_uid(platform, raw.external_id, record)
    record["dedup_key"] = make_dedup_key(record)
    record["source_batch"] = batch
    return record


def _asset_kwargs(record: dict) -> dict:
    return {k: v for k, v in record.items()
            if k in _ASSET_COLUMNS and not k.startswith("_")}


def upsert_asset(db: Session, record: dict) -> tuple[str, Asset]:
    """按 uid 写入或更新；返回 (模式, 资产对象)。模式：created / updated。"""
    uid = record["uid"]
    existing = db.execute(select(Asset).where(Asset.uid == uid)).scalar_one_or_none()

    if existing is None:
        asset = Asset(**_asset_kwargs(record))
        now = datetime.now()
        asset.first_seen_at = now
        asset.last_seen_at = now
        db.add(asset)
        db.flush()
        return "created", asset

    # 已存在 → 保留最新版本数据，版本号 +1
    updated = merge_record(existing, _asset_kwargs(record))
    existing.last_seen_at = datetime.now()
    if updated:
        existing.data_version = (existing.data_version or 1) + 1
    db.flush()
    return "updated", existing


# ==================================================================== 采集调度
def ingest_records(db: Session, records: list[dict], trigger: str,
                   batch: str | None = None) -> dict:
    """把一批已归一化的记录写入并评分。采集与导入共用这条路径。"""
    rs = load_ruleset(db)
    stats = {"fetched": len(records), "created": 0, "updated": 0,
             "scored": 0, "vetoed": 0, "cross_platform_dup": 0}

    for record in records:
        mode, asset = upsert_asset(db, record)
        stats["created" if mode == "created" else "updated"] += 1

        if asset.dedup_key:
            others = db.execute(
                select(Asset.uid).where(
                    Asset.dedup_key == asset.dedup_key, Asset.uid != asset.uid)
            ).scalars().all()
            if others:
                stats["cross_platform_dup"] += 1

        result = evaluate_and_persist(db, asset, trigger=trigger, ruleset=rs)
        stats["scored"] += 1
        if result.status == "vetoed":
            stats["vetoed"] += 1

    return stats


def run_adapter(db: Session, platform: str, trigger: str = "manual",
                limit: int = 200, batch: str | None = None) -> CrawlLog:
    """跑一个平台的采集。失败不抛异常，写成 CrawlLog 状态，便于批量调度。"""
    started = time.perf_counter()
    log = CrawlLog(platform=platform, trigger=trigger, status="running")
    db.add(log)
    db.flush()

    try:
        adapter = get_adapter(platform)
        listings = adapter.fetch(limit=limit)
    except AdapterNotConfigured as exc:
        log.status = "skipped"
        log.message = str(exc)
        log.duration_ms = int((time.perf_counter() - started) * 1000)
        db.flush()
        return log
    except Exception as exc:  # noqa: BLE001 - 采集异常必须留痕而不是中断调度
        log.status = "failed"
        log.message = f"{type(exc).__name__}: {exc}"
        log.duration_ms = int((time.perf_counter() - started) * 1000)
        db.flush()
        return log

    try:
        records = [build_record(platform, raw, batch) for raw in listings]
        stats = ingest_records(db, records, trigger=f"crawl:{platform}", batch=batch)
        log.status = "success"
        log.fetched = stats["fetched"]
        log.created = stats["created"]
        log.updated = stats["updated"]
        log.scored = stats["scored"]
        log.skipped_dup = stats["updated"] + stats["cross_platform_dup"]
        log.message = (f"新增 {stats['created']} 条，更新 {stats['updated']} 条，"
                       f"命中否决 {stats['vetoed']} 条，"
                       f"跨平台重复 {stats['cross_platform_dup']} 条")
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        log = CrawlLog(platform=platform, trigger=trigger, status="failed",
                       message=f"入库失败：{type(exc).__name__}: {exc}",
                       duration_ms=int((time.perf_counter() - started) * 1000))
        db.add(log)
        db.flush()
        return log

    log.duration_ms = int((time.perf_counter() - started) * 1000)
    db.flush()
    return log


def run_all(db: Session, trigger: str = "schedule", limit: int = 200) -> list[CrawlLog]:
    """跑全部已启用平台；这就是 7×24 定时任务的入口（见 run.py 的 schedule 子命令）。"""
    logs = []
    for adapter in all_adapters():
        logs.append(run_adapter(db, adapter.platform, trigger=trigger, limit=limit))
    return logs


def recent_logs(db: Session, limit: int = 50) -> list[CrawlLog]:
    return list(db.execute(
        select(CrawlLog).order_by(CrawlLog.id.desc()).limit(limit)
    ).scalars().all())
