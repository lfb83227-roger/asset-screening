"""数据库连接与会话管理。"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import DB_URL


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


_is_sqlite = DB_URL.startswith("sqlite")

engine = create_engine(
    DB_URL,
    echo=False,
    future=True,
    # SQLite 在多线程 Web 服务下需要放开线程检查
    connect_args={"check_same_thread": False} if _is_sqlite else {},
)

if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragma(dbapi_conn, _rec):  # pragma: no cover - 基础设施
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """事务上下文：正常退出提交，异常回滚。"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db() -> Iterator[Session]:
    """FastAPI 依赖注入用的会话生成器。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """建表 + 首次运行灌入种子数据。"""
    from app import models  # noqa: F401  确保模型已注册到 metadata

    Base.metadata.create_all(bind=engine)
