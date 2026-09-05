"""API 分层：对外提供选股结果的只读 HTTP 查询接口。

依赖方向：api → data, core。本层**不得** import strategy 或 notify——
接口只读数据库，不触发选股、不发送通知。
"""
