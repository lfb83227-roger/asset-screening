# AI资产智能筛选与评分系统（一期）

不良资产线上公开数据初筛工具。全网采集 → 一票否决风控 → 五维量化评分 → 标签生成 →
分级归档 → 标准化 PDF 报告导出，全流程可配置、可复现、可审计。

> **合规边界（系统永久保留）**
> 本系统所有测算数据、评分结果仅为线上公开数据初筛参考，不构成投资建议、资产收益承诺、
> 过户担保。所有标的最终准入、尽调、决策权归人工专业团队，必须经过线下实地深度尽调复核。
> 系统仅采集平台公开公示信息，无法识别线下隐性风险（私下租约、口头协议、隐性欠款、实地占用等）。

---

## 一、快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 初始化（建表 + 出厂参数 + 默认账号 + 内置样本标的）
python run.py init --with-samples

# 3. 启动服务
python run.py serve

# 打开 http://127.0.0.1:8000
```

默认账号（**上线前必须全部修改**）：

| 账号 | 口令 | 角色 | 权限 |
|---|---|---|---|
| `admin` | `admin123` | 系统管理员 | 全部，含参数配置与账号管理 |
| `operator` | `operator123` | 业务操作 | 标的增删改、采集、导入、报告导出 |
| `viewer` | `viewer123` | 只读查看 | 仅浏览与报告导出 |

### 命令行

```bash
python run.py init [--with-samples] [--reset]  # 初始化 / 清库重建
python run.py serve [--reload]                 # 启动 Web 服务
python run.py crawl [平台]                      # 执行一轮采集（默认 all）
python run.py schedule --interval 1800         # 7×24 循环采集
python run.py reevaluate                       # 按当前参数全量重算
python run.py report <asset_id>                # 导出指定标的 PDF 报告
python run.py stats                            # 打印评分概览
```

### 测试

```bash
python tests/test_engine.py    # 引擎单元测试（24 例，无需数据库）
python tests/test_web.py       # Web 集成测试（21 例，独立临时库）
```

---

## 二、需求实现对照

| PRD 章节 | 需求 | 实现位置 | 状态 |
|---|---|---|---|
| 二 | 五大数据源 7×24 自动采集 | `app/crawler/adapters/`、`run.py schedule` | ✅ 管道完整，适配器待接授权 |
| 模块1 | 采集 / 去重 / 结构化 / 手动录入 | `app/crawler/{service,dedup,normalizer}.py` | ✅ |
| 模块2 | 一票否决风险拦截（最高优先级） | `app/core/veto.py` + `veto_rules` 表 | ✅ 5 条规则全覆盖 |
| 模块3 | 五维量化评分（100 分） | `app/core/scoring.py` | ✅ 权重与锚点可配 |
| 模块4 | A/B/C 自动分级 | `app/core/scoring.py::grade_of` | ✅ 阈值可配 |
| 模块5 | 风险与优势标签自动生成 | `app/core/tags.py`（30 个标签） | ✅ 可单独开关 |
| 模块6 | 智能标的报告 PDF 导出 | `app/report/pdf.py` | ✅ 六项固定内容 + 强制免责声明 |
| 四 | 净租售比公式固定写入 | `app/core/rental.py` | ✅ 空置统一预留 10% |
| 五 | 后台参数自定义、权限、日志 | `app/routers/admin.py` | ✅ |
| 六 | 一期禁止功能 | 见 `app/config.py` 开关 | ✅ 未实现 |
| 八 | 验收标准 7 条 | `tests/` 45 个用例对应覆盖 | ✅ |

### 五维评分权重（出厂）

| 维度 | 满分 | 打分依据 |
|---|---|---|
| 价格折价 | 30 | 起拍价相对参考价（优先周边成交价，其次评估价）的折价率，锚点表插值 |
| **净租售比现金流** | **30** | 核心指标。≥5% 满分；4%~5% 梯度扣分；<4% 大幅扣分 |
| 产权司法风险 | 20 | 抵押数、轮候查封数、涉诉数、纠纷频次，按项扣分并封顶 |
| 区位流通性 | 15 | 城市能级 6 + 产业配套 3 + 出租需求 3 + 转手成交率 3 |
| 权属合规补充 | 5 | 土地剩余年限 2 + 无隐性共有产权 1.5 + 合规等级 1.5 |

分级：**A ≥ 80**（进尽调池）· **B 60–79**（选择性复核）· **C < 60 或触发否决**（直接剔除）。

### 净租售比公式

```
净租售比 =（预估年毛租金 − 年度税费 − 年度修缮运维费 − 空置损耗预留）
           ÷ 标的成交预估价值 × 100%
