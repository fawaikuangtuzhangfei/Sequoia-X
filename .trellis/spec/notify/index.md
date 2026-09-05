# notify — Feishu Push

> `sequoia_x/notify/feishu.py`. One class, `FeishuNotifier`. The last hop in the
> pipeline.

---

## Docs

| Doc | Read when |
|-----|-----------|
| [Feishu Guidelines](./feishu-guidelines.md) | Changing the card, the routing, or the error handling |

---

## Public Surface

```python
notifier = FeishuNotifier(settings)
notifier.send(symbols=selected, strategy_name="TurtleTradeStrategy", webhook_key="turtle")
```

`send()` is the only public method. `_to_xueqiu_code`, `_get_stock_names`, and
`_build_card` are private.

---

## Invariants

- **`send()` never raises.** Its docstring says so explicitly
  (`feishu.py:114-115`). A failed push logs ERROR and returns; `main.py`'s loop
  continues to the next strategy. Breaking this turns one bad webhook into a
  lost run.
- **`main.py` only calls `send()` when `selected` is non-empty**
  (`main.py:86`). `_build_card` has a `（无选股结果）` fallback, but that branch
  is unreachable in the current flow — do not rely on it.
- `notify` imports only from `core`. It calls baostock directly for stock names
  rather than importing `data`; see
  [architecture](../project/architecture.md#dependency-direction).
