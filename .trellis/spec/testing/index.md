# testing — Property-Based Tests

> `tests/`. pytest + hypothesis. Property tests, not example tests.

---

## Docs

| Doc | Read when |
|-----|-----------|
| [Property Testing](./property-testing.md) | Writing or modifying any test |

---

## Layout

One test file per production module, named `test_<module>.py`:

| Test file | Covers |
|-----------|--------|
| `tests/test_config.py` | `sequoia_x/core/config.py` |
| `tests/test_logger.py` | `sequoia_x/core/logger.py` |
| `tests/test_data_engine.py` | `sequoia_x/data/engine.py` |
| `tests/test_strategy.py` | `sequoia_x/strategy/` |
| `tests/test_feishu.py` | `sequoia_x/notify/feishu.py` |
| `tests/test_main.py` | `main.py` |

`pytest` with no arguments runs everything (`testpaths = ["tests"]`).

---

## Invariants

- **No test touches the network.** baostock and `requests.post` are always
  patched. A test that hits the real API is a bug, not slow coverage.
- **SQLite is real, not mocked.** Tests create an actual DB in a
  `tempfile.TemporaryDirectory()`.
- Every test is a hypothesis property with an explicit
  `# Feature: sequoia-x-v2, Property N: <claim>` header comment.
- No `conftest.py` and no shared fixtures — each file defines its own local
  helper (`make_engine_in`, `make_settings`). Follow that; do not introduce a
  fixture layer for two call sites.
