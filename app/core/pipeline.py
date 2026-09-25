"""评估流水线：把否决 → 测算 → 评分 → 标签 → 分级 串成一条确定性的链。

对外两个入口：
  evaluate(asset, ruleset, bench)           纯函数，不碰数据库（便于单测）
  evaluate_and_persist(db, asset, trigger)   落库 + 写评分历史 + 写操作日志
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session

from app import ENGINE_VERSION
from app.core import constants as C
from app.core.benchmark import push_down_benchmark, resolve_benchmark
from app.core.rental import compute_rent
from app.core.ruleset import RuleSet, load_ruleset
from app.core.scoring import score_all
from app.core.tags import evaluate_tags
from app.core.veto import evaluate_veto
from app.models import Asset, ScoreHistory


@dataclass
class Evaluation:
    status: str
    veto_hits: list = field(default_factory=list)
    total_score: float = 0.0
    grade: str = "C"
    score_detail: dict = field(default_factory=dict)
    rent_detail: dict = field(default_factory=dict)
    advantage_tags: list = field(default_factory=list)
    risk_tags: list = field(default_factory=list)
    conclusion: str = ""
    params_snapshot: dict = field(default_factory=dict)
    engine_version: str = ENGINE_VERSION


# ==================================================================== 结论文案
_GRADE_ACTION = {
    "A": "建议进入人工深度尽调池，安排实地核查与权属核验。",
    "B": "列为观察标的，选择性复核后决定是否投入尽调资源。",
    "C": "建议直接剔除，放弃该标的以释放人力。",
}


def _build_conclusion(veto_hits, total, grade, dims, advantages, risks) -> str:
    if veto_hits:
        names = "、".join(f"{h['code']} {h['name']}" for h in veto_hits)
        ev = "；".join(h["evidences"][0] for h in veto_hits if h.get("evidences"))
        return (f"命中一票否决规则（{names}）：{ev}。系统判定 0 分、{C.GRADES['C']}，"
                f"不参与量化打分，直接剔除。")

    parts = [f"综合得分 {total:.2f} 分（满分 100），判定为{C.GRADES[grade]}。"]
    if dims:
        order = dims.get("order", [])
        seg = "、".join(
            f"{dims['dimensions'][c]['label'].replace('维度', '')}"
            f" {dims['dimensions'][c]['score']:.1f}/{dims['dimensions'][c]['weight']:.0f}"
            for c in order if c in dims.get("dimensions", {})
        )
        parts.append(f"五维明细：{seg}。")

    if advantages:
        parts.append("优势：" + "、".join(a["name"] for a in advantages) + "。")
    else:
        parts.append("优势：暂无明显优势标签。")
    if risks:
        parts.append("风险：" + "、".join(r["name"] for r in risks) + "。")

    parts.append(_GRADE_ACTION.get(grade, ""))
    parts.append("本结论为线上公开数据初筛结果，须经线下人工深度尽调复核后方可作为决策依据。")
    return "".join(parts)


# ==================================================================== 纯函数评估
def evaluate(asset: Asset, ruleset: RuleSet, bench: dict) -> Evaluation:
    # 1) 一票否决（最高优先级，命中即终止）
    veto_hits = evaluate_veto(asset, ruleset)

    bench = push_down_benchmark(bench, asset)
    rent_detail = compute_rent(asset, ruleset, bench)

    if veto_hits:
        advantages, risks = evaluate_tags(asset, ruleset, {}, rent_detail)
        return Evaluation(
            status="vetoed",
            veto_hits=veto_hits,
            total_score=0.0,
            grade="C",
            score_detail={"dimensions": {}, "order": [], "vetoed": True,
                          "weights_total": round(ruleset.total_weight(), 2),
                          "total_score": 0.0},
            rent_detail=rent_detail,
            advantage_tags=advantages,
            risk_tags=risks,
            conclusion=_build_conclusion(veto_hits, 0.0, "C", {}, advantages, risks),
            params_snapshot=ruleset.to_snapshot(),
        )

    # 2) 五维打分
    score_detail, total = score_all(asset, ruleset, rent_detail, bench)
    from app.core.scoring import grade_of
    grade = grade_of(total, ruleset)

    # 3) 标签
    advantages, risks = evaluate_tags(asset, ruleset, score_detail, rent_detail)

    return Evaluation(
        status="scored",
        veto_hits=[],
        total_score=total,
        grade=grade,
        score_detail=score_detail,
        rent_detail=rent_detail,
        advantage_tags=advantages,
        risk_tags=risks,
        conclusion=_build_conclusion([], total, grade, score_detail, advantages, risks),
        params_snapshot=ruleset.to_snapshot(),
    )


# ==================================================================== 落库
def evaluate_and_persist(db: Session, asset: Asset, trigger: str = "manual",
                         ruleset: RuleSet | None = None) -> Evaluation:
    rs = ruleset or load_ruleset(db)
    bench = resolve_benchmark(db, asset, rs)
    result = evaluate(asset, rs, bench)

    now = datetime.now()
    asset.status = result.status
    asset.veto_hits = result.veto_hits or None
    asset.total_score = result.total_score
    asset.grade = result.grade
    asset.score_detail = result.score_detail
    asset.rent_detail = result.rent_detail
    asset.advantage_tags = result.advantage_tags
    asset.risk_tags = result.risk_tags
    asset.conclusion = result.conclusion
    asset.scored_at = now
    asset.engine_version = result.engine_version
    if asset.first_seen_at is None:
        asset.first_seen_at = now
    asset.last_seen_at = now

    db.add(ScoreHistory(
        asset_id=asset.id,
        total_score=result.total_score,
        grade=result.grade,
        veto_hits=result.veto_hits or None,
        score_detail=result.score_detail,
        rent_detail=result.rent_detail,
        advantage_tags=result.advantage_tags,
        risk_tags=result.risk_tags,
        engine_version=result.engine_version,
        params_snapshot=result.params_snapshot,
        trigger=trigger,
    ))
    db.flush()
    return result


def reevaluate_all(db: Session, trigger: str = "batch", only_ids: list[int] | None = None):
    """批量重算。后台改完权重/系数后一键刷新全量标的。"""
    from sqlalchemy import select

    stmt = select(Asset)
    if only_ids:
        stmt = stmt.where(Asset.id.in_(only_ids))
    assets = list(db.execute(stmt).scalars().all())

    rs = load_ruleset(db)
    stats = {"total": len(assets), "scored": 0, "vetoed": 0, "A": 0, "B": 0, "C": 0}
    for asset in assets:
        res = evaluate_and_persist(db, asset, trigger=trigger, ruleset=rs)
        stats["vetoed" if res.status == "vetoed" else "scored"] += 1
        stats[res.grade] += 1
    db.flush()
    return stats
