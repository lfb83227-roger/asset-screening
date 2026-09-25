#!/usr/bin/env python
"""AI资产智能筛选与评分系统 —— 命令行入口。

用法：
    python run.py init                 初始化数据库（建表 + 默认参数 + 默认账号）
    python run.py init --with-samples  初始化并灌入内置样本标的
    python run.py init --reset         清库重建（谨慎）
    python run.py serve                启动 Web 服务（默认 http://127.0.0.1:8000）
    python run.py crawl                跑一轮全平台采集
    python run.py crawl alipaimai      只跑指定平台
    python run.py schedule             按间隔循环采集（7×24 自动轮询）
    python run.py reevaluate           按当前参数全量重算
    python run.py report <asset_id>    导出指定标的 PDF 报告
    python run.py stats                打印标的评分概览
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import HOST, PORT  # noqa: E402


def cmd_init(args) -> int:
    from app.bootstrap import bootstrap

    stats = bootstrap(with_samples=args.with_samples, reset=args.reset)
    print("初始化完成：")
    print(f"  数据库           {stats['db_url']}")
    print(f"  参数配置项       {stats['config']} 项")
    print(f"  一票否决规则     {stats['veto_rules']} 条")
    print(f"  区域基准         {stats['regions']} 条")
    print(f"  后台账号         {stats['users']} 个")
    if stats.get("samples"):
        print("  样本采集：")
        for platform, info in stats["samples"].items():
            print(f"    - {platform:<16} {info.get('status')} "
                  f"新增 {info.get('created', 0)} 更新 {info.get('updated', 0)}")
    print()
    print("默认账号：admin / admin123　operator / operator123　viewer / viewer123")
    print("⚠️  生产环境请立即修改默认口令。")
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    from app.database import init_db
    from app.security import ensure_default_users
    from app.database import session_scope
    from app.core.ruleset import ensure_defaults

    init_db()
    with session_scope() as db:
        ensure_defaults(db)
        ensure_default_users(db)

    print(f"服务启动中…… http://{HOST}:{PORT}")
    uvicorn.run("app.main:app", host=HOST, port=PORT,
                reload=args.reload, log_level="info")
    return 0


def cmd_crawl(args) -> int:
    from app.crawler.service import run_adapter, run_all
    from app.database import session_scope

    with session_scope() as db:
        if args.platform == "all":
            logs = run_all(db, trigger="cli")
        else:
            logs = [run_adapter(db, args.platform, trigger="cli")]
        for log in logs:
            print(f"[{log.status:<8}] {log.platform:<16} {log.message}")
    return 0


def cmd_schedule(args) -> int:
    """7×24 自动轮询（PRD 模块1-1）。生产建议交给系统计划任务调用 crawl。"""
    from app.crawler.service import run_all
    from app.database import session_scope

    interval = max(60, args.interval)
    print(f"进入定时采集模式，间隔 {interval} 秒（Ctrl+C 退出）")
    while True:
        started = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            with session_scope() as db:
                logs = run_all(db, trigger="schedule")
            summary = "；".join(f"{l.platform}:{l.status}" for l in logs)
            print(f"[{started}] {summary}")
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"[{started}] 本轮异常：{type(exc).__name__}: {exc}")
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("已退出定时采集。")
            return 0


def cmd_reevaluate(args) -> int:
    from app.core.pipeline import reevaluate_all
    from app.database import session_scope

    with session_scope() as db:
        stats = reevaluate_all(db, trigger="cli")
    print(f"重算完成：共 {stats['total']} 条　A {stats['A']} / B {stats['B']} / "
          f"C {stats['C']}　其中一票否决 {stats['vetoed']} 条")
    return 0


def cmd_report(args) -> int:
    from app.core.ruleset import load_ruleset
    from app.database import session_scope
    from app.models import Asset
    from app.report.pdf import save_pdf

    with session_scope() as db:
        asset = db.get(Asset, args.asset_id)
        if asset is None:
            print(f"标的不存在：{args.asset_id}")
            return 1
        path = save_pdf(asset, load_ruleset(db), operator="cli")
    print(f"报告已生成：{path}")
    return 0


def cmd_stats(args) -> int:
    from sqlalchemy import select

    from app.core import constants as C
    from app.database import session_scope
    from app.models import Asset

    with session_scope() as db:
        rows = db.execute(
            select(Asset).order_by(Asset.total_score.desc())
        ).scalars().all()

    print(f"{'ID':<4}{'等级':<5}{'总分':>7}  {'折价率':>8}{'净租售比':>10}  "
          f"{'状态':<9}{'来源':<14}标题")
    print("-" * 120)
    for a in rows:
        d = a.discount_rate
        rr = (a.rent_detail or {}).get("net_rent_ratio_pct", "—")
        print(f"{a.id:<4}{a.grade or '-':<5}{(a.total_score or 0):>7.2f}  "
              f"{(f'{d * 100:.1f}%' if d is not None else '—'):>8}{rr:>10}  "
              f"{C.ASSET_STATUSES.get(a.status, a.status):<9}"
              f"{C.PLATFORMS.get(a.source_platform, ''):<14}{a.title[:30]}")
    grades = {}
    for a in rows:
        grades[a.grade] = grades.get(a.grade, 0) + 1
    print(f"\n合计 {len(rows)} 条：" + "　".join(
        f"{g} 类 {grades.get(g, 0)} 条" for g in ("A", "B", "C")))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="run.py", description="AI资产智能筛选与评分系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="初始化数据库")
    p.add_argument("--with-samples", action="store_true", help="同时灌入内置样本标的")
    p.add_argument("--reset", action="store_true", help="清空并重建全部表（危险）")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("serve", help="启动 Web 服务")
    p.add_argument("--reload", action="store_true", help="开发模式热重载")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("crawl", help="执行一轮采集")
    p.add_argument("platform", nargs="?", default="all",
                   help="平台标识，默认 all")
    p.set_defaults(func=cmd_crawl)

    p = sub.add_parser("schedule", help="按间隔循环采集")
    p.add_argument("--interval", type=int, default=1800, help="间隔秒数，默认 1800")
    p.set_defaults(func=cmd_schedule)

    p = sub.add_parser("reevaluate", help="按当前参数全量重算")
    p.set_defaults(func=cmd_reevaluate)

    p = sub.add_parser("report", help="导出标的 PDF 报告")
    p.add_argument("asset_id", type=int)
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("stats", help="打印评分概览")
    p.set_defaults(func=cmd_stats)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
