"""Sequoia-X Web 服务入口。

    python serve.py                  # 仅本机可访问：127.0.0.1:8000
    python serve.py --host 0.0.0.0   # 内网可访问

与 main.py 是两个互不干扰的进程：main.py 由 cron 定时跑选股并写库，
本服务只读同一个 SQLite 文件。数据库已启用 WAL，读不会阻塞写。
"""

import argparse
import os

import uvicorn
from dotenv import load_dotenv

from sequoia_x.api.app import create_app

# 本服务不发任何通知，但 Settings 把 feishu_webhook_url 定为必填字段，
# 只想开网页的人会被卡在一个跟自己无关的配置上。
#
# 兜底放在这里而不是放宽 Settings：那个必填约束是 main.py 的安全网
# （漏配 webhook 就等于每天静默地不推送），tests/test_config.py 也在断言它。
# setdefault 保证真实配置仍然优先。
_PLACEHOLDER_WEBHOOK = "unused://serve-py-does-not-push"


def main() -> None:
    parser = argparse.ArgumentParser(description="Sequoia-X 选股结果查询服务")
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="监听地址，默认 127.0.0.1（仅本机）。内网访问用 0.0.0.0",
    )
    parser.add_argument("--port", type=int, default=8000, help="监听端口，默认 8000")
    args = parser.parse_args()

    # 必须在 create_app() 之前：Settings 在应用构造时才读环境变量。
    # 与 main.py 不同，这里无需放到 import 之前——没有任何模块在导入期读配置。
    load_dotenv()
    os.environ.setdefault("FEISHU_WEBHOOK_URL", _PLACEHOLDER_WEBHOOK)

    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
