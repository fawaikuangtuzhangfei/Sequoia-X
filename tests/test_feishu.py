"""飞书通知属性测试。"""

import json
import logging
from unittest.mock import MagicMock, patch

from hypothesis import given, settings as h_settings
from hypothesis import strategies as st

from sequoia_x.core.config import Settings
from sequoia_x.notify.feishu import FeishuNotifier


def make_settings(webhook_url: str = "https://example.com/default") -> Settings:
    return Settings(
        db_path="data/test.db",
        start_date="2024-01-01",
        feishu_webhook_url=webhook_url,
    )


def make_notifier(settings: Settings, names: dict[str, str] | None = None) -> FeishuNotifier:
    """构造带桩数据引擎的推送器。

    名称查询以前是每只股票一次 baostock 请求，测试必须逐个 patch 掉，
    否则又慢又依赖外网（曾导致 hypothesis DeadlineExceeded）。现在它只是
    DataEngine 的一次读库，给个桩引擎就够了，不再需要任何网络隔离。
    """
    engine = MagicMock()
    engine.get_stock_names.return_value = names or {}
    return FeishuNotifier(settings, engine)


# Feature: sequoia-x-v2, Property 10: 飞书通知包含所有选股结果
@given(
    symbols=st.lists(
        st.text(min_size=6, max_size=6, alphabet="0123456789"),
        min_size=1, max_size=10, unique=True,
    )
)
@h_settings(max_examples=50)
def test_notification_contains_all_symbols(symbols: list[str]) -> None:
    """属性 10：send() 发出的请求体应包含所有 symbol。"""
    notifier = make_notifier(make_settings())

    with patch("requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        notifier.send(symbols=symbols, strategy_name="TestStrategy")

    call_args = mock_post.call_args
    body = json.loads(call_args.kwargs.get("data") or call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs["data"])
    card_text = json.dumps(body)
    for symbol in symbols:
        assert symbol in card_text


# Feature: sequoia-x-v2, Property 11: 飞书通知使用 ConfigManager 中的 Webhook URL
@given(
    webhook_url=st.from_regex(r"https://open\.feishu\.cn/open-apis/bot/v2/hook/[a-z0-9\-]{8,36}", fullmatch=True)
)
@h_settings(max_examples=50)
def test_notification_uses_config_url(webhook_url: str) -> None:
    """属性 11：send() 发出的 HTTP 请求目标 URL 应等于 settings.feishu_webhook_url。"""
    notifier = make_notifier(make_settings(webhook_url=webhook_url))

    with patch("requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        notifier.send(symbols=["000001"], strategy_name="Test", webhook_key="default")

    called_url = mock_post.call_args.args[0] if mock_post.call_args.args else mock_post.call_args.kwargs.get("url")
    assert called_url == webhook_url


# Feature: sequoia-x-v2, Property 12: HTTP 失败时记录 ERROR 日志
@given(status_code=st.integers(min_value=400, max_value=599))
@h_settings(max_examples=50)
def test_http_failure_logs_error(status_code: int) -> None:
    """属性 12：非 200 响应时，send() 应记录 ERROR 级别日志，不抛出异常。"""
    import sequoia_x.notify.feishu as feishu_module

    notifier = make_notifier(make_settings())

    # feishu logger 设置了 propagate=False，需直接在其上挂 handler
    feishu_logger = logging.getLogger(feishu_module.__name__)
    log_records: list[logging.LogRecord] = []

    class _ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            log_records.append(record)

    handler = _ListHandler(logging.ERROR)
    feishu_logger.addHandler(handler)
    try:
        with patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=status_code, text="error")
            notifier.send(symbols=["000001"], strategy_name="Test")
    finally:
        feishu_logger.removeHandler(handler)

    assert any(r.levelno == logging.ERROR for r in log_records)


# Feature: sequoia-x-v2, Property 32: 名称查询失败不阻断推送
def test_name_lookup_failure_still_sends() -> None:
    """属性 32：名称查询抛异常时，推送照发，卡片降级展示雪球代码。

    名称只是展示信息。让它挡住推送，等于用一个次要功能换掉这个项目的核心价值。
    """
    engine = MagicMock()
    engine.get_stock_names.side_effect = RuntimeError("数据库炸了")
    notifier = FeishuNotifier(make_settings(), engine)

    with patch("requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        notifier.send(symbols=["600519"], strategy_name="Test")

    assert mock_post.call_count == 1
    body = json.dumps(json.loads(mock_post.call_args.kwargs["data"]), ensure_ascii=False)
    assert "SH600519" in body  # 名称缺失时退回展示雪球代码


def test_names_are_fetched_in_one_call() -> None:
    """名称查询必须是一次批量调用，不能退回成每只股票一次。

    这正是这次改动要消灭的 N+1：13 只股票曾经意味着 13 次网络往返。
    """
    engine = MagicMock()
    engine.get_stock_names.return_value = {"600519": "贵州茅台"}
    notifier = FeishuNotifier(make_settings(), engine)

    symbols = [f"6005{i:02d}" for i in range(13)]
    with patch("requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        notifier.send(symbols=symbols, strategy_name="Test")

    assert engine.get_stock_names.call_count == 1
    assert engine.get_stock_names.call_args.args[0] == symbols
