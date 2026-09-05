# Thinking Guides

> **Purpose**: Catch the "didn't think of that" bugs before they ship.

Layer specs tell you *how* to write code here. These guides tell you *what to
check* before you start.

---

## Available Guides

| Guide | Use when |
|-------|----------|
| [Code Reuse Thinking Guide](./code-reuse-thinking-guide.md) | You are about to write something that might already exist |
| [Cross-Layer Thinking Guide](./cross-layer-thinking-guide.md) | Your change touches more than one of core / data / strategy / notify |

---

## Triggers — Code Reuse

- [ ] You are adding a symbol-format converter (there are already three)
- [ ] You are writing a baostock `login` / query / `logout` block
- [ ] You are adding a retry loop
- [ ] You are copying a strategy file as a starting point
- [ ] You are about to change a threshold, a webhook key, or an env var name
- [ ] You are opening a raw `sqlite3.connect` outside `sequoia_x/data/`

→ [Code Reuse Thinking Guide](./code-reuse-thinking-guide.md)

## Triggers — Cross-Layer

- [ ] Adding a strategy (touches `strategy/`, `main.py`, `.env.example`, `README.md`)
- [ ] Adding a `Settings` field (touches `core/config.py`, `.env.example`, a consumer)
- [ ] Changing the `stock_daily` schema or a column's meaning
- [ ] Changing what `run()` returns, or its ordering
- [ ] Changing the Feishu card shape

→ [Cross-Layer Thinking Guide](./cross-layer-thinking-guide.md)

---

## The Pre-Modification Rule

> **Before changing any value, grep for it first.**

```bash
rg "turtle"           # webhook key? env var? class name? README row?
rg "STRATEGY_WEBHOOK"
rg "adjustflag"
```

Most of this project's cross-file couplings are **by string, not by symbol** —
`webhook_key` ↔ `STRATEGY_WEBHOOK_*`, strategy class name ↔ card title ↔ README
table. No type checker or test will catch a mismatch. Grep is the safety net.

---

## Verifying AI Review Findings

When an AI reviewer flags something in this codebase, check these first — they
are the recurring false positives here:

| Finding | Check before acting |
|---------|--------------------|
| "Bare `except Exception` is too broad" | Is it a per-symbol loop body? That is the mandated pattern — see [strategy-contract](../strategy/strategy-contract.md) |
| "Missing input validation" | Data is from baostock/akshare and already coerced on write. It is not user input |
| "This function swallows errors" | `FeishuNotifier.send` never raising is a documented contract, not a bug |
| "Unused import / dead code" | `_bs_fetch_batch` is used via `Pool.map`; `main.py`'s `load_dotenv()` must precede imports |
| "Function-local import should move to module top" | Deferred imports for baostock/akshare are deliberate |

Real issues the codebase does have, already catalogued — do not re-report them
as new findings, but do fix them if you are in the neighbourhood:

- Three disagreeing symbol-prefix mappings
  ([architecture](../project/architecture.md#symbol-format-is-a-contract))
- Dead `settings_customise_sources` override
  ([config-guidelines](../core/config-guidelines.md#known-wart--do-not-extend-it))
- `rps_breakout.py` style drift
  ([strategy-contract](../strategy/strategy-contract.md#rps_breakoutpy-is-not-the-style-reference))
- `_get_stock_names` missing login check and `try/finally`
  ([feishu-guidelines](../notify/feishu-guidelines.md#stock-names))
- `turtle_trade.py` calling `engine._to_baostock_code` across the layer boundary

---

## Contributing

Found a new "didn't think of that" moment? Add it to the relevant guide, or —
if it is a rule rather than a question — to the owning layer spec.
