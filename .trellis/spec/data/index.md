# data — Storage & Market Data Sync

> `sequoia_x/data/engine.py`. One class, `DataEngine`, owns both the SQLite
> schema and every baostock call in the sync path.

---

## Docs

| Doc | Read when |
|-----|-----------|
| [SQLite Guidelines](./sqlite-guidelines.md) | Any query, write, schema, or connection change |
| [baostock Guidelines](./baostock-guidelines.md) | Any call to the market data API, retries, multiprocessing |

---

## Public Surface

| Method | Returns | Notes |
|--------|---------|-------|
| `get_ohlcv(symbol)` | `pd.DataFrame` | All bars for one symbol, `ORDER BY date`. The read path every strategy uses. |
| `get_local_symbols()` | `list[str]` | Distinct symbols already in the DB. Cheap. |
| `get_all_symbols()` | `list[str]` | Full A-share list from baostock. Network call — backfill only. |
| `sync_today_bulk()` | `int` | 8-process incremental sync; returns rows written. Daily mode. |
| `backfill(symbols)` | `None` | Single-process history load with retry/reconnect. |

Everything else (`_init_db`, `_get_last_date`, `_to_baostock_code`,
`_bs_fetch_batch`) is private. **Strategies must not call private members** —
`sequoia_x/strategy/turtle_trade.py:42` calls `self.engine._to_baostock_code`,
which is a boundary violation to fix, not a pattern to copy. If a strategy needs
a converter, promote it to a public method or a shared helper first.

---

## Schema

```sql
CREATE TABLE IF NOT EXISTS stock_daily (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol   TEXT    NOT NULL,
    date     TEXT    NOT NULL,     -- 'YYYY-MM-DD'
    open     REAL,
    high     REAL,
    low      REAL,
    close    REAL,
    volume   REAL,
    turnover REAL,
    UNIQUE (symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_symbol_date ON stock_daily (symbol, date);
```

Facts every consumer depends on:

- **Prices are hfq (后复权, `adjustflag="1"`).** Historical values never change
  as new dividends occur, which is what makes append-only incremental storage
  correct. Never mix in raw or qfq prices — see
  [baostock guidelines](./baostock-guidelines.md#adjustflag).
- `date` is a **TEXT** `'YYYY-MM-DD'` string, so lexicographic comparison equals
  chronological comparison. `_get_last_date` and `sync_today_bulk` rely on this
  (`last_date >= today_str`). Strategies that need real dates convert on read:
  `pd.to_datetime(df['date'])`.
- `turnover` is baostock's `amount` field (成交额, CNY), renamed on write
  (`engine.py:274`). It is **not** turnover rate (换手率). `TurtleTradeStrategy`
  compares it against `100_000_000` as a yuan amount.
- `UNIQUE (symbol, date)` is the only dedup mechanism. Both write paths lean on
  it — see [SQLite guidelines](./sqlite-guidelines.md#idempotent-writes).
- The DB file is at `settings.db_path` (default `data/sequoia_v2.db`),
  gitignored, and portable — it can be copied between machines as-is.
