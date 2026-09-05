# SQLite Guidelines

> `sequoia_x/data/engine.py`

---

## Connection Pattern

**One short-lived connection per operation, always via a `with` block:**

```python
with sqlite3.connect(self.db_path) as conn:
    row = conn.execute(
        "SELECT MAX(date) FROM stock_daily WHERE symbol = ?",
        (symbol,),
    ).fetchone()
```

`DataEngine` stores `self.db_path` (a string), **never a connection object**.
This is not incidental: `sync_today_bulk` forks 8 worker processes, and a shared
`sqlite3.Connection` cannot cross a process boundary. Do not add a cached
connection attribute.

**Caveat you must know:** `with sqlite3.connect(...)` commits (or rolls back) on
exit but does **not close** the connection. That is why write paths call
`conn.commit()` explicitly (`engine.py:69`, `engine.py:153`) even inside the
`with`. Follow that: if you write, commit explicitly.

---

## Queries

Always `?` placeholders with a params tuple. There is not a single f-string SQL
statement in this repo — keep it that way.

```python
# ✓
conn.execute("DELETE FROM stock_daily WHERE date = ?", (d,))
pd.read_sql("SELECT * FROM stock_daily WHERE symbol = ? ORDER BY date", conn, params=(symbol,))

# ✗ never
conn.execute(f"DELETE FROM stock_daily WHERE date = '{d}'")
```

Reads that produce DataFrames go through `pd.read_sql`; reads that produce
scalars or small lists use `conn.execute(...).fetchone()/.fetchall()`.

Schema DDL lives in module-level `_UPPER_SNAKE` constants
(`_CREATE_TABLE_SQL`, `_CREATE_INDEX_SQL`) and is applied idempotently with
`IF NOT EXISTS` in `_init_db`, which runs on every `DataEngine.__init__`. Adding
a column therefore needs an explicit migration path — `CREATE TABLE IF NOT
EXISTS` will not alter an existing table.

---

## Idempotent Writes

Two different strategies, both relying on `UNIQUE (symbol, date)`:

**1. Daily sync — delete-then-append per date** (`engine.py:149-153`):

```python
with sqlite3.connect(self.db_path) as conn:
    for d in df["date"].unique().tolist():
        conn.execute("DELETE FROM stock_daily WHERE date = ?", (d,))
    df.to_sql("stock_daily", conn, if_exists="append",
              index=False, method="multi", chunksize=500)
    conn.commit()
```

Re-running `main.py` on the same day is safe: the day's rows are wiped and
rewritten. **This deletes the date across all symbols**, so any DataFrame handed
to this path must contain every symbol that should have data for that date.

**2. Backfill — append and swallow the conflict** (`engine.py:277-284`):

```python
try:
    with sqlite3.connect(self.db_path) as conn:
        df.to_sql("stock_daily", conn, if_exists="append",
                  index=False, method="multi", chunksize=500)
except sqlite3.IntegrityError:
    pass
```

Backfill also skips symbols already current via `_get_last_date`, so an
interrupted backfill can simply be re-run and resumes where it stopped.

`to_sql` arguments are fixed across both paths — `if_exists="append"`,
`index=False`, `method="multi"`, `chunksize=500`. Do not use
`if_exists="replace"` anywhere; it drops and recreates the table, losing the
`UNIQUE` constraint and the index.

---

## Data Cleaning Before Write

Every write path runs the same three-step clean. Reproduce it exactly for any
new ingest:

```python
for col in ["open", "high", "low", "close", "volume", "turnover"]:
    df[col] = pd.to_numeric(df[col], errors="coerce")
df = df.dropna(subset=["close"])
df = df[df["volume"] > 0]
```

Rationale: baostock returns **strings**, including empty strings for suspended
sessions. `errors="coerce"` turns those into `NaN`; dropping on `close` removes
unusable bars; `volume > 0` removes suspended trading days that would otherwise
poison rolling averages in every strategy.

Column order is normalised before write
(`["symbol", "date", "open", "high", "low", "close", "volume", "turnover"]`)
because `to_sql` matches on column name — an extra column from baostock (e.g.
`amount` un-renamed) will raise rather than be ignored.

---

## Reading From Strategies

Prefer `engine.get_ohlcv(symbol)`. It is the documented read path and keeps the
SQL in one place.

`RpsBreakoutStrategy` opens its own connection against `self.engine.db_path` to
load the whole market in one query (`rps_breakout.py:18-19`). That is a
legitimate need — 5000 individual `get_ohlcv` calls would be far slower — but
the right fix is a `DataEngine` method for it, not a second raw-SQL site. If you
write another whole-market strategy, add the method to `DataEngine` instead of
copying the raw connection.
