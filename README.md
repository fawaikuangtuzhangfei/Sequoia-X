# Sequoia-X: 王者回归 | The King Returns

> A 股量化选股系统 V2 | A-Share Quantitative Stock Selection System V2

---

## 简介 | Introduction

Sequoia-X V2 是面向 A 股市场的量化选股系统，基于现代 Python 工程化标准从零重构。
系统以 OOP 架构、向量化计算和增量数据更新为核心设计原则，每日收盘后自动选股并推送至飞书群。

数据层使用 [baostock](http://baostock.com)（免费、无需注册、无限流）拉取历史及增量日 K 数据（后复权），
存储于本地 SQLite，彻底规避东方财富反爬问题。

---

## 运行模式

```bash
python main.py                # 日常模式：8进程增量补数据 + 跑策略 + 飞书推送（2~3分钟）
python main.py --backfill     # 回填模式：全市场历史K线一次性灌入（约12分钟）
python main.py --backfill-delisted  # 回填已退市股票，消除回测的幸存者偏差（约1分钟）
python main.py --refresh-names   # 仅刷新股票名称表（几秒）

# 回测：策略到底赚不赚钱
python main.py --replay                              # 历史回放，产出历史选股样本（约2小时）
python main.py --track-returns --source replay       # 算收益并打印汇总报表
python main.py --track-returns                       # 同上，但只算实盘推荐的收益
```

---

## 内置策略 | Strategies

| 策略 | 在找什么 | 结果顺序 |
|---|---|---|
| **TurtleTrade** 海龟突破 | 创 20 日新高，且成交额过亿、当天是实体阳线 | 按流通市值从大到小 |
| **MaVolume** 均线金叉放量 | 5 日均线上穿 20 日均线的当天，成交量 > 20 日均量 ×1.5 | 未排序 |
| **HighTightFlag** 高旗形整理 | 40 日涨幅超 60% 后，近 10 日振幅收窄到 15% 以内且缩量 | 未排序 |
| **LimitUpShakeout** 涨停洗盘 | 涨停次日放量收阴，但最低价没跌破前一日收盘 | 未排序 |
| **UptrendLimitDown** 上升趋势跌停 | MA20 > MA60 的上升趋势中出现放量跌停 | 未排序 |
| **RpsBreakout** RPS 强度突破 | 120 日涨幅排进前 10%，且股价 ≥ 120 日最高价 ×0.9 | 未排序 |
| **PrivatePlacement** 定增公告 | 最近 7 天发布定向增发公告（akshare，非技术形态） | 按发行日期从新到旧 |

上表是摘要。逐条判据、数据来源、最少需要多少历史数据，写在各策略类自身的
`title` / `criteria` 等属性上（与 `run()` 的阈值同处一个文件），
由 `GET /api/strategies/docs` 下发，Web 界面的「策略说明」页直接渲染。

新增策略只需在 `sequoia_x/strategy/registry.py` 里加一行，
`main.py` 会执行它，策略说明页也会自动出现。

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
| GET | `/api/strategies/docs` | 全部已注册策略的说明（含当天没跑出结果的） |
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
│   ├── backtest/                # 收益跟踪与历史回放
│   │   ├── returns.py           # 收益计算（买卖口径、涨停可成交性判定）
│   │   ├── benchmark.py         # 全市场等权基准 + 数据覆盖度告警
│   │   ├── metrics.py           # 按 (策略, 持有期) 汇总指标
│   │   ├── analysis.py          # 分层分析：rank 对照 / 共振 / 选择性
│   │   ├── report.py            # 终端报表 + 固定的偏差声明
│   │   ├── tracker.py           # 编排：刷基准 → 取样本 → 算 → 落库 → 出表
│   │   └── replay.py            # as-of 只读包装 + 逐日回放
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

## 回测 | Backtest

回答"这些策略到底赚不赚钱"。两步：先造样本，再算收益。

```bash
python main.py --backfill-delisted               # 先补退市股，否则结论被幸存者偏差抬高
python main.py --replay                          # 逐个历史交易日重跑策略（约2小时）
python main.py --track-returns --source replay   # 算 T+1/5/10/20 收益并出表
```

`--replay` 把策略放回过去的每一天重跑，结果写入 `selection_replay`。
**它绝不写 `selection_result`**——那张表是实盘推荐的事实记录。

`--backfill-delisted` 不是可选步骤。只用当前在市的股票回测，等于假装
"涨完崩掉退市"的票从不存在，动量类策略会被系统性高估。补进来之后，
候选池按"当日有行情"筛选，退市股在其退市日之后自然退出，不会污染实盘推荐。

`--track-returns` 按下面的口径算收益：

| | |
|---|---|
| 买入 | 推荐日的**下一个交易日**开盘价。该股当天停牌 → 信号无法执行，整条丢弃 |
| 卖出 | 持有 N 个交易日后的收盘价，N = 1 / 2 / 3 / 5 / 10 / 20 |
| 基准 | 全市场等权日收益（由本地行情现算）|
| 超额 | 个股收益 − 同期基准复合收益 |

报表分「全样本」和「可成交样本」两张表。**做决策看后者**——
买入日一字涨停的票根本买不进，把它们算进收益是自欺欺人。

每张表都带「扣费后」两列，按一买一卖合计 0.2% 逐条样本扣除后再统计。
短线尤其要看这两列——0.2% 和短持有期的超额往往是同一量级。

**T+1 那一列打了 `*`，因为它在 A 股执行不了。** 本工具的 T+1 是"次日开盘买、
当日收盘卖"，而 A 股 T+1 交收禁止当日回转。它作为研究口径有意义
（看得出信号的即时强度），但绝不能照着它下单。最短的可执行持有期是 T+2。

改动了收益口径之后要加 `--recompute`，否则已经算过的样本会被跳过，
报表会变成新旧口径的混合物。

### 分层分析

```bash
python main.py --analyze --source replay
```

主报表回答"策略整体赚不赚钱"，分层分析回答"有没有哪一部分是赚钱的"。
只读已落库的明细，不重算。四张表：

| | |
|---|---|
| ① rank 分层 | **对照组**。回放里 rank 不携带信息，这里不该出现跨持有期一致的效应；出现了就先怀疑收益计算漏了未来数据 |
| ② 多策略共振 | 实测**共振越强表现越差**，各持有期单调一致 |
| ③ 选择性 | 策略当天选得越少，选得越准（仅 T+1） |
| ④ 策略内部对照 | ③ 的决定性检验：在每个策略内部按当日选股数中位数切两半，排除混淆 |
| ⑤ 分期对比 | 各季度的扣费后超额，回答"这个策略最近还灵吗" |
| ⑥ 集中度 | 超额来自广泛分布还是少数几只，**决定这个边际能不能用少量仓位吃到** |

⑥ 是里面最该看的一张。平均超额相同的两个策略，一个中位数为正、去掉最好的
1% 后几乎不变（拿几个仓位就能跟），另一个一半以上持仓在亏、超额的一半来自
最好的 1%（必须几乎全买，挑着买是负期望）——这个差别在「平均超额」那一列上
完全看不出来。

想只看最近一段时间，**不需要重跑回放**——样本已经在库里，加 `--since` 切片即可：

```bash
python main.py --analyze --source replay --since 2026-06-01
python main.py --track-returns --source replay --since 2026-06-01
```

⑤ 分期对比刻意**不受 `--since` 限制**。它的价值就在于把最近和以往并排比较，
砍掉历史等于砍掉对照组——孤立地看最近一个季度，没法判断 −0.3% 是策略失效了
还是它一贯如此。

### 结果不可尽信的地方

报表每次都会把这些原样打出来，这里再说一遍：幸存者偏差（本地池只有当前在市的股票）、
后复权序列含未来复权因子、不计手续费印花税滑点、本地池不等于全市场
（RPS 的分位数只相对本地池成立）。此外海龟策略的回放结果不含流通市值排序，
因为那需要当日实时数据。

如果某个策略的胜率高得离谱，**先怀疑口径而不是策略**。

### 三个策略的回放限制

| 策略 | 情况 |
|---|---|
| `TurtleTrade` | 选股可回放；市值排序不可（需当日实时换手率），回放的 rank 是选股顺序 |
| `RpsBreakout` | 已改为经由 `engine.get_market_ohlcv()` 取数，可正常回放 |
| `PrivatePlacement` | 依赖定增公告接口，无历史快照，**排除在回放之外** |

---

## 数据说明

- **数据源**：[baostock](http://baostock.com)（免费、无需注册、无限流）
- **复权方式**：后复权（hfq）— 历史价格不变，适合增量存储，避免除权导致数据错乱
- **存储**：本地 SQLite（`data/sequoia_v2.db`），可直接拷贝到其他机器使用
- **日常增量**：8 进程并行通过 baostock 拉取，2~3 分钟完成全市场更新

回测依赖行情的**完整性**，不只是新鲜度。某段时间只有几百只股票有数据时，
等权基准的噪声会很大，落在那段区间的结论不可信。`--track-returns` 检测到
这种情况会打 WARNING，跑一次 `--backfill` 补齐即可。

---

## 许可证 | License

MIT
