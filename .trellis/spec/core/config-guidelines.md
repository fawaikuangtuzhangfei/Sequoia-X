# Config Guidelines

> `sequoia_x/core/config.py`

---

## The Model

```python
class Settings(BaseSettings):
    db_path: str = "data/sequoia_v2.db"
    start_date: str = "2024-01-01"
    feishu_webhook_url: str            # required — missing => ValidationError
    strategy_webhooks: dict[str, str] = {}

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
```

Field name ↔ env var mapping is `case_sensitive=False` pydantic-settings
default: `db_path` ← `DB_PATH`.

---

## Access Pattern

**Production code always goes through the singleton:**

```python
from sequoia_x.core.config import get_settings

settings = get_settings()
```

`get_settings()` caches into the module-global `_settings`
(`sequoia_x/core/config.py:74-92`). In practice `main.py` calls it once and
passes the instance down to `DataEngine`, every strategy, and `FeishuNotifier`
via constructor injection.

**Never do this in library code:**

```python
settings = Settings()          # ✗ bypasses the singleton, re-reads .env
```

Direct construction is allowed **only in tests**, with all required fields
passed explicitly — see [testing/property-testing](../testing/property-testing.md).

Downstream classes read what they need in `__init__` and store it, rather than
holding the `Settings` object and reaching into it repeatedly:

```python
class DataEngine:
    def __init__(self, settings: Settings) -> None:
        self.db_path: str = settings.db_path
        self.start_date: str = settings.start_date
```

`BaseStrategy` is the exception — it keeps `self.settings` because subclasses
may need arbitrary fields.

---

## Adding a Setting — checklist

1. Add the field to `Settings` with a type annotation and a default (or no
   default if it must be required).
2. Add it to `.env.example` with a Chinese comment explaining the default.
3. Update `README.md` if it changes user-visible behaviour.
4. If a layer consumes it, copy it into that class's `__init__` following the
   pattern above.

`extra="ignore"` means a typo'd env var is **silently ignored**, not rejected.
There is no runtime safety net here — the `.env.example` ↔ `Settings` sync is
manual and must be done in the same commit.

---

## Webhook Routing

Two-tier lookup, defined in one place:

```python
def get_webhook_url(self, webhook_key: str) -> str:
    return self.strategy_webhooks.get(webhook_key.lower(), self.feishu_webhook_url)
```

- `strategy_webhooks` is populated from every `STRATEGY_WEBHOOK_<KEY>` env var,
  lowercased, in `model_post_init` (`sequoia_x/core/config.py:45-57`).
- Unknown key → falls back to `feishu_webhook_url`. **This never raises**, so a
  typo in `webhook_key` or in the env var name shows up as "all strategies post
  to the same bot", not as an error.
- `FeishuNotifier.send` is the only production caller
  (`sequoia_x/notify/feishu.py:117`). Do not re-implement the fallback anywhere
  else.

---

## Known Wart — do not extend it

`settings_customise_sources` (`sequoia_x/core/config.py:19-43`) scans the same
`STRATEGY_WEBHOOK_` prefix and stashes the result on
`cls._parsed_strategy_webhooks`, plus sets a `_STRATEGY_WEBHOOKS_PARSED`
env marker. **Nothing reads either of them.** The merge that actually takes
effect is the duplicate scan in `model_post_init`.

If you are already editing this area, delete the dead override rather than
adding a third scan. If you are not, leave it alone — but do not treat it as
the pattern to copy when adding a new prefix-scanned setting. Put new
prefix-scanning logic in `model_post_init` only.

`model_post_init` writes through `object.__setattr__` to bypass pydantic's
assignment guard. That is required here; do not "clean it up" into a plain
assignment.
