# Code Reuse Thinking Guide

> **Purpose**: Stop before writing something this repo already has.

---

## Why This Matters Here

This is a ~1400-line codebase where the same operations recur in every layer:
convert a stock code, open a baostock session, clean a DataFrame, guard a
rolling window. Each duplication that exists today already caused a divergence
(the three symbol converters disagree on Beijing Exchange codes). Every new one
will too.

---

## Step 1: Grep First

```bash
rg "_to_baostock_code|_to_xueqiu_code"   # symbol conversion
rg "bs.login"                            # baostock session
rg "pd.to_numeric"                       # DataFrame cleaning
rg "sqlite3.connect"                     # DB access
rg "max_retries|time.sleep"              # retry logic
```

---

## Step 2: The Existing Inventory

Before writing any of these, check what is already there:

| You need | It already exists at |
|----------|---------------------|
| Symbol → baostock code | `DataEngine._to_baostock_code` (`data/engine.py:90`) |
| Symbol → Xueqiu code | `FeishuNotifier._to_xueqiu_code` (`notify/feishu.py:32`) |
| Symbol → stock name | `FeishuNotifier._get_stock_names` (`notify/feishu.py:41`) |
| Read one symbol's bars | `DataEngine.get_ohlcv` |
| List symbols in the DB | `DataEngine.get_local_symbols` |
| List all A-shares | `DataEngine.get_all_symbols` |
| baostock session + retry + reconnect | `DataEngine.backfill` (`data/engine.py:158`) |
| Multiprocess baostock fetch | `_bs_fetch_batch` (`data/engine.py:34`) |
| Numeric coercion + bad-bar filter | `data/engine.py:143-146` and `:264-267` |
| Webhook URL with fallback | `Settings.get_webhook_url` (`core/config.py:59`) |
| A configured logger | `get_logger` (`core/logger.py:9`) |
| Test DB + Settings | `make_engine_in` (`tests/test_data_engine.py:16`) |

---

## Step 3: The Questions

| Question | If yes |
|----------|--------|
| Does a `DataEngine` method already return this data? | Call it. Do not open your own connection |
| Am I writing my second `bs.login()` in this file? | Extract a helper first |
| Am I copying a strategy file wholesale? | Fine — but delete every condition you are not using, and update the docstring's numbered list |
| Am I re-deriving an exchange prefix from digits? | Use the existing converter, or unify the three that exist |
| Am I adding a third place that knows this constant? | Make it a class constant or a `Settings` field |

---

## Known Duplication in This Repo

These are already duplicated. Fixing one is a welcome change; **adding a fourth
instance is not**.

**1. Symbol prefix derivation — three variants**

```python
"sh" if symbol.startswith(("6", "9")) else "sz"    # data/engine.py:92
"sh" if code.startswith(("6", "9")) else "sz"      # notify/feishu.py:47  (copy)
"SH" / "BJ" / "SZ" by 6 / 4,8 / else               # notify/feishu.py:33  (different rules)
```

The right fix: one public converter, probably on `DataEngine`, parameterised by
target format. See
[architecture § symbol format](../project/architecture.md#symbol-format-is-a-contract).

**2. DataFrame cleaning — two near-identical blocks**

`data/engine.py:143-146` and `data/engine.py:264-267` differ only in whether the
amount column is named `turnover` or `amount`. Extract to a module-level
`_clean_ohlcv(df, amount_col)` when you next touch either.

**3. STRATEGY_WEBHOOK_ scanning — two scans**

`settings_customise_sources` and `model_post_init` both scan the same prefix.
Only the second one has any effect. See
[config-guidelines](../core/config-guidelines.md#known-wart--do-not-extend-it).

**4. baostock session boilerplate — four sites**

`engine.backfill`, `engine.get_all_symbols`, `_bs_fetch_batch`,
`feishu._get_stock_names`, plus `turtle_trade._get_market_caps`. They disagree
on whether to check `error_code` and whether to use `try/finally`. A context
manager (`with baostock_session():`) would collapse all five.

---

## When Duplication Is Correct

Not everything should be shared:

- **Strategy screening logic.** Two strategies computing a 20-day MA is not
  duplication worth extracting — the conditions are the strategy's identity, and
  a shared `_compute_indicators` helper would couple unrelated strategies.
- **The per-symbol loop skeleton.** It is repeated five times deliberately; a
  template-method base class would make each strategy harder to read and would
  not accommodate the whole-market and external-API shapes.
- **Test helpers.** `make_engine_in` and `make_settings` are per-file by design.
  Two call sites do not justify a `conftest.py`.

The line: **share mechanism, not policy.** Session handling, conversion,
cleaning, retry → share. What counts as a buy signal → don't.

---

## Before You Change a Constant

Every one of these is coupled across files by string only:

| Change | Also update |
|--------|-------------|
| `webhook_key` value | `.env.example`, deployed `.env`, `README.md` |
| Strategy class name | `main.py` import + list, Feishu card title (auto), `README.md` table |
| A `Settings` field name | `.env.example`, deployed `.env`, every consumer's `__init__` |
| A `stock_daily` column | `_CREATE_TABLE_SQL`, both write paths' column lists, every strategy that reads it |
| A screening threshold | The class docstring's numbered condition list |

Grep before, grep after.
