# Project Specs — Sequoia-X

> Cross-cutting rules that apply to every layer. Read this before touching any file.

---

## What This Project Is

A-share quantitative stock screener. Runs once per trading day after close:
pull incremental daily bars from baostock into local SQLite, run N screening
strategies over the local data, push each strategy's hits to its own Feishu bot.

Entry point: `main.py`. Two modes:

```bash
python main.py             # daily: incremental sync + strategies + push (2-3 min)
python main.py --backfill  # one-off: full-market history backfill (~12 min)
```

---

## Spec Index

| Doc | Read when |
|-----|-----------|
| [Architecture](./architecture.md) | Adding a module, unsure which layer owns something |
| [Coding Style](./coding-style.md) | Any code change (naming, docstrings, comments, commits) |
| [Tooling](./tooling.md) | Running lint/tests, adding a dependency, changing config |

Layer specs: [core](../core/index.md) · [data](../data/index.md) ·
[strategy](../strategy/index.md) · [notify](../notify/index.md) ·
[testing](../testing/index.md)

Thinking guides: [guides/](../guides/index.md)

---

## The Five Rules That Matter Most

1. **One bad symbol must never kill the run.** Every per-symbol loop wraps its
   body in `try/except`, logs a WARNING, and `continue`s. See
   `sequoia_x/strategy/ma_volume.py:58`.
2. **Vectorized pandas only.** No `iterrows()`, no per-row Python loops over a
   DataFrame. Every strategy docstring in this repo states this explicitly.
3. **Bare 6-digit codes are the canonical symbol format.** Prefixes (`sh.`,
   `SH`, `BJ`) exist only at the boundary that needs them. See
   [Architecture § Symbol format](./architecture.md#symbol-format-is-a-contract).
4. **Never build SQL with f-strings.** Always `?` placeholders + params tuple.
   See `sequoia_x/data/engine.py:75`.
5. **`main.py` is the only wiring point.** Layers never import sideways or
   upward — see the dependency rule in [Architecture](./architecture.md).

---

## Language Convention

- **Code identifiers**: English (`selected`, `webhook_key`, `_MIN_BARS`).
- **Docstrings, inline comments, log messages, commit subjects**: Chinese.
  This is consistent across the whole repo — match it, do not "translate to
  English" as a drive-by cleanup.
- **These spec docs**: English.