```

* 年度税费 = 年毛租金 × 房产税率（从租计征） + 土地使用税单价 × 土地面积
* 空置损耗预留 = 年毛租金 × **10%**（PRD 明确要求，可在后台调整）
* 租金、税费调用区域大数据均值，逐级匹配：`城市+区县+类型` → `城市+类型` → 系统默认

---

## 三、目录结构

```
AI资产智能筛选与评分系统/
├── run.py                      # CLI 入口（init / serve / crawl / schedule / report / stats）
├── requirements.txt
├── app/
│   ├── main.py                 # FastAPI 装配
│   ├── config.py               # 路径、数据库、业务开关、字体、免责声明
│   ├── database.py             # 引擎与会话
│   ├── models.py               # 10 张表（资产/评分历史/区域基准/否决规则/参数/日志/用户…）
│   ├── security.py             # 口令哈希、会话签名、角色校验、操作日志
│   ├── bootstrap.py            # 初始化引导
│   ├── webutils.py             # 模板渲染、过滤器、导航
│   ├── core/                   # ★ 业务核心（纯逻辑，不依赖 Web）
│   │   ├── constants.py        #   出厂参数与规则种子（唯一的参数来源）
│   │   ├── ruleset.py          #   参数装载 + 锚点插值
│   │   ├── veto.py             #   模块2 一票否决（含关键词否定护栏）
│   │   ├── rental.py           #   净租售比测算
│   │   ├── benchmark.py        #   区域基准逐级匹配
│   │   ├── scoring.py          #   模块3 五维评分
│   │   ├── tags.py             #   模块5 标签生成
│   │   └── pipeline.py         #   评估流水线 + 批量重算
│   ├── crawler/                # 模块1 采集
│   │   ├── base.py             #   适配器基类（限速、超时、选择器映射）
│   │   ├── adapters/           #   5 个平台适配器
│   │   ├── normalizer.py       #   字段归一 + 公告文本结构化
│   │   ├── dedup.py            #   同源/跨平台两级去重
│   │   ├── importer.py         #   CSV / XLSX 批量导入
│   │   ├── samples.py          #   内置样本（覆盖 A/B/C 与 V1~V5 全分支）
│   │   ├── registry.py         #   适配器注册表
│   │   └── service.py          #   采集调度与入库评分
│   ├── report/pdf.py           # 模块6 PDF 报告
│   ├── routers/                # 页面与 API 路由
│   ├── templates/              # 13 个 Jinja2 模板
│   └── static/style.css
├── data/
│   ├── app.db                  # SQLite（可用 APP_DB_URL 切 PostgreSQL）
│   ├── reports/                # 导出的 PDF
│   └── logs/
└── tests/                      # 引擎单测 + Web 集成测试
```

---

## 四、设计要点（几个值得说明的工程决策）

### 1. 参数即数据，规则不改代码

所有评分参数（权重、锚点表、扣分系数、标签阈值、否决规则、区域基准）都存在数据库里，
后台可视化编辑。**打分算法只有一处实现** —— `ruleset.interp()` 的分段线性插值，
其余全是数据的组合。改一次权重不需要发版。

### 2. 结果可复现（PRD 验收标准 3）

引擎不使用随机数、不调用外部服务、不依赖系统时间做判定。同一份标的 + 同一份参数，
必然得出逐字节相同的结果（`tests/test_engine.py::test_result_is_reproducible`）。
每次评分还会把当次**全量参数快照**写入 `score_history.params_snapshot`，
事后可以精确还原"这个分数当时是按什么参数算出来的"。

### 3. 关键词匹配带否定护栏

PRD 一期不做大模型语义解析，只能靠关键词。但纯子串匹配遇到否定表述会严重误判 ——
"不属于**无法清退**情形"会被当成"无法清退"。

系统在 `app/core/veto.py` 里加了一个确定性的轻量护栏：命中关键词后回看前 6 个字，
出现「不 / 未 / 非 / 无 / 不属于 / 并非 / 排除」等否定词则不计命中。
被拦下的判定会连同原文片段一起写进证据里，复核人员可随时审计。

这是本次开发中发现的**真实误杀案例**（样本里"成都市龙泉驿区汽车产业园标准厂房"
原本被判 V2 否决，修正后正确回到 A 类 82.72 分）。

### 4. 显式值优先，正则只填空

公告原文的结构化抽取永远不会覆盖人工录入或表格填写的值 —— 归一化阶段会记录
`_explicit_fields`，抽取器只填这些字段之外的空白。人工录入的结果什么时候都不会被机器冲掉。

### 5. 采集适配器与管道分离

换平台 = 新增一个 adapter 文件 + 在 `registry.py` 注册，翻页/限速/去重/入库/评分
这些公共逻辑一行不改。页面选择器写在类属性 `selector_map` 里而不是硬编码，
平台改版时运维改配置即可。

---

## 五、代码质量与测试

```
python tests/test_engine.py   →  24 通过 / 0 失败
python tests/test_web.py      →  21 通过 / 0 失败
```

引擎单测覆盖：锚点插值的数学正确性、V1~V5 全部否决分支、关键词否定护栏、
净租售比公式逐项对账、五维得分与分级边界、可复现性。

Web 集成测试覆盖：登录与三种角色的权限边界、全部页面渲染、
采集→去重→评分全链路、GBK 编码的 CSV 导入、PDF 导出、参数热调整与重算、JSON API。

---

## 六、上线前的必做事项

1. **修改默认口令**，或删除 `operator`/`viewer` 演示账号。
2. **设置强随机 `APP_SECRET_KEY`**（默认值是开发用的，绝不可用于生产）：
   ```bash
   set APP_SECRET_KEY=<64位随机字符串>
   ```
3. **切换数据库**（建议 PostgreSQL）：
   ```bash
   set APP_DB_URL=postgresql+psycopg://user:pwd@host:5432/assetdb
   ```
4. **采集授权**：阿里拍卖、京东司法拍卖等平台均有反爬机制与访问条款限制，
   系统默认以**样本模式**运行，不会对外发起任何请求。启用真实采集前须完成：
   ① 法务确认平台 robots.txt 与用户协议并取得书面授权；
   ② 按平台当前实际 DOM 校正 `selector_map`；
   ③ 把该适配器的 `enabled` 与 `authorization_confirmed` 置为 `True`。
5. **区域基准数据**：内置的是样本城市数据，正式使用前应由数据团队补充目标城市的
   租金、税费、空置率、流动性等均值（后台「区域基准」页可手工维护）。
6. **定时采集**：用系统计划任务调用 `python run.py crawl`，或常驻 `python run.py schedule`。

---

## 七、技术栈

Python 3.12+ · FastAPI · SQLAlchemy 2.0 · SQLite/PostgreSQL · Jinja2 服务端渲染 ·
ReportLab（PDF，中文字体三级降级）· httpx + BeautifulSoup（采集）· openpyxl（Excel 导入）

无前端构建步骤，一条命令启动，便于交付外包团队继续开发。
