"""初始化引导：建表 → 灌默认参数与规则 → 建默认账号 → （可选）导入样本标的。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.ruleset import ensure_defaults
from app.database import init_db, session_scope
from app.security import ensure_default_users


def bootstrap(with_samples: bool = True, reset: bool = False) -> dict:
    """一键初始化。幂等，可重复执行。"""
    from app.config import DB_URL

    if reset:
        _drop_all()

    init_db()
    stats: dict = {"config": 0, "veto_rules": 0, "regions": 0, "users": 0, "samples": {}}

    with session_scope() as db:
        stats.update({k: v for k, v in ensure_defaults(db).items()})
        stats["users"] = ensure_default_users(db)

    if with_samples:
        stats["samples"] = load_sample_assets()

    stats["db_url"] = str(DB_URL)
    return stats


def _drop_all() -> None:
    from app.database import Base, engine
    from app import models  # noqa: F401

    Base.metadata.drop_all(bind=engine)


def load_sample_assets(db: Session | None = None) -> dict:
    """把内置样本通过**真实采集管道**灌进去（走归一化/去重/评分全流程）。"""
    from app.crawler.registry import REGISTRY
    from app.crawler.service import run_adapter
    from app.crawler.samples import sample_platforms

    def _run(session: Session) -> dict:
        out = {}
        for platform in sample_platforms():
            if platform == "manual":
                out[platform] = _load_manual(session)
                continue
            if platform not in REGISTRY:
                continue
            log = run_adapter(session, platform, trigger="seed", limit=100)
            out[platform] = {"status": log.status, "created": log.created,
                             "updated": log.updated, "message": log.message}
        return out

    if db is not None:
        return _run(db)
    with session_scope() as session:
        return _run(session)


def _load_manual(db: Session) -> dict:
    """手工录入样本走导入器（验证 CSV 之外的直接入库路径）。"""
    from app.crawler.base import RawListing
    from app.crawler.service import build_record, ingest_records
    from app.crawler.samples import sample_listings

    records = [build_record("manual", RawListing(**r), batch="SEED")
               for r in sample_listings("manual")]
    stats = ingest_records(db, records, trigger="seed", batch="SEED")
    return {"status": "success", "created": stats["created"],
            "updated": stats["updated"], "message": "手工录入样本已入库"}


if __name__ == "__main__":  # pragma: no cover
    import json

    print(json.dumps(bootstrap(), ensure_ascii=False, indent=2))
