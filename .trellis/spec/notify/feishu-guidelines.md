# Feishu Guidelines

> `sequoia_x/notify/feishu.py`

---

## Success Is Not HTTP 200

Feishu returns HTTP 200 with an error body. Both checks are required:

```python
resp = requests.post(url, data=json.dumps(payload),
                     headers={"Content-Type": "application/json"}, timeout=10)
resp_json = resp.json()

if resp.status_code != 200 or resp_json.get("code") != 0:
    logger.error(
        f"飞书推送失败 [{webhook_key}] "
        f"HTTP状态={resp.status_code} 飞书响应={resp.text}"
    )
else:
    logger.info(f"飞书推送成功 [{webhook_key}]，共 {len(symbols)} 只股票")
```

`resp_json.get("code") != 0` is the real signal — an invalid or revoked webhook
token returns 200 with a non-zero `code`. This was a real silent-failure bug;
do not simplify the condition back to a status-code check.

The error log includes `resp.text`, not the parsed body, so a non-JSON response
is still readable in the log.

---

## Request Rules

- `timeout=10` on every request. Never omit it — this runs unattended under
  cron and a hung POST blocks the remaining strategies.
- `data=json.dumps(payload)` with an explicit `Content-Type` header, not
  `json=payload`. The existing tests read `call_args.kwargs["data"]`, so
  switching to `json=` breaks `tests/test_feishu.py`.
- Only `requests.RequestException` is caught (`feishu.py:139`). A malformed
  response body would raise `json.JSONDecodeError` from `resp.json()` and escape
  `send()`, violating the never-raises contract. If you touch this block, widen
  the catch rather than narrowing it.

---

## Routing

```python
url = self.settings.get_webhook_url(webhook_key)
```

One line, delegating to `Settings`. Do not add fallback logic here — the
two-tier lookup and its default live in
[core/config-guidelines](../core/config-guidelines.md#webhook-routing).

`webhook_key` defaults to `"default"` in the signature, matching
`BaseStrategy.webhook_key`'s default.

---

## Card Structure

Feishu interactive card, built in `_build_card`:

```python
{
    "msg_type": "interactive",
    "card": {
        "header": {
            "title": {"tag": "plain_text", "content": f"📈 Sequoia-X 选股播报 | {strategy_name}"},
            "template": "blue",
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": "..."}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": "..."}},
        ],
    },
}
```

- Body text uses `"tag": "lark_md"` — Feishu's markdown dialect. `**bold**` and
  `[text](url)` work; standard-markdown constructs beyond that mostly do not.
- Symbols render as Xueqiu links: `[{name}](https://xueqiu.com/S/{xq_code})`,
  joined with a single space.
- The header carries `strategy_name`, which `main.py:80` computes as
  `type(strategy).__name__` — the class name, e.g. `TurtleTradeStrategy`. Keep
  class names presentable; they are user-facing.
- Symbol order in the card is the order `run()` returned. See
  [strategy-contract § ordering](../strategy/strategy-contract.md#run-contract).

If you add a card element, extend `elements`; do not build a second card
variant. One strategy's card must look like every other strategy's card.

---

## Stock Names

`_get_stock_names` (`feishu.py:41-53`) does one `bs.query_stock_basic` per
symbol inside a single login/logout pair, returning `{code: name}`. Falls back
to the Xueqiu code as the display label when a name is missing
(`feishu.py:62`) — the card must render even if baostock is unreachable.

This is deliberately N+1. It is acceptable **only** because it runs on the few
symbols a strategy selected, never on the full market. If a strategy ever
returns hundreds of symbols, batch this before shipping it.

Missing from this path: any `error_code` check on `bs.login()`, and any
`try/finally` around `logout()`. Every other baostock call site has both — see
[data/baostock-guidelines](../data/baostock-guidelines.md#session-lifecycle).
Add them if you touch this function.

---

## Code Conversion

```python
@staticmethod
def _to_xueqiu_code(code: str) -> str:
    if code.startswith("6"):
        return f"SH{code}"
    elif code.startswith(("4", "8")):
        return f"BJ{code}"
    return f"SZ{code}"
```

Xueqiu prefixes are uppercase with no dot, unlike baostock's `sh.600519`. Note
this mapping disagrees with the data layer's on `9`-prefixed codes — see
[architecture § symbol format](../project/architecture.md#symbol-format-is-a-contract)
before adding a third variant.
