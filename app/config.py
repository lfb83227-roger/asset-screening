"""全局配置。

所有路径与运行参数集中在此，可用环境变量覆盖，便于交付到不同环境。
"""
from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------- 路径
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
SAMPLE_DIR = DATA_DIR / "samples"
REPORT_DIR = DATA_DIR / "reports"
UPLOAD_DIR = DATA_DIR / "uploads"
LOG_DIR = DATA_DIR / "logs"
STATIC_DIR = BASE_DIR / "app" / "static"
TEMPLATE_DIR = BASE_DIR / "app" / "templates"

for _d in (DATA_DIR, SAMPLE_DIR, REPORT_DIR, UPLOAD_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- 数据库
# 默认 SQLite 单机零配置；交付生产可切 PostgreSQL：
#   set APP_DB_URL=postgresql+psycopg://user:pwd@host:5432/assetdb
DB_URL = os.getenv("APP_DB_URL", f"sqlite:///{DATA_DIR / 'app.db'}")

# ---------------------------------------------------------------- Web
HOST = os.getenv("APP_HOST", "127.0.0.1")
PORT = int(os.getenv("APP_PORT", "8000"))
SECRET_KEY = os.getenv("APP_SECRET_KEY", "dev-only-change-me-in-production")
SESSION_COOKIE = os.getenv("APP_SESSION_COOKIE", "asset_session")
SESSION_MAX_AGE = 12 * 3600  # 12 小时

# ---------------------------------------------------------------- 业务开关
# 一期禁止：AI 大模型语义解析 / 线上付费 / 客户登录 / 对外展示端口 / 自动撮合。
# 这里集中声明，任何越界功能都应显式打开此开关并走评审。
ENABLE_LLM_SEMANTIC_PARSE = False   # PRD 六-1：一期不开发
ENABLE_PUBLIC_PORTAL = False        # PRD 六-2：一期不开发

# ---------------------------------------------------------------- PDF 字体
# 报告导出需要中文字体。按顺序探测，命中即用。
CJK_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyh.ttf",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    r"C:\Windows\Fonts\Deng.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]
CJK_FONT_NAME = "AssetCJK"

# ---------------------------------------------------------------- 免责声明
# PRD 模块6-6：报告固定自带、不可删改（后台可覆盖文案，但不可置空）。
DEFAULT_DISCLAIMER = (
    "本报告基于公开平台数据由 AI 自动测算生成，仅为初步筛选参考，"
    "不构成资产投资、过户、收益承诺。所有标的须经线下人工深度尽调复核后方可决策。"
    "系统仅采集平台公开公示信息，无法识别线下隐性风险（私下租约、口头协议、隐性欠款、"
    "实地占用等），租金、税费、租售比均为大数据预估值，不作为最终落地依据。"
)

# ---------------------------------------------------------------- 采集
CRAWLER_TIMEOUT = float(os.getenv("APP_CRAWLER_TIMEOUT", "15"))
CRAWLER_USER_AGENT = (
    "Mozilla/5.0 (compatible; AssetScreeningBot/1.0; "
    "+respect-robots.txt; contact=internal-ops)"
)
