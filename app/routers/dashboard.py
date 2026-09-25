"""总览看板。"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import constants as C
from app.crawler.service import recent_logs
from app.database import get_db
from app.models import Asset, OperationLog
from app.security import require_login
from app.webutils import render

router = APIRouter(tags=["dashboard"])


@router.get("/")
def dashboard(request: Request, db: Session = Depends(get_db),
              user=Depends(require_login)):
    total = db.execute(select(func.count(Asset.id))).scalar_one()
    by_grade = dict(db.execute(
        select(Asset.grade, func.count(Asset.id)).group_by(Asset.grade)
    ).all())
    by_status = dict(db.execute(
        select(Asset.status, func.count(Asset.id)).group_by(Asset.status)
    ).all())
    by_type = dict(db.execute(
        select(Asset.asset_type, func.count(Asset.id)).group_by(Asset.asset_type)
    ).all())
    by_platform = dict(db.execute(
        select(Asset.source_platform, func.count(Asset.id))
        .group_by(Asset.source_platform)
    ).all())

    scored = [g for g in ("A", "B", "C") if g in by_grade]
    avg_score = db.execute(
        select(func.avg(Asset.total_score)).where(Asset.status == "scored")
    ).scalar_one()

    # 近 7 天新增（按 first_seen_at）
    since = datetime.now() - timedelta(days=7)
    new_7d = db.execute(
        select(func.count(Asset.id)).where(Asset.first_seen_at >= since)
    ).scalar_one()

    top_assets = db.execute(
        select(Asset).where(Asset.status == "scored")
        .order_by(Asset.total_score.desc()).limit(8)
    ).scalars().all()

    # 待办：临近截止的优质标的
    soon = db.execute(
        select(Asset).where(
            Asset.status == "scored",
            Asset.deadline_at.is_not(None),
            Asset.deadline_at >= datetime.now(),
            Asset.deadline_at <= datetime.now() + timedelta(days=15),
        ).order_by(Asset.deadline_at).limit(6)
    ).scalars().all()

    recent_ops = db.execute(
        select(OperationLog).order_by(OperationLog.id.desc()).limit(8)
    ).scalars().all()

    max_type = max(by_type.values()) if by_type else 1
    max_plat = max(by_platform.values()) if by_platform else 1

    total_for_bar = sum(by_grade.values()) or 1

    return render(
        request, "dashboard.html", user=user, active="dashboard",
        total=total, by_grade=by_grade, by_status=by_status,
        by_type=by_type, by_platform=by_platform, scored_grades=scored,
        avg_score=round(avg_score, 2) if avg_score else 0.0,
        new_7d=new_7d, top_assets=top_assets, soon=soon,
        recent_ops=recent_ops, logs=recent_logs(db, 6),
        max_type=max_type, max_plat=max_plat, total_for_bar=total_for_bar,
    )
