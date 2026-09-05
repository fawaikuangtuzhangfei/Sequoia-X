# Tooling

> Commands, config, and dependency rules.

---

## Commands

```bash
uv sync                     # install (preferred; pip install '.[dev]' also works)

ruff check .                # lint — must be clean before commit
ruff check . --fix          # auto-fix import order (I) and pyupgrade (UP)

pytest                      # full suite; testpaths = ["tests"] so no path arg needed
pytest tests/test_config.py # single file

python main.py              # daily run
python main.py --backfill   # history backfill
```

There is no CI workflow and no pre-commit hook in this repo. `ruff check .` and
`pytest` are manual gates — run both before every commit.

---

## Config Lives in `pyproject.toml`

```toml
[tool.ruff]
line-length = 100
target-version = "py311"
select = ["E", "F", "I", "UP"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- **Line length is 100**, not 88. Several existing lines sit in the 90-100 range
  (e.g. `sequoia_x/data/engine.py:142`) — that is fine, do not rewrap them
  narrower "for consistency".
- `target-version = "py311"` while `requires-python = ">=3.10"`. Ruff will
  suggest 3.11+ idioms; the runtime floor is 3.10. If you use a 3.11-only
  feature, bump `requires-python` in the same commit.
- **`select` is at `[tool.ruff]`, which newer ruff versions deprecate in favour
  of `[tool.ruff.lint]`.** If `ruff check .` starts warning about this, move the
  key to `[tool.ruff.lint]` rather than deleting it.

---

## Dependencies

Runtime deps are declared in `[project.dependencies]`, dev-only in
`[project.optional-dependencies].dev`. `uv.lock` is committed — regenerate it
with `uv sync` in the same commit as any `pyproject.toml` dependency change.

Current runtime set and what each is for:

| Package | Used by |
|---------|---------|
| `baostock` | `data/engine.py`, `notify/feishu.py`, `strategy/turtle_trade.py` — the primary data source |
| `akshare` | `strategy/private_placement.py` only |
| `pandas` | every strategy, `data/engine.py` |
| `pydantic-settings` | `core/config.py` |
| `python-dotenv` | `main.py:10-11` (`load_dotenv()` before any project import) |
| `rich` | `core/logger.py` |
| `requests` | `notify/feishu.py` |

**Note on akshare:** commit `cca53c1` removed akshare in favour of
baostock-everywhere, and `cd81243` reintroduced it for the private-placement
announcement feed (baostock has no equivalent dataset). Before adding another
akshare-backed strategy, check whether baostock covers the data — the project's
stated direction is baostock-first because of Eastmoney anti-scraping.

Adding a dependency requires: `pyproject.toml` entry + `uv sync` + a line in
this table.

---

## Environment Variables

`.env.example` is the source of truth for what is configurable. Every field on
`Settings` must appear there with a comment. `main.py` calls `load_dotenv()` at
line 11, **before** importing anything from `sequoia_x`, so module-level code
can rely on env vars being present.

`Settings` uses `extra="ignore"`, so an unknown or misspelled env var is
silently dropped rather than raising. This is why the `.env.example` ↔
`Settings` ↔ `README` triple has to be kept in sync by hand.

---

## Runtime Notes

- `main.py:15-16` sets a global 10s socket timeout
  (`socket.setdefaulttimeout(10.0)`) before any network import. Do not remove
  it — it is the backstop against baostock hanging forever.
- `sync_today_bulk` spawns 8 processes (`multiprocessing.Pool`). On Windows this
  re-imports the module in each child, which is why `_bs_fetch_batch` is a
  module-level function and not a closure or method — keep it that way.
- Scheduled via crontab on the deploy host (see `README.md`), not by any
  in-process scheduler.
