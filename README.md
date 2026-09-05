# Sequoia-X: 王者回归 | The King Returns

> A 股量化选股系统 V2 | A-Share Quantitative Stock Selection System V2

---

## 简介 | Introduction

Sequoia-X V2 是面向 A 股市场的量化选股系统，基于现代 Python 工程化标准从零重构。
系统以 OOP 架构、向量化计算和增量数据更新为核心设计原则，每日收盘后自动选股并推送至飞书群。

数据层使用 [baostock](http://baostock.com)（免费、无需注册、无限流）拉取历史及增量日 K 数据（后复权），
存储于本地 SQLite，彻底规避东方财富反爬问题。

---

## 两种运行模式

```bash
python main.py               # 日常模式：8进程增量补数据 + 跑策略 + 飞书推送（2~3分钟）
python main.py --backfill     # 回填模式：全市场历史K线一次性灌入（约12分钟）
```

---

## 内置策略 | Strategies

| 策略 | 说明 |
|---|---|
| **TurtleTrade** | 海龟突破：20日新高 + 成交额过亿 + 阳线防诱多，按涨幅排序 |
| **MaVolume** | 均线+放量突破 |
| **HighTightFlag** | 高而窄的旗形整理突破 |
| **LimitUpShakeout** | 涨停洗盘回踩确认 |
| **UptrendLimitDown** | 上升趋势中的跌停反包 |
| **RpsBreakout** | 欧奈尔 RPS 相对强度突破 |

---

## 快速开始 | Quick Start

### 环境要求

- Python >= 3.10

### 1. 安装依赖

```bash
# 推荐使用 uv（快速包管理器）
uv sync

# 或者 pip
pip install .
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填写飞书 Webhook URL
```

### 3. 首次回填历史数据

```bash
python main.py --backfill
```

约 12 分钟完成 ~5200 只 A 股历史后复权日 K 数据回填。

### 4. 日常运行

```bash
python main.py
```

建议配合 crontab 每个交易日收盘后自动执行：

```cron
15 19 * * 1-5 cd /root/Sequoia-X && .venv/bin/python main.py >> log.txt 2>&1
```

---

## Web 界面 | Web UI

除飞书推送外，选股结果会写入数据库，可通过网页翻阅历史记录。
`main.py` 与 Web 服务是两个独立进程，共用同一个 SQLite 文件，
**不启 Web 服务完全不影响选股和推送**。

### 构建并启动

```bash
cd frontend && npm install && npm run build && cd ..
python serve.py
```

浏览器打开 <http://127.0.0.1:8000>。

### 股票名称

页面上的股票名称来自 `stock_basic` 表，它在 `--backfill` 时被顺带填充。
如果你从未跑过回填、只用日常模式，名称列会显示 `—`。刷新一次即可（约几秒）：

```bash
python main.py --refresh-names
```

名称是纯展示信息，缺失不影响选股、推送和查询。

默认只监听 `127.0.0.1`（仅本机）。需要内网其它机器访问时：

```bash
python serve.py --host 0.0.0.0 --port 8000
```

> 接口没有鉴权，**请勿直接暴露到公网**。

### 前端开发模式

```bash
python serve.py            # 终端 1：后端
cd frontend && npm run dev # 终端 2：前端，带热更新
```

开发态访问 Vite 给出的地址（默认 <http://127.0.0.1:5173>），
`/api` 请求由 `vite.config.ts` 的 proxy 转发到后端。

`frontend/dist` 不存在时，`serve.py` 会自动降级为纯 API 服务——
只想用接口、不想装 Node 的话可以跳过整个构建步骤。

### 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 存活检查 |
| GET | `/api/dates` | 有选股数据的日期，倒序 |
| GET | `/api/strategies` | 有选股数据的策略名 |
| GET | `/api/selections` | 查询选股结果，支持 `date` / `strategy` / `page` / `page_size` |

交互式文档见 <http://127.0.0.1:8000/docs>。

---

## 目录结构 | Project Structure

```
Sequoia-X/
├── main.py                      # 入口：argparse 分发日常/回填模式
├── serve.py                     # 入口：启动 Web 查询服务
├── pyproject.toml               # 依赖声明 + ruff/pytest 配置
├── .env.example                 # 环境变量模板
├── data/                        # SQLite 数据库（运行时生成，不入 git）
├── sequoia_x/
│   ├── core/
│   │   ├── config.py            # Pydantic-settings 配置管理
│   │   ├── logger.py            # rich 结构化日志
│   │   └── symbols.py           # 股票代码格式转换
│   ├── data/
│   │   └── engine.py            # 数据引擎（baostock 回填 + 增量同步 + SQLite）
│   ├── strategy/
│   │   ├── base.py              # 策略抽象基类
│   │   ├── turtle_trade.py      # 海龟交易策略
│   │   ├── ma_volume.py         # 均线放量策略
│   │   ├── high_tight_flag.py   # 高窄旗形策略
│   │   ├── limit_up_shakeout.py # 涨停洗盘策略
│   │   ├── uptrend_limit_down.py # 上升跌停策略
│   │   ├── rps_breakout.py      # RPS 突破策略
│   │   └── private_placement.py # 定增公告监控策略
│   ├── notify/
│   │   └── feishu.py            # 飞书 Webhook 推送
│   └── api/                     # 选股结果只读查询接口（FastAPI）
│       ├── app.py               # create_app()：路由 + 静态资源挂载
│       ├── schemas.py           # 响应模型
│       ├── deps.py              # 依赖注入
│       └── routers/selections.py
├── frontend/                    # 结果浏览页面（React + TypeScript + Vite）
│   └── src/
│       ├── api.ts               # 接口封装
│       ├── App.tsx              # 状态编排
│       └── components/          # Filters / SelectionList
└── tests/                       # 属性测试（hypothesis）
```

---

## 数据说明

- **数据源**：[baostock](http://baostock.com)（免费、无需注册、无限流）
- **复权方式**：后复权（hfq）— 历史价格不变，适合增量存储，避免除权导致数据错乱
- **存储**：本地 SQLite（`data/sequoia_v2.db`），可直接拷贝到其他机器使用
- **日常增量**：8 进程并行通过 baostock 拉取，2~3 分钟完成全市场更新

---

## 许可证 | License

MIT
