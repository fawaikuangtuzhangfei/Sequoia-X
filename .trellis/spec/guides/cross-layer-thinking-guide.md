# Cross-Layer Thinking Guide

> **Purpose**: Trace the data before you change it. Most bugs in this project
> live at the boundary between two layers, not inside one.

---

## The Layers

```
baostock / akshare
        │  strings, exchange-prefixed codes, adjustflag-dependent prices
        ▼
   data (DataEngine)
        │  cleaned floats, bare 6-digit codes, hfq prices, TEXT dates
        ▼
   strategy (BaseStrategy.run)
        │  list[str] of bare codes, order may be meaningful
        ▼
   main.py
        │  (symbols, strategy class name, webhook_key)
        ▼
   notify (FeishuNotifier)
        │  Xueqiu-prefixed codes, stock names, lark_md
        ▼
   Feishu bot
```

`core` sits beside all of them: every layer reads `Settings` and `get_logger`.

---

## The Five Boundaries

### 1. baostock/akshare → data

| Question | Answer in this repo |
|----------|--------------------|
| What type comes back? | **Strings**, always. Including `""` for suspended days |
| How is failure signalled? | `rs.error_code != "0"`, not an exception |
| Who cleans it? | `data` — `pd.to_numeric(errors="coerce")` → `dropna(close)` → `volume > 0` |
| What does the code look like? | `sh.600519` — prefix must be stripped before storage |
| Are prices comparable across time? | Only with `adjustflag="1"` (hfq). Mixing in `"3"` silently corrupts |

**If you change this boundary**, the cleaning step and the `stock_daily` column
list both move together. See [data/sqlite-guidelines](../data/sqlite-guidelines.md).

### 2. data → strategy

The contract is `get_ohlcv(symbol) -> pd.DataFrame` with columns
`id, symbol, date, open, high, low, close, volume, turnover`, ordered by date
ascending.

| Question | Answer |
|----------|--------|
| Is `date` a datetime? | **No** — TEXT `'YYYY-MM-DD'`. Convert only if you need `.dt` |
| Is `turnover` a rate? | **No** — it is 成交额 in CNY (baostock `amount`) |
| Can it be empty or short? | Yes. Guard with `_MIN_BARS` before `iloc[-2]` |
| Are there gaps? | Yes — suspended days were filtered out on write, so calendar gaps exist. `rolling(20)` means 20 *rows*, not 20 calendar days |

**Adding a column** to `stock_daily` means: `_CREATE_TABLE_SQL` + a migration
(the `IF NOT EXISTS` DDL will not alter an existing table) + both write paths'
column lists + the cleaning loop. Strategies pick it up for free via
`SELECT *`, but only after every deployed DB is migrated.

### 3. strategy → main.py

`run() -> list[str]`. Everything `main.py` knows about a strategy is this list,
`type(strategy).__name__`, and `strategy.webhook_key`.

| Question | Answer |
|----------|--------|
| How is "nothing found" signalled? | `[]` — and `main.py:86` skips the push |
| How is "the strategy broke" signalled? | Also `[]`, plus an ERROR log. There is no other channel |
| Does order matter? | Yes if the strategy defines one; the card renders in list order |
| Can `run()` raise? | It shouldn't. An escaped exception kills the **whole run** via `main.py`'s top-level handler — remaining strategies never execute |

That last row is the highest-consequence coupling in the codebase. `main.py`
does not wrap individual `strategy.run()` calls, so one unguarded exception
loses every subsequent strategy's push. This is exactly why the per-symbol
`try/except` in the loop template is mandatory.

### 4. main.py → notify

`send(symbols, strategy_name, webhook_key)`.

| Question | Answer |
|----------|--------|
| Where does the routing decision live? | `Settings.get_webhook_url`, called inside `send` |
| What happens on an unknown `webhook_key`? | Silent fallback to the default bot. **No error** |
| What happens on a failed POST? | ERROR log, `send` returns normally, loop continues |
| What is `strategy_name`? | The class name — it is user-visible in the card header |

### 5. notify → Feishu

| Question | Answer |
|----------|--------|
| Is HTTP 200 success? | **No.** Also requires body `code == 0` |
| What code format does the card use? | Xueqiu (`SH600519`), converted in `notify` |
| What if a stock name lookup fails? | Falls back to the Xueqiu code as the label |

---

## Change Recipes

### Adding a strategy — 4 layers

| Layer | Edit |
|-------|------|
| `strategy/` | New file, subclass, `webhook_key`, `run()` |
| `main.py` | Import + append to the `strategies` list |
| config | `STRATEGY_WEBHOOK_<KEY>` in `.env.example` **and** the deployed `.env` |
| docs | Strategy table in `README.md` |

Missing the `main.py` step means the strategy silently never runs. Missing the
env var means it posts to the default bot. Neither fails loudly.

### Adding a `Settings` field — 3 layers

| Layer | Edit |
|-------|------|
| `core/config.py` | Field with type + default |
| config | `.env.example` entry with a Chinese comment |
| consumer | Copy into that class's `__init__` (`self.x = settings.x`) |

`extra="ignore"` means a misspelled env var is dropped silently. There is no
validation safety net.

### Changing a `stock_daily` column — 3 layers, plus deployed data

`data/engine.py` DDL → both write-path column lists → the cleaning loop →
every strategy reading that column → **a migration for existing DB files**, which
this project has no mechanism for. Plan the migration explicitly.

### Changing what `run()` returns

Touches `main.py`'s emptiness check, `FeishuNotifier.send`'s signature, the card
builder, and `tests/test_strategy.py`'s Property 9. Prefer adding a new optional
class attribute over changing the return type.

---

## Trust Boundaries

Useful when triaging a "missing validation" review comment:

| Source | Trusted? | Handling |
|--------|----------|----------|
| `.env` / env vars | Yes (operator-controlled) | Pydantic type validation only |
| SQLite `stock_daily` | Yes (we wrote it, cleaned) | Length/NaN guards, not sanitisation |
| baostock responses | **No** — strings, empty values, error codes | Coerce, drop, check `error_code` |
| akshare responses | **No** — scraped, Chinese column names, schema can shift | Wrap in `try/except`, defensive regex extraction |
| Feishu responses | **No** | Check `status_code` *and* `code` |

There is no untrusted end-user input anywhere in this system. Injection-style
findings against `stock_daily` reads are false positives — but the `?`
placeholder rule stands anyway, because it is also the correct way to handle
quoting.
