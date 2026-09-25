"""采集管理 + 批量导入。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from app.core import constants as C
from app.crawler.importer import import_table, template_csv
from app.crawler.registry import adapter_status
from app.crawler.service import recent_logs, run_adapter, run_all
from app.database import get_db
from app.models import ImportBatch
from app.security import ROLE_LEVEL, client_ip, log_operation, require_login
from app.webutils import render
from sqlalchemy import select

router = APIRouter(tags=["crawl"])

OPERATOR = ROLE_LEVEL["operator"]


def _deny_operator(request: Request, user):
    if ROLE_LEVEL.get(user.role, 0) < OPERATOR:
        return RedirectResponse("/?err=当前角色无执行权限（需要业务操作及以上）",
                                status_code=303)
    return None


# ==================================================================== 采集
@router.get("/crawl")
def crawl_page(request: Request, db: Session = Depends(get_db),
               user=Depends(require_login)):
    return render(request, "crawl.html", user=user, active="crawl",
                  adapters=adapter_status(), logs=recent_logs(db, 40),
                  platforms=C.PLATFORMS)


@router.post("/crawl/run")
def crawl_run(request: Request, platform: str = "all",
              db: Session = Depends(get_db), user=Depends(require_login)):
    denied = _deny_operator(request, user)
    if denied:
        return denied

    if platform == "all":
        logs = run_all(db, trigger="manual")
        ok = [l for l in logs if l.status == "success"]
        skip = [l for l in logs if l.status == "skipped"]
        created = sum(l.created for l in ok)
        updated = sum(l.updated for l in ok)
        msg = (f"全平台采集完成：成功 {len(ok)} 个、跳过 {len(skip)} 个；"
               f"新增 {created} 条、更新 {updated} 条")
        log_operation(db, user, "crawl_run_all",
                      detail={"success": len(ok), "skipped": len(skip),
                              "created": created, "updated": updated},
                      ip=client_ip(request))
        db.commit()
        return RedirectResponse(f"/crawl?ok={msg}", status_code=303)

    log = run_adapter(db, platform, trigger="manual")
    log_operation(db, user, "crawl_run", target=platform,
                  detail={"status": log.status, "created": log.created,
                          "updated": log.updated}, ip=client_ip(request))
    db.commit()

    label = C.PLATFORMS.get(platform, platform)
    if log.status == "success":
        return RedirectResponse(
            f"/crawl?ok={label} 采集完成：新增 {log.created} 条、更新 {log.updated} 条",
            status_code=303)
    return RedirectResponse(f"/crawl?err={label}：{log.message}", status_code=303)


@router.post("/assets/reevaluate-all")
def reevaluate_all_route(request: Request, db: Session = Depends(get_db),
                         user=Depends(require_login)):
    denied = _deny_operator(request, user)
    if denied:
        return denied
    from app.core.pipeline import reevaluate_all

    stats = reevaluate_all(db, trigger="manual_all")
    log_operation(db, user, "reevaluate_all", detail=stats, ip=client_ip(request))
    db.commit()
    return RedirectResponse(
        f"/assets?ok=已全量重算 {stats['total']} 条（A {stats['A']} / B {stats['B']} / "
        f"C {stats['C']}，其中否决 {stats['vetoed']} 条）", status_code=303)


# ==================================================================== 导入
@router.get("/import")
def import_page(request: Request, db: Session = Depends(get_db),
                user=Depends(require_login)):
    batches = db.execute(
        select(ImportBatch).order_by(ImportBatch.id.desc()).limit(20)
    ).scalars().all()
    from app.crawler.importer import TEMPLATE_COLUMNS
    return render(request, "import.html", user=user, active="import",
                  batches=batches, columns=TEMPLATE_COLUMNS)


@router.post("/import/upload")
async def import_upload(request: Request, file: UploadFile = File(...),
                        db: Session = Depends(get_db), user=Depends(require_login)):
    denied = _deny_operator(request, user)
    if denied:
        return denied

    if not file.filename:
        return RedirectResponse("/import?err=未选择文件", status_code=303)
    if not file.filename.lower().endswith((".csv", ".xlsx", ".xlsm")):
        return RedirectResponse(
            "/import?err=仅支持 .csv / .xlsx 文件（.xls 请先另存为 .xlsx）", status_code=303)

    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        return RedirectResponse("/import?err=文件超过 20MB 上限", status_code=303)

    batch = import_table(db, file.filename, content, operator=user.username)
    log_operation(db, user, "import_upload", target=batch.batch_no,
                  detail={"imported": batch.imported, "failed": batch.failed},
                  ip=client_ip(request))
    db.commit()

    if batch.failed and batch.imported == 0:
        return RedirectResponse(
            f"/import?err=导入失败（批次 {batch.batch_no}），请查看下方错误明细",
            status_code=303)
    return RedirectResponse(
        f"/import?ok=批次 {batch.batch_no} 导入完成：成功 {batch.imported} 行、"
        f"失败 {batch.failed} 行，已自动完成评分", status_code=303)


@router.get("/import/template.csv")
def import_template(user=Depends(require_login)):
    return Response(
        content=template_csv(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 'attachment; filename="asset-import-template.csv"'},
    )
