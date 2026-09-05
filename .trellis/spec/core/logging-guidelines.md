# Logging Guidelines

> `sequoia_x/core/logger.py`

---

## Getting a Logger

```python
from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)     # module level, right after imports
```

Never use `logging.getLogger()` or `print()` directly. `get_logger` configures a
`RichHandler` with `rich_tracebacks=True`, `show_path=False`, timestamp format
`[%Y-%m-%d %H:%M:%S]`, and format `"%(name)s - %(message)s"`.

Two properties the factory guarantees, both covered by `tests/test_logger.py`:

- **Idempotent**: `if logger.handlers: return logger` — calling it twice never
  double-attaches a handler (which would double-print every line).
- **Non-propagating**: `logger.propagate = False`. Records do not reach the root
  logger. This matters for tests — see below.

Level is `DEBUG` for every logger. There is no per-module level config and no
env var to change it. If you need one, add it to `Settings` first.

---

## Level Semantics In This Project

| Level | Means | Example |
|-------|-------|---------|
| `INFO` | Normal progress and result counts | `logger.info(f"{name} 选出 {len(selected)} 只股票")` |
| `WARNING` | One item failed, the run continues | `logger.warning(f"[{symbol}] 策略计算失败：{exc}")` |
| `ERROR` | A whole operation failed, degraded result returned | `logger.error(f"飞书推送失败 [{webhook_key}] ...")` |
| `logger.exception` | Fatal, about to exit | `main.py:98` only |

The distinction that matters: **WARNING = skipped one symbol, ERROR = lost a
whole strategy or a whole push.** A per-symbol failure inside a loop is never
ERROR — otherwise a normal run produces hundreds of ERROR lines and real
failures get lost.

`logger.exception` appears exactly once, in the top-level handler in `main()`.
Do not use it inside layer code; log the exception with an f-string and continue.

---

## Message Conventions

- Chinese prose, f-string interpolation.
- Per-symbol messages lead with a bracketed symbol:
  `f"[{symbol}] TurtleTradeStrategy 计算失败：{exc}"`.
- Per-webhook messages lead with a bracketed key:
  `f"飞书推送成功 [{webhook_key}]，共 {len(symbols)} 只股票"`.
- **Every strategy's `run()` ends with a count line** before returning:
  `logger.info(f"{ClassName} 选出 {len(selected)} 只股票")`. `main.py:84` logs
  the same count again — that redundancy is intentional (per-strategy detail vs.
  orchestration view); keep both.
- Long-running loops log progress every 500 items
  (`sequoia_x/data/engine.py:288-292`) with success/skip/fail tallies. Match
  that shape for any new bulk loop.

---

## Capturing Logs In Tests

Because `propagate = False`, `caplog` and root-logger handlers do **not** see
these records. Attach a handler to the module's own logger by name:

```python
feishu_logger = logging.getLogger("sequoia_x.notify.feishu")   # == module.__name__
handler = _ListHandler(logging.ERROR)
feishu_logger.addHandler(handler)
try:
    ...
finally:
    feishu_logger.removeHandler(handler)
```

Full example: `tests/test_feishu.py` (Property 12). Always remove the handler in
a `finally` — loggers are process-global and a leaked handler pollutes every
later test.
