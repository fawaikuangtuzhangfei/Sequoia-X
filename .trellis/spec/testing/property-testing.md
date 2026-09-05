# Property Testing

> How tests are written in this repo.

---

## Anatomy

```python
"""策略引擎属性测试。"""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from hypothesis import given, settings as h_settings
from hypothesis import strategies as st

from sequoia_x.core.config import Settings
from sequoia_x.data.engine import DataEngine
from sequoia_x.strategy.ma_volume import MaVolumeStrategy


# Feature: sequoia-x-v2, Property 9: 策略 run() 返回值类型正确
@given(
    symbols=st.lists(
        st.text(min_size=6, max_size=6, alphabet="0123456789"),
        min_size=0, max_size=3, unique=True,
    )
)
@h_settings(max_examples=30, deadline=None)
def test_strategy_run_returns_list_of_str(symbols: list[str]) -> None:
    """属性 9：run() 应返回 list[str]，每个元素为非空字符串。"""
    ...
    assert isinstance(result, list)
    assert all(isinstance(s, str) and len(s) > 0 for s in result)
```

Required elements:

- **Module docstring** in Chinese, matching production style.
- **`from hypothesis import settings as h_settings`** — always aliased. Plain
  `settings` collides with the project's `Settings` / `settings` variable and
  produces confusing shadowing. This alias is used in all six test files.
- **Header comment** `# Feature: sequoia-x-v2, Property N: <claim>` directly
  above the decorator. `N` is a stable, globally unique number across all test
  files (currently 1-13). New properties take the next free number.
- **Function docstring** restating the property in Chinese as `属性 N：...`.
- **Type-annotated signature**, `-> None`.

---

## `@h_settings` Tuning

| Setting | When |
|---------|------|
| `max_examples=100` | Pure, fast functions (`test_config.py`, `test_logger.py`) |
| `max_examples=50` | Cheap I/O — one SQLite write, one mocked POST |
| `max_examples=30` + `deadline=None` | Anything constructing a `DataEngine` or importing `main` |

`deadline=None` is required whenever a test creates a temp directory and a real
SQLite file — hypothesis's default per-example deadline is not met by the first
(cold) example and produces a flaky `DeadlineExceeded`.

`suppress_health_check=[HealthCheck.function_scoped_fixture]` is needed when
combining `@given` with a function-scoped pytest fixture such as `monkeypatch`
(`tests/test_config.py:12`).

---

## Generating Symbols

The canonical strategy for a stock code:

```python
st.text(min_size=6, max_size=6, alphabet="0123456789")
```

Used in `test_strategy.py`, `test_data_engine.py`, and `test_feishu.py`. Add
`unique=True` on `st.lists` when duplicates would make the assertion trivially
pass.

For webhook URLs, generate from a regex so the value is realistic:

```python
st.from_regex(r"https://open\.feishu\.cn/open-apis/bot/v2/hook/[a-z0-9\-]{8,36}",
              fullmatch=True)
```

---

## Real SQLite, Temp Directory

```python
def make_engine_in(tmp_dir: str) -> tuple[DataEngine, Settings]:
    settings = Settings(
        db_path=str(Path(tmp_dir) / "test.db"),
        start_date="2024-01-01",
        feishu_webhook_url="https://example.com/hook",
    )
    engine = DataEngine(settings)
    return engine, settings


with tempfile.TemporaryDirectory() as tmp_dir:
    engine, _ = make_engine_in(tmp_dir)
    ...
```

- `Settings(...)` is constructed **directly with all required fields**, not via
  `get_settings()`. This is the one sanctioned exception to the singleton rule
  in [core/config-guidelines](../core/config-guidelines.md#access-pattern);
  it isolates the test from any real `.env`.
- The `TemporaryDirectory` context must wrap everything that touches the DB.
  Hypothesis re-runs the body per example, so a new DB is created each time —
  which is the point: no cross-example state.

---

## Isolating the Network

**baostock / DataEngine methods** — patch on the instance:

```python
with patch.object(engine, "get_all_symbols", return_value=symbols):
    with patch.object(engine, "get_ohlcv", return_value=pd.DataFrame()):
        strategy = MaVolumeStrategy(engine=engine, settings=settings)
        result = strategy.run()
```

**HTTP** — patch `requests.post` and hand back a `MagicMock`:

```python
with patch("requests.post") as mock_post:
    mock_post.return_value = MagicMock(status_code=200)
    notifier.send(symbols=symbols, strategy_name="TestStrategy")
```

Note: `MagicMock(status_code=200)` makes `resp.json()` return another
`MagicMock`, so `resp_json.get("code") != 0` is truthy and the code takes the
**error** branch. The existing tests still pass because they assert on the
request, not the log — but if you write a test for the *success* path, set
`json=lambda: {"code": 0}` explicitly.

**Module-level dependencies** — patch the name in the importing module, not
where it is defined:

```python
with patch.object(main_module, "get_settings", side_effect=RuntimeError(error_msg)):
```

`main.py` does `from ... import get_settings`, so patching
`sequoia_x.core.config.get_settings` would have no effect.

---

## Resetting the Settings Singleton

`get_settings()` caches in a module global. Any test that changes config env
vars must clear it:

```python
import sequoia_x.core.config as cfg_module
monkeypatch.setenv("DB_PATH", db_path)
monkeypatch.setenv("FEISHU_WEBHOOK_URL", "https://example.com/hook")
monkeypatch.setattr(cfg_module, "_settings", None)
```

To test the required-field failure, bypass `.env` entirely and restore any real
env var in a `finally`:

```python
env_backup = os.environ.pop("FEISHU_WEBHOOK_URL", None)
try:
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
finally:
    if env_backup is not None:
        os.environ["FEISHU_WEBHOOK_URL"] = env_backup
```

`tests/test_config.py`. Without `_env_file=None`, a developer's local `.env`
would satisfy the field and the test would fail only on CI-like machines.

---

## Capturing Logs

Project loggers set `propagate = False`, so `caplog` does not work. Attach a
handler to the module's own logger and remove it in `finally` — full pattern in
[core/logging-guidelines](../core/logging-guidelines.md#capturing-logs-in-tests).

---

## What to Assert

Assert the **property**, not the implementation:

| Good | Bad |
|------|-----|
| `run()` returns a `list[str]` for any symbol set | `run()` returns exactly `["000001"]` |
| Inserting `(symbol, date)` twice leaves one row | `to_sql` was called twice |
| The POST body contains every selected symbol | The card JSON equals a fixed blob |
| Non-2xx response produces an ERROR record, no raise | The log string equals a literal |

A test that would still pass if you deleted the feature is a tautological test —
mentally delete the behaviour under test and check that the assertion fails.

---

## Adding a Test for a New Strategy

`tests/test_strategy.py` currently only covers `MaVolumeStrategy` (Property 9,
the return-type contract). When adding a strategy, at minimum extend that
property to cover it — the return-type contract in
[strategy-contract](../strategy/strategy-contract.md#run-contract) is what
`main.py` depends on and it is cheap to verify with an empty-DataFrame patch.
