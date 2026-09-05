# Coding Style

> Conventions this codebase already follows. Match them; do not introduce a
> second style.

---

## Module Skeleton

Every module under `sequoia_x/` follows the same shape:

```python
"""模块一句话说明：职责 + 关键约束。"""

import sqlite3                       # 1. stdlib
from pathlib import Path

import pandas as pd                  # 2. third party

from sequoia_x.core.config import Settings      # 3. first party
from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)        # module-level logger, immediately after imports
```

Rules:

- **Module docstring is required** and written in Chinese.
- Import groups: stdlib / third-party / first-party, separated by blank lines,
  alphabetical within a group. Ruff's `I` rule enforces this — run
  `ruff check .` before committing.
- Absolute imports only (`from sequoia_x.core.logger import ...`). No relative
  imports anywhere in the repo.
- `logger = get_logger(__name__)` at module level for library modules.
  `main.py` is the exception: it calls `get_logger(__name__)` inside `main()`
  so that config errors surface before logging is set up.

### Deferred (function-local) imports

Heavy or optional dependencies are imported inside the function, not at module
top. This is deliberate and used consistently:

```python
def backfill(self, symbols: list[str]) -> None:
    import time
    from datetime import date, timedelta

    import baostock as bs          # heavy, network-bound, only needed here
```

Use a deferred import when the dependency is: `baostock`, `akshare`,
`multiprocessing.Pool`, or only reachable in one code path. Everything else goes
at module top.

---

## Type Hints

- **Every public function and method is annotated**, including `-> None`.
- Modern builtin generics only: `list[str]`, `dict[str, float]`, `str | None`.
  Never `List`, `Dict`, `Optional` from `typing` — `requires-python = ">=3.10"`
  and ruff's `UP` rule will flag it.
- Local variables get annotations when the type is not obvious from the
  right-hand side: `candidates: list[str] = []`.

---

## Docstrings

Google style, Chinese prose, English section headers:

```python
def get_webhook_url(self, webhook_key: str) -> str:
    """
    根据 webhook_key 返回对应的 Webhook URL。

    优先从 strategy_webhooks 查找，找不到则 fallback 到 feishu_webhook_url。

    Args:
        webhook_key: 策略标识，如 'ma_volume'、'breakout'。

    Returns:
        对应的 Webhook URL 字符串。
    """
```

For strategy classes the class docstring carries the **screening conditions as a
numbered list** — this is the primary documentation of what the strategy does
and must be updated whenever a threshold changes:

```python
class MaVolumeStrategy(BaseStrategy):
    """均线+成交量选股策略。

    选股条件（全部向量化，严禁 iterrows）：
    1. 5日收盘均线上穿20日收盘均线（金叉）
    2. 当日成交量 > 20日均量的 1.5 倍（放量确认）

    Attributes:
        webhook_key: 路由到 'ma_volume' 专属飞书机器人。
    """
```

`Raises:` is documented even when the answer is "never" — see
`sequoia_x/notify/feishu.py:114`.

---

## Naming

| Kind | Convention | Example |
|------|-----------|---------|
| Module | `snake_case`, matches the strategy concept | `high_tight_flag.py` |
| Strategy class | `<CamelName>Strategy` | `HighTightFlagStrategy` |
| Private helper (module) | `_leading_underscore` | `_bs_fetch_batch` |
| Private method | `_leading_underscore` | `_to_baostock_code` |
| Module SQL / format constants | `_UPPER_SNAKE` | `_CREATE_TABLE_SQL`, `_FORMAT` |
| Tunable class constant | `_UPPER_SNAKE` class attr | `_MIN_BARS`, `_LOOKBACK_DAYS` |
| Public class attr as config | `snake_case` class attr | `webhook_key`, `rps_period` |

Numeric literals: use underscores for readability — `100_000_000`, not
`100000000`.

---

## Section Comments

Longer modules use box-drawing separators to mark sections:

```python
    # ── 数据同步 ──
```

Used in `sequoia_x/data/engine.py:95` and `main.py:53`. Follow this when a class
grows past ~3 logical groups of methods.

---

## Strings and Quotes

Double quotes everywhere. `sequoia_x/strategy/rps_breakout.py` uses single
quotes throughout — it is the outlier, not the pattern. Do not copy it.

f-strings for all interpolation, including log messages:
`logger.info(f"{StrategyName} 选出 {len(selected)} 只股票")`.

**Exception:** never f-string into SQL. See
[data/sqlite-guidelines](../data/sqlite-guidelines.md).

---

## Commit Messages

Conventional Commits with a Chinese subject:

```
feat(strategy): 海龟策略选股结果按涨幅降序排列
fix(backfill): 增加重试和自动重连，解决长连接超时问题
chore: .env.example 补充定增策略 webhook 配置示例
docs: README 和 main.py docstring 与项目现状对齐
refactor: 彻底移除 akshare 依赖，全链路走 baostock
```

Types in use: `feat` `fix` `chore` `docs` `refactor` `revert`. Scope is
optional and names the layer or module (`strategy`, `data`, `feishu`,
`backfill`).
