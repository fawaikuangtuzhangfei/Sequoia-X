# Pandas Guidelines

> How screening computations are written. `iterrows()` is banned — every
> strategy docstring in this repo says so explicitly (「严禁 iterrows」).

---

## Why Vectorization Is Mandatory

A daily run evaluates ~5000 symbols. Per-symbol strategies already pay one
SQLite round-trip each; adding a Python-level row loop on top turns a 2-3 minute
run into an hour. Vectorized pandas ops push the loop into C.

Banned: `df.iterrows()`, `df.itertuples()` for computation, `df.apply(axis=1)`,
and manual `for i in range(len(df))` indexing.

Allowed: the outer `for symbol in symbols` loop — that is iteration over
independent datasets, not over rows.

---

## The Vocabulary In Use

Learn these five; they cover every current strategy.

**Rolling windows** — moving averages, N-day extremes:

```python
df["ma5"] = df["close"].rolling(5).mean()
df["vol_ma20"] = df["volume"].rolling(20).mean()
```

**`shift` before `rolling`** — to exclude today from a lookback window. This is
the subtle one:

```python
# 前20个交易日 high 的最大值，不含今日
df["high_20"] = df["high"].shift(1).rolling(20).max()
```

Without `.shift(1)`, today's own high is inside the window and
`close > high_20` can never be a genuine breakout. `turtle_trade.py:81`.

**Positional row access for the tail** — the standard way to compare
today/yesterday:

```python
today = df.iloc[-1]
prev = df.iloc[-2]
prev2 = df.iloc[-3]
```

`get_ohlcv` returns rows `ORDER BY date`, so `-1` is the latest bar. Guard with
`_MIN_BARS` first, or `iloc[-3]` raises `IndexError`.

**`tail(n)` for window aggregates** without materialising a rolling column:

```python
tail40 = df.tail(40)
high40 = tail40["high"].max()
low40 = tail40["low"].min()
```

`high_tight_flag.py:43-49`. Use this when you need one scalar per window rather
than a full series.

**`groupby` for whole-market operations** — cross-sectional work only:

```python
df["close_shift"] = df.groupby("symbol")["close"].shift(self.rps_period)
roll_high = df.groupby("symbol")["high"].rolling(
    window=self.rps_period, min_periods=self.rps_period // 2
).max().reset_index(level=0, drop=True)
```

Note the `.reset_index(level=0, drop=True)` — `groupby().rolling()` returns a
MultiIndex `(symbol, original_index)`, and it must be dropped before assigning
back to `df`. Forgetting this misaligns every row. `rps_breakout.py:43-45`.

---

## NaN Is the Default Failure Mode

`rolling(n)` produces `NaN` for the first `n-1` rows. A length check alone is
not sufficient when data has gaps.

```python
if pd.isna(prev["ma20"]) or pd.isna(prev["ma60"]) or pd.isna(today["vol_ma20"]):
    continue
```

`uptrend_limit_down.py:50`. Guard **every** rolling-derived cell you are about
to compare, not just the longest window.

For whole-market strategies use `dropna(subset=[...])` instead
(`rps_breakout.py:36`).

Comparisons against `NaN` return `False` silently — a missing guard does not
crash, it just makes the strategy quietly select nothing. That failure is
invisible in the logs (`选出 0 只股票` looks like a normal quiet day), which is
why the guard is mandatory rather than optional.

---

## Division Guards

Ratio conditions must guard the denominator:

```python
if low40 == 0 or low10 == 0:
    continue
momentum = high40 / low40 > 1.6
```

`high_tight_flag.py:51`. Also seen in `turtle_trade.py:57` (`if turn > 0`) with
`ZeroDivisionError` in the caught exception tuple as a second line of defence.

---

## Writing Derived Columns

Assigning to `df` inside the loop is fine and is the established pattern —
`get_ohlcv` returns a fresh DataFrame per symbol, so there is no shared-state or
`SettingWithCopyWarning` risk:

```python
df["ma5"] = df["close"].rolling(5).mean()
```

When you *do* slice before assigning, `.copy()` first — `rps_breakout.py:35`
and `:40` do this correctly:

```python
latest_df = df[df["date"] == latest_date].copy()
latest_df["rps"] = latest_df["pct_change"].rank(pct=True) * 100
```

---

## Types on Read

`stock_daily.date` is TEXT. String comparison works chronologically for
`'YYYY-MM-DD'`, so most strategies never convert. Convert only when you need
date arithmetic or `.dt` accessors:

```python
df["date"] = pd.to_datetime(df["date"])
df = df.sort_values(["symbol", "date"])
```

Price/volume columns are already REAL in SQLite, so no coercion is needed on the
read path. Coercion (`pd.to_numeric(errors="coerce")`) belongs in the **write**
path — see [data/sqlite-guidelines](../data/sqlite-guidelines.md#data-cleaning-before-write).

Returning symbols from a DataFrame: `.tolist()`, not `list(...)` — it converts
numpy scalars to Python types. `selected["symbol"].tolist()`.
