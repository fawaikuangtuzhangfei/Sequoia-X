# core — Configuration & Logging

> `sequoia_x/core/` is the leaf layer. It imports nothing from the project and
> everything else imports it.

---

## Files

| File | Owns |
|------|------|
| `sequoia_x/core/config.py` | `Settings` model, `get_settings()` singleton, webhook routing |
| `sequoia_x/core/logger.py` | `get_logger(name)` factory over `rich.logging.RichHandler` |

## Docs

| Doc | Read when |
|-----|-----------|
| [Config Guidelines](./config-guidelines.md) | Adding/changing a setting, touching webhook routing |
| [Logging Guidelines](./logging-guidelines.md) | Deciding a log level, adding a log line, capturing logs in a test |

---

## Invariants

- `core` must never import from `sequoia_x.data`, `sequoia_x.strategy`, or
  `sequoia_x.notify`. If a change here needs one of those, the code belongs in
  the other layer.
- `Settings` is instantiated exactly once in production, via `get_settings()`.
- `get_logger(name)` is idempotent: same name → same `Logger`, one handler.
  `tests/test_logger.py` asserts both properties.
