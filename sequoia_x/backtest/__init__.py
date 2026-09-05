"""回测分层：把选股结果换算成收益，并支持按历史日期回放策略。

本层可以 import core / data / strategy，**绝不 import notify 或 api**——
回测是离线分析，不该有能力往飞书群里灌东西。
"""
