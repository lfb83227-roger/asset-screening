# 部署交接文档 —— AI资产智能筛选与评分系统

> 本文档面向三类读者：① 路小飞平台的 AI（对话式部署）② 外包/运维团队（自有服务器部署）③ 你自己（本地复跑）。
> 项目本身零配置依赖：一台能跑 Python 的机器 + 三条命令即可上线。
>
> **Git 仓库**：https://github.com/lfb83227-roger/asset-screening.git（公开）
> 路小飞对话指令见 `deploy/路小飞部署指令.txt`（整段复制粘贴即可）。
> **自有服务器保姆级部署**（systemd 守护 + Nginx + 备份）见 `deploy/独立服务器部署指南.md`。

## 🚀 生产环境（已上线）

| 项 | 值 |
|---|---|
| 访问地址 | http://106.54.226.110 |
| 服务器 | 腾讯云轻量 · 上海 · Ubuntu 24.04.4 LTS · 2核/3.6G |
| 部署目录 | `/opt/asset-screening`（venv 在 `venv/`） |
| 服务名 | `systemctl status asset-screening`（已 enable 开机自启 + 崩溃自愈） |
| 反向代理 | Nginx `80 → 127.0.0.1:8000`，配置在 `/etc/nginx/sites-available/asset-screening` |
| 数据目录 | `/opt/asset-screening/data/`（SQLite `app.db` + 报告 + 日志） |

### 日常运维（SSH 登录服务器后）

```bash
sudo systemctl restart asset-screening   # 重启应用
sudo systemctl status asset-screening    # 查看状态
sudo journalctl -u asset-screening -f    # 实时日志（Ctrl+C 退出）
sudo systemctl reload nginx              # 重载 Nginx
cd /opt/asset-screening && git pull && sudo systemctl restart asset-screening  # 更新代码
```

### 已做的安全加固

- `APP_SECRET_KEY` 为 32 字节随机值，写入 systemd 服务文件（权限 600）
- admin / operator / viewer 三个默认口令**已全部作废**并替换为随机强口令
- 采集模块保持关闭状态（不对任何外部平台发起请求）
- 服务器可直连 GitHub，后续更新用 `git pull` 即可

---

## 一、项目概况（30 秒了解）

| 项 | 说明 |
|---|---|
| 形态 | 单体 Web 应用（服务端渲染，无前端构建步骤） |
| 技术栈 | Python 3.11+ · FastAPI · SQLAlchemy 2.0 · SQLite · Jinja2 · ReportLab |
| 资源需求 | 内存 <300MB，磁盘 <200MB（含依赖），单进程 |
| 端口 | 默认 8000，可用环境变量覆盖 |
| 数据库 | SQLite 落在 `./data/app.db`，应用自动建库建表，无需预装数据库 |
| 外部依赖 | **无**。采集模块默认关闭真实抓取，不会对外发任何请求 |

## 二、部署三步（通用）

```bash
# 1. 解压后进入项目根目录，装依赖
pip install -r requirements.txt

# 2. 初始化数据库（建表 + 默认参数 + 默认账号 + 内置样本）
python run.py init --with-samples

# 3. 启动
python run.py serve
```

启动成功后访问 `http://<host>:8000/login`，默认账号：

| 账号 | 密码 | 角色 |
|---|---|---|
| admin | admin123 | 管理员（全部权限） |
| operator | operator123 | 业务操作（录入/采集/导入） |
| viewer | viewer123 | 只读 |

## 三、环境变量

全部可选，不设置就用默认值：

| 变量 | 默认 | 说明 |
|---|---|---|
| `APP_HOST` | `127.0.0.1` | **云部署必须改为 `0.0.0.0`**，否则外部访问不到 |
| `APP_PORT` | `8000` | 按平台分配的端口覆盖 |
| `APP_DB_URL` | `sqlite:///data/app.db` | 可切 PostgreSQL（`postgresql+psycopg://user:pwd@host:5432/assetdb`） |
| `APP_SECRET_KEY` | dev 默认值 | **上线必改**，会话签名密钥 |
| `APP_SESSION_COOKIE` | `asset_session` | Cookie 名 |
| `APP_CRAWLER_TIMEOUT` | `15` | 采集超时秒数 |

## 四、平台适配注意事项

