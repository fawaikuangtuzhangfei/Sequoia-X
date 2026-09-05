# strategy — Screening Strategies

> `sequoia_x/strategy/`. One file per strategy, one class per file, all
> subclassing `BaseStrategy`.

---

## Docs

| Doc | Read when |
|-----|-----------|
| [Strategy Contract](./strategy-contract.md) | Adding or modifying any strategy — start here |
| [Pandas Guidelines](./pandas-guidelines.md) | Writing the actual screening computation |

Also required reading before adding a strategy:
[architecture § Adding a New Strategy](../project/architecture.md#adding-a-new-strategy--the-full-checklist)
— the 5-step checklist that includes registering it in `main.py`.

---

## Current Strategies

| Class | File | `webhook_key` | Shape |
|-------|------|---------------|-------|
| `MaVolumeStrategy` | `ma_volume.py` | `ma_volume` | per-symbol loop |
| `TurtleTradeStrategy` | `turtle_trade.py` | `turtle` | per-symbol loop + market-cap sort |
| `HighTightFlagStrategy` | `high_tight_flag.py` | `flag` | per-symbol loop |
| `LimitUpShakeoutStrategy` | `limit_up_shakeout.py` | `shakeout` | per-symbol loop |
| `UptrendLimitDownStrategy` | `uptrend_limit_down.py` | `limit_down` | per-symbol loop |
| `RpsBreakoutStrategy` | `rps_breakout.py` | `rps` | whole-market single query |
| `PrivatePlacementStrategy` | `private_placement.py` | `private_placement` | external API (akshare) |

Three shapes exist. Pick the one that matches your data access, and copy the
closest existing strategy as the starting point:

- **per-symbol loop** — the default. Copy `ma_volume.py` (simplest) or
  `uptrend_limit_down.py` (has NaN guards).
- **whole-market** — when the strategy needs cross-sectional ranking (RPS
  percentile). Copy `rps_breakout.py`'s *structure* but not its style; see the
  anti-pattern notes in [strategy-contract](./strategy-contract.md#rps_breakoutpy-is-not-the-style-reference).
- **external API** — when the data is not in `stock_daily` at all. Copy
  `private_placement.py`.

---

## Invariants

- `run()` returns `list[str]` of **bare 6-digit codes**, empty list for no hits.
- A strategy never pushes, never writes to the DB, and never imports `notify`.
- A single symbol's failure never aborts the run.
- All computation is vectorized pandas. `iterrows()` is banned.
