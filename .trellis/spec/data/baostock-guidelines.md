# baostock Guidelines

> The market data source. Free, no registration, no rate limit — but a stateful
> session with a connection that dies if held too long.

---

## Session Lifecycle

`bs.login()` / `bs.logout()` are **process-global session state**, not per-call.
Every block that talks to baostock owns its own login/logout pair:

```python
import baostock as bs

lg = bs.login()
if lg.error_code != "0":
    logger.error(f"baostock 登录失败: {lg.error_msg}")
    return []
try:
    ...
finally:
    bs.logout()
```

Rules:

- **Always import baostock inside the function**, never at module top — see
  [coding-style § deferred imports](../project/coding-style.md#deferred-function-local-imports).
- **Always `logout()` in a `finally`.** `get_all_symbols` and `backfill` both do
  (`engine.py:326`, `engine.py:294-295`). A leaked session is what causes the
  next login to hang.
- **Always check `error_code != "0"`** on both `login()` and every query. baostock
  signals failure through the result object, not exceptions.

---

## Query Shape

```python
rs = bs.query_history_k_data_plus(
    bs_code,                                        # "sh.600519"
    "date,open,high,low,close,volume,amount",       # comma-joined field list
    start_date=start,                               # 'YYYY-MM-DD', inclusive
    end_date=end,
    frequency="d",
    adjustflag="1",
)
if rs.error_code != "0":
    raise RuntimeError(rs.error_msg)

rows = []
while rs.next():
    rows.append(rs.get_row_data())
```

The result set is a **cursor**, consumed with `while rs.next()` + `get_row_data()`.
`rs.fields` gives the column names and is used directly as the DataFrame columns
in `backfill` (`engine.py:263`). Every value comes back as a **string** — see
the cleaning step in
[SQLite guidelines](./sqlite-guidelines.md#data-cleaning-before-write).

### adjustflag

| Value | Meaning | Used for |
|-------|---------|----------|
| `"1"` | 后复权 hfq | **Everything stored in `stock_daily`** (`engine.py:46`, `engine.py:229`) |
| `"3"` | 不复权 raw | Only where a real-world price is needed — market cap in `turtle_trade.py:49` |

Never store `"3"` data in `stock_daily`, and never compute market cap from
`"1"` data. Mixing them silently produces wrong numbers, not errors.

---

## Retry and Reconnect (backfill)

`backfill` is the reference implementation (`engine.py:158-297`). Its three
defences exist because of a real production failure — long runs used to die
partway through (commit `444c0db`):

1. **Per-symbol retry**: 3 attempts, sleep `2 ** (attempt + 1)` → 2s / 4s / 8s,
   with a `logout()` + `login()` reconnect between attempts.
2. **Periodic reconnect**: every `reconnect_interval = 200` processed symbols,
   logout, sleep 1s, login again. This is the fix for long-connection timeout.
3. **Resume on restart**: `_get_last_date(symbol)` skips symbols already current,
   so a killed backfill is re-runnable with no flags.

Tallies (`success` / `skipped` / `failed`) are logged every 500 symbols and once
at the end. Any new long-running loop over the market should copy this shape.

Constants live as **local variables at the top of the function**
(`max_retries = 3`, `reconnect_interval = 200`). If you add a third tunable,
promote all of them to module-level `_UPPER_SNAKE` constants rather than
scattering more magic numbers.

---

## Multiprocessing (daily sync)

`sync_today_bulk` splits work across 8 processes:

```python
n_workers = min(8, len(tasks))
chunks = [tasks[i::n_workers] for i in range(n_workers)]   # round-robin, not contiguous
with Pool(n_workers) as pool:
    batch_results = pool.map(_bs_fetch_batch, chunks)
```

Non-negotiable constraints:

- **`_bs_fetch_batch` is a module-level function** (`engine.py:34`), not a method
  or closure — it must be picklable, and on Windows the child re-imports the
  module. Keep any new worker at module level.
- **Each worker calls `bs.login()` itself** and `bs.logout()` at the end. A
  session created in the parent does not survive the fork/spawn.
- **Workers pass only plain data** — `(symbol, bs_code, start, end)` tuples in,
  `list[list[str]]` out. No `DataEngine`, no `Settings`, no connection.
- **Workers do not write to SQLite.** They return rows; the parent does one
  batched write. Concurrent SQLite writers from 8 processes would deadlock.
- Round-robin chunking (`tasks[i::n_workers]`) spreads slow symbols evenly. Do
  not switch to contiguous slices.

Worker-side errors are swallowed with `continue` (`engine.py:49`) — a failed
symbol in daily mode is simply absent from that day's write and gets picked up
by the next run's incremental range.

---

## Beyond the Data Layer

Two other places call baostock directly:

- `sequoia_x/notify/feishu.py:41` — `_get_stock_names`, one
  `query_stock_basic` per selected symbol. Acceptable because it only runs on
  the handful of symbols a strategy actually selected.
- `sequoia_x/strategy/turtle_trade.py:39` — `_get_market_caps`, one query per
  candidate for `adjustflag="3"` prices.

Both follow the login/try/finally-logout shape. Neither has retry logic; if
either becomes flaky, factor the retry helper out of `backfill` rather than
duplicating it a third time.
