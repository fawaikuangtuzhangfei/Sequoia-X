# Architecture

> Layer boundaries, dependency direction, and where new code goes.

---

## Layout

```
Sequoia-X/
├── main.py                    # composition root: argparse, wiring, strategy loop
├── pyproject.toml             # deps + ruff + pytest config
├── .env.example               # every supported env var, documented
├── data/                      # SQLite db (runtime, gitignored)
├── sequoia_x/
│   ├── core/
│   │   ├── config.py          # Settings (pydantic-settings) + get_settings() singleton
│   │   └── logger.py          # get_logger() factory over rich.RichHandler
│   ├── data/
│   │   └── engine.py          # DataEngine: SQLite schema, baostock sync/backfill
│   ├── strategy/
│   │   ├── base.py            # BaseStrategy ABC
│   │   └── <name>.py          # one file per strategy, one class per file
│   └── notify/
│       └── feishu.py          # FeishuNotifier: card build + webhook POST
└── tests/                     # hypothesis property tests, one file per module
```

---

## Dependency Direction

```
main.py  ──wires──>  core · data · strategy · notify
                       ▲       ▲        │          │
                       │       └────────┘          │
                       └───────────────────────────┘
```

Allowed imports:

| Layer | May import |
|-------|-----------|
| `core` | nothing from `sequoia_x` (leaf layer) |
| `data` | `core` |
| `strategy` | `core`, `data` |
| `notify` | `core` |
| `main.py` | everything |

Hard rules:

- **`strategy` never imports `notify`.** A strategy returns `list[str]`;
  `main.py` decides whether and where to push it (`main.py:79-93`).
- **`notify` never imports `data`.** It needs stock names, so it calls baostock
  directly (`sequoia_x/notify/feishu.py:41`). That is intentional — do not
  "fix" it by injecting `DataEngine`, because names are not in `stock_daily`.
- **`core` imports nothing from the project.** If you find yourself wanting
  `core` to import `data`, you are putting the code in the wrong layer.

---

## Adding a New Strategy — the full checklist

This is the most common change in this repo. All five steps are required:

1. Create `sequoia_x/strategy/<snake_name>.py` with one class
   `<CamelName>Strategy(BaseStrategy)`.
2. Set a class-level `webhook_key: str = "<key>"`.
3. Implement `run() -> list[str]` per the
   [strategy contract](../strategy/strategy-contract.md).
4. Import it in `main.py` and append an instance to the `strategies` list
   (`main.py:66-74`). **Forgetting this means the strategy never runs and
   nothing fails loudly.**
5. Add `STRATEGY_WEBHOOK_<KEY>=...` to `.env.example` and update the strategy
   table in `README.md`.

`webhook_key` has no compile-time link to the env var. `<KEY>` in
`STRATEGY_WEBHOOK_<KEY>` is the uppercased `webhook_key`
(`sequoia_x/core/config.py:49-54`). A typo silently falls back to the default
bot instead of erroring — grep both files after editing either.

---

## Symbol Format Is a Contract

The canonical in-memory and in-database format is the **bare 6-digit code**
(`"600519"`, `"000001"`). Conversion happens only at the boundary that needs it:

| Converter | Where | Mapping |
|-----------|-------|---------|
| `DataEngine._to_baostock_code` | `sequoia_x/data/engine.py:90` | `6`/`9` → `sh.`, else `sz.` |
| `FeishuNotifier._to_xueqiu_code` | `sequoia_x/notify/feishu.py:32` | `6` → `SH`, `4`/`8` → `BJ`, else `SZ` |
| ad-hoc prefix in `_get_stock_names` | `sequoia_x/notify/feishu.py:47` | `6`/`9` → `sh`, else `sz` |

**Known divergence — do not copy blindly.** The three mappings disagree about
Beijing Exchange (`4`/`8`) and `9`-prefixed codes. `_to_baostock_code` maps
`4`/`8` codes to `sz.` even though baostock addresses Beijing listings as
`bj.<code>`; `_to_xueqiu_code` maps `9` codes to `SZ` while the data layer maps
them to `sh.`. `get_all_symbols()` strips the exchange prefix
(`sequoia_x/data/engine.py:319`), so the original exchange is lost and has to be
re-derived from the digits. If you touch symbol routing, unify these into one
helper rather than adding a fourth variant — see
[code-reuse-thinking-guide](../guides/code-reuse-thinking-guide.md).

---

## Data Flow (daily mode)

```
baostock ──8 procs──> DataEngine.sync_today_bulk() ──> SQLite stock_daily
                                                            │
                                        DataEngine.get_ohlcv(symbol) / read_sql
                                                            ▼
                                              Strategy.run() -> list[str]
                                                            │
                                          main.py: if selected: notifier.send(...)
                                                            ▼
                                     FeishuNotifier ──POST──> Feishu bot (per webhook_key)
```

Failure semantics at each hop:

| Hop | On failure |
|-----|-----------|
| baostock fetch (backfill) | retry 3× with 2/4/8s backoff, then skip the symbol |
| baostock fetch (bulk sync) | worker `continue`s past the bad symbol |
| strategy per-symbol compute | WARNING log + `continue` |
| strategy top-level (external API) | ERROR log + return `[]` |
| Feishu POST | ERROR log, **never raises** — the loop continues to the next strategy |
| anything else in `main()` | `logger.exception` + `sys.exit(1)` (`main.py:95-102`) |
