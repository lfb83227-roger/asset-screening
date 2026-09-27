"""v1 → v2 双轨制迁移脚本（幂等，可重复执行）。

做三件事：
1. 给 assets 表补齐 v2 新增列（SQLite 没有 ALTER COLUMN，只做 ADD COLUMN）。
2. 重建 sys_config / veto_rules 的双轨参数与规则（旧的同名 key 会被覆盖为出厂默认）。
3. 保留 users / 已有 assets 数据不动 —— 口令不受影响。

用法（在项目根目录）：
    python deploy/migrate_v2.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, text

from app.core import constants as C
from app.core.scoring import score_all
from app.core.ruleset import load_ruleset
from app.database import Base, SessionLocal, engine
from app import models  # noqa: F401  确保 metadata 完整


# v2 在 assets 上新增的列：名称 -> SQLite 声明类型
NEW_COLUMNS: dict[str, str] = {
    "asset_class": "VARCHAR(16) DEFAULT 'property'",
    "utility_owed": "FLOAT",
    "heating_owed": "FLOAT",
    "owner_is_company": "BOOLEAN",
    "transfer_tax_estimate": "FLOAT",
    "appraisal_at": "DATETIME",
    "appraisal_refreshed_price": "FLOAT",
    "market_comp_source": "VARCHAR(64)",
    "market_comp_count": "INTEGER",
    "debt_principal": "FLOAT",
    "debt_interest": "FLOAT",
    "collateral_value": "FLOAT",
    "debt_start_price": "FLOAT",
    "guarantee_rank": "VARCHAR(16)",
    "execution_stage": "VARCHAR(16)",
    "debtor_solvency": "VARCHAR(16)",
    "debt_doc_level": "VARCHAR(16)",
    "debt_transferable": "BOOLEAN",
    "debt_limitation_ok": "BOOLEAN",
    "competing_claims": "INTEGER",
}


VETO_RULE_NEW_COLUMNS: dict[str, str] = {
    "applies_to": "VARCHAR(12) DEFAULT 'both'",
    "action": "VARCHAR(12) DEFAULT 'veto'",
}


def ensure_columns(conn) -> list[str]:
    insp = inspect(conn)
    added: list[str] = []

    have = {c["name"] for c in insp.get_columns("assets")}
    for name, ddl in NEW_COLUMNS.items():
        if name not in have:
            conn.execute(text(f"ALTER TABLE assets ADD COLUMN {name} {ddl}"))
            added.append(f"assets.{name}")

    have_v = {c["name"] for c in insp.get_columns("veto_rules")}
    for name, ddl in VETO_RULE_NEW_COLUMNS.items():
        if name not in have_v:
            conn.execute(text(f"ALTER TABLE veto_rules ADD COLUMN {name} {ddl}"))
            added.append(f"veto_rules.{name}")

    return added


def reseed_config(db) -> tuple[int, int]:
    """按出厂默认写入/覆盖双轨参数与规则。返回 (参数数, 规则数)。"""
    import json

    from app.models import SysConfig, VetoRule

    n_cfg = 0
    for key, default in C.CONFIG_KEYS.items():
        value = default if isinstance(default, str) else json.dumps(default, ensure_ascii=False)
        row = db.get(SysConfig, key)
        if row is None:
            db.add(SysConfig(key=key, value=value))
        else:
            row.value = value
        n_cfg += 1

    n_rule = 0
    for spec in C.VETO_RULE_SEED:
        code = spec["code"]
        row = db.query(VetoRule).filter(VetoRule.code == code).one_or_none()
        data = {k: v for k, v in spec.items() if k not in ("id", "code")}
        if row is None:
            db.add(VetoRule(code=code, **data))
        else:
            for k, v in data.items():
                setattr(row, k, v)
        n_rule += 1

    db.commit()
    return n_cfg, n_rule


def rescore_all(db) -> int:
    """用 v2 引擎重算全部标的（事务末尾统一提交，避免逐条 flush）。"""
    from app.core.pipeline import evaluate_and_persist
    from app.models import Asset

    rs = load_ruleset(db)
    ids = db.execute(text("SELECT id FROM assets")).scalars().all()
    n = 0
    for aid in ids:
        a = db.get(Asset, aid)
        evaluate_and_persist(db, a, trigger="migrate_v2", ruleset=rs)
        n += 1
        if n % 50 == 0:
            db.flush()
    db.commit()
    return n


def main() -> int:
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        added = ensure_columns(conn)
    print(f"① 新增列 {len(added)} 个：{', '.join(added) if added else '（无，已是最新）'}")

    with SessionLocal() as db:
        n_cfg, n_rule = reseed_config(db)
        print(f"② 写入参数 {n_cfg} 项 / 否决规则 {n_rule} 条")
        n = rescore_all(db)
        print(f"③ 重算标的 {n} 条")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