1. **监听地址**：云沙箱/容器环境必须 `APP_HOST=0.0.0.0`，端口用平台分配的（通常是 `PORT` 环境变量，映射到 `APP_PORT`）。
2. **数据持久化**：SQLite、PDF 报告、日志都在 `./data/` 目录下。确认该目录挂载在持久卷上（路小飞部署位含 5GB 应用数据空间，够用）。
3. **中文字体（PDF 导出）**：按顺序探测 Windows / Linux / macOS 常见中文字体；云沙箱若都没有，自动降级为 ReportLab 内置 CID 字体 `STSong-Light`，功能不受影响。想字更好看可装 `fonts-wqy-zenhei`。
4. **启动探活**：健康检查端点 `GET /api/health`，返回 JSON，适合做平台的存活探针。
5. **进程守护**：单进程 uvicorn 即可，1核1G 富余很大；不需要 gunicorn 多 worker（SQLite 写并发有限，多 worker 反而有锁竞争风险）。

## 五、上线前 Checklist（合规相关，勿跳过）

- [ ] `APP_SECRET_KEY` 换成随机长字符串（如 `python -c "import secrets; print(secrets.token_hex(32))"`）
- [ ] 三个默认口令全部修改（后台「用户管理」页）
- [ ] 确认采集平台适配器 `enabled=False`（默认即关）——启用真实抓取前须过法务授权
- [ ] 确认「对外展示端口」开关关闭（`app/config.py::ENABLE_PUBLIC_PORTAL=False`，PRD 一期禁区）
- [ ] 区域基准表（后台「区域基准」页）按目标城市补齐真实数据，否则净租售比维度失真

## 六、验收清单

部署完成后逐项验证：

| 检查项 | 期望 |
|---|---|
| `GET /api/health` | 200，返回 JSON |
| `GET /login` | 200，登录页正常渲染 |
| admin 登录 | 跳转仪表盘，15 条样本标的存在 |
| 标的列表 `/assets` | 筛选（等级/状态/来源/关键词）、排序、分页可用 |
| 标的详情 `/assets/2` | 五维得分明细、同区域对比、评分历史可查 |
| PDF 导出 `/assets/2/report.pdf` | 下载 PDF，中文正常，含免责声明 |
| 后台五页 | 评分参数 / 否决规则 / 区域基准 / 用户 / 日志均可读写 |
| JSON API | `POST /api/evaluate` 能对传入标的返回评分结果 |
| 回归测试 | `python tests/test_engine.py && python tests/test_web.py` 全绿（45 例） |

---

## 七、给「路小飞」平台 AI 的部署指令（直接粘贴）

> 在路小飞会话中上传 `AI资产智能筛选与评分系统-完整包.zip` 后，把下面整段话发给它：

```text
我在附件里上传了一个完整的可运行项目「AI资产智能筛选与评分系统」（zip 包），
请帮我把它跑起来并部署上线。

【项目概况】
- 技术栈：Python 3.11+ / FastAPI / SQLite / Jinja2 服务端渲染，无前端构建步骤
- 形态：单体 Web 应用，单进程，内存占用 <300MB
- 外部依赖：无（采集模块默认关闭真实网络抓取，不会对外发请求）

【部署步骤】（解压后，在项目根目录执行）
1. pip install -r requirements.txt
2. python run.py init --with-samples
   （自动建表 + 默认参数 + 默认账号 + 15 条内置样本标的）
3. 环境变量：APP_HOST=0.0.0.0，APP_PORT 用平台分配的端口（没有就默认 8000）
4. python run.py serve

【验收标准】
- GET /api/health 返回 JSON
- GET /login 出现登录页
- 用 admin / admin123 登录后：仪表盘有统计卡片，/assets 有 15 条样本标的
- /assets/2 详情页五维评分明细正常渲染
- /assets/2/report.pdf 能下载 PDF 报告（中文正常显示）

【注意事项】
- 数据库是 SQLite，落在 ./data/app.db，请确保 ./data/ 目录可写且在持久化空间内
- PDF 导出需要中文字体；沙箱若没有中文字体会自动降级为内置 CID 字体
  （STSong-Light），不影响功能；想字更好看可先装 fonts-wqy-zenhei
- 启动命令是常驻进程，请确保后台持续运行并绑定到公网入口
- 验证完成后，帮我把它发布为公网可访问的链接
```

## 八、故障速查

| 症状 | 原因 / 解法 |
|---|---|
| 外部访问 502 / 连不上 | `APP_HOST` 没设成 `0.0.0.0`，或端口映射错 |
| 启动报 `sqlite unable to open` | `./data/` 目录无写权限 |
| PDF 中文变方框 | 无中文字体，装 `fonts-wqy-zenhei` 或确认降级逻辑生效（`app/report/pdf.py`） |
| 登录后立即登出 | `APP_SECRET_KEY` 两侧不一致（多实例部署时），统一配置 |
| 评分结果和预期不符 | 参数被改过；后台「操作日志」有审计记录，或看标的详情页「评分历史」参数快照 |
