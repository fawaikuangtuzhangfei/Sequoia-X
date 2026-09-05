# Strategy Contract

> What every `BaseStrategy` subclass must honour.

---

## The Base Class

```python
class BaseStrategy(ABC):
    webhook_key: str = "default"

    def __init__(self, engine: DataEngine, settings: Settings) -> None:
        self.engine = engine
        self.settings = settings

    @abstractmethod
    def run(self) -> list[str]: ...
```

`sequoia_x/strategy/base.py`. Do not add abstract methods to it without
updating all seven subclasses — there is no CI to catch a missed one.

---

## `run()` Contract

| Aspect | Requirement |
|--------|-------------|
| Signature | `def run(self) -> list[str]` — no arguments |
| Return | Bare 6-digit codes, e.g. `["000001", "600519"]` |
| No hits | Return `[]`. Never `None`, never raise |
| Ordering | Meaningful if the strategy defines one (see below), otherwise DB order |
| Side effects | None. No DB writes, no notifications, no file I/O |
| Duration | Runs over ~5000 symbols; keep per-symbol work cheap |

`main.py:86` treats an empty list as "skip the push" — that is the only signal
for "nothing found", so returning `[]` on an internal failure is
indistinguishable from a clean no-hit day. That is the accepted trade-off; log
an ERROR before returning `[]` on failure so the difference is visible in the
log.

**Ordering carries meaning where it exists.** `TurtleTradeStrategy` sorts by
circulating market cap descending (`turtle_trade.py:106-108`);
`PrivatePlacementStrategy` sorts by announcement date descending. The Feishu
card renders symbols in list order, so the order is user-visible. Document it
in the class docstring when you define one.

---

## `webhook_key`

```python
class MaVolumeStrategy(BaseStrategy):
    webhook_key: str = "ma_volume"
```

A **class attribute**, lowercase snake_case, never set in `__init__`.
`main.py:90` reads it off the instance and passes it to `notifier.send`.

The env var is `STRATEGY_WEBHOOK_<UPPERCASE_KEY>`. Nothing validates the link —
a mismatch silently routes to the default bot. When you add or rename a key,
grep `.env.example` in the same edit.

---

## Per-Symbol Loop Template

This is the shape five of the seven strategies use. Every element is load-bearing:

```python
class ExampleStrategy(BaseStrategy):
    """一句话策略说明。

    选股条件（向量化，严禁 iterrows）：
    1. ...
    2. ...

    Attributes:
        webhook_key: 路由到 'example' 专属飞书机器人。
    """

    webhook_key: str = "example"
    _MIN_BARS: int = 60          # longest rolling window + 1

    def run(self) -> list[str]:
        symbols = self.engine.get_local_symbols()
        selected: list[str] = []

        for symbol in symbols:
            try:
                df = self.engine.get_ohlcv(symbol)
                if len(df) < self._MIN_BARS:      # 1. length guard
                    continue

                df["ma20"] = df["close"].rolling(20).mean()   # 2. vectorized

                prev = df.iloc[-2]
                today = df.iloc[-1]

                if pd.isna(prev["ma20"]):          # 3. NaN guard
                    continue

                if cond_a and cond_b:              # 4. named boolean conditions
                    selected.append(symbol)

            except Exception as exc:               # 5. per-symbol isolation
                logger.warning(f"[{symbol}] ExampleStrategy 计算失败：{exc}")
                continue

        logger.info(f"ExampleStrategy 选出 {len(selected)} 只股票")   # 6. count line
        return selected
```

1. **`_MIN_BARS` class constant** guarding `len(df)`. Set it to the longest
   window your computation needs plus the lookback rows you index
   (`turtle_trade.py:24` uses 21 for a 20-day window + today).
   `MaVolumeStrategy` inlines `20` instead — prefer the named constant.
2. **Vectorized only.** See [pandas-guidelines](./pandas-guidelines.md).
3. **NaN guard after rolling windows.** `rolling(n)` yields `NaN` for the first
   `n-1` rows, and `len(df) >= _MIN_BARS` does not guarantee the specific cell
   you read is non-NaN when data has gaps. `uptrend_limit_down.py:50` and
   `turtle_trade.py:86` both guard explicitly.
4. **Name each condition** as its own boolean (`golden_cross`, `volume_surge`,
   `support_hold`) and combine in one `if`. This keeps the class docstring's
   numbered conditions traceable to code. Do not write one giant boolean
   expression.
5. **Broad `except Exception` per symbol**, WARNING log with `[{symbol}]`
   prefix, `continue`. This is the one place a bare-broad catch is correct — a
   single malformed symbol must not lose the other 5000.
6. **Count line** immediately before `return`.

---

## Thresholds Belong in Named Constants

Current state is inconsistent. Some strategies name their tunables:

```python
_MIN_BARS: int = 40
_LOOKBACK_DAYS: int = 7
rps_period: int = 120
rps_threshold: int = 90
```

Others inline them:

```python
liquid = last["turnover"] > 100_000_000                 # turtle_trade.py:92
volume_surge = last["volume"] > last["vol_ma20"] * 1.5  # ma_volume.py:53
limit_up_yesterday = prev1["close"] >= prev2["close"] * 1.095   # limit_up_shakeout.py:49
```

**New code names them.** Class-level `_UPPER_SNAKE` with a type annotation, and
the value repeated in the docstring's numbered condition list. When you change a
threshold, the docstring changes in the same edit — the docstring is the only
documentation of what a strategy does.

---

## External-Data Strategies

`PrivatePlacementStrategy` is the template when the data is not in
`stock_daily`:

```python
def run(self) -> list[str]:
    try:
        import akshare as ak
        df = ak.stock_qbzf_em()
    except Exception as exc:
        logger.error(f"PrivatePlacementStrategy 获取定增数据失败：{exc}")
        return []

    if df is None or df.empty:
        logger.info("PrivatePlacementStrategy 无定增数据")
        return []
    ...
```

- Fetch wrapped in `try/except` → **ERROR** log (not WARNING — the whole
  strategy failed) → `return []`.
- Guard `df is None or df.empty` separately; a scraped source can return either.
- Extract codes defensively:
  `df["股票代码"].astype(str).str.extract(r"(\d{6})")[0].dropna().tolist()` —
  never assume the upstream column is already a clean 6-digit string.
- Deduplicate while preserving order (`private_placement.py:65-70`), because
  order is meaningful here.
- Chinese column names from akshare (`"发行方式"`, `"发行日期"`) are the upstream
  API's schema. They can change without notice — that is why the whole path is
  defensive.

---

## `rps_breakout.py` Is Not the Style Reference

Its whole-market approach is sound; its style is not. When copying it, fix:

- Missing module docstring (every other module has one).
- Import order violates ruff `I` (`sequoia_x...` before `sqlite3`).
- Single-quoted strings throughout — the repo uses double quotes.
- Raw `sqlite3.connect(self.engine.db_path)` instead of a `DataEngine` method.
- No `_MIN_BARS`-equivalent guard; relies on `dropna` instead.
- `rps_period` / `rps_threshold` are public class attrs; other strategies use
  `_UPPER_SNAKE` private constants for the same role.

Similarly, `turtle_trade.py:42` calls `self.engine._to_baostock_code` — reaching
into a private method of another layer. Do not replicate that; if you need the
converter, make it public on `DataEngine` first.
