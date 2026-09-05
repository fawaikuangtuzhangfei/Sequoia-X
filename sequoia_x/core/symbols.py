"""股票代码格式转换模块：项目内规范格式为纯 6 位数字代码。

本模块是**新代码唯一的代码转换入口**。

历史遗留：项目中已存在三处各自实现的前缀映射，且规则互不一致——
  - DataEngine._to_baostock_code   6/9 -> sh.，其余 -> sz.
  - FeishuNotifier._to_xueqiu_code 6 -> SH，4/8 -> BJ，其余 -> SZ
  - FeishuNotifier._get_stock_names 内联的 6/9 -> sh，其余 -> sz

它们在北交所（4/8 开头）和 9 开头代码上的处理彼此冲突。统一这三处属于独立
重构，不在当前改动范围内，因此本模块只服务于新增代码，旧代码保持原样。
"""


def to_xueqiu_code(symbol: str) -> str:
    """
    将纯数字代码转为雪球格式。

    映射规则与 FeishuNotifier._to_xueqiu_code 保持一致，
    确保网页链接与飞书推送里的链接指向同一个页面。

    Args:
        symbol: 纯 6 位数字代码，如 '600519'。

    Returns:
        雪球格式代码，如 'SH600519'。6 开头为 SH，
        4/8 开头（北交所）为 BJ，其余为 SZ。
    """
    if symbol.startswith("6"):
        return f"SH{symbol}"
    if symbol.startswith(("4", "8")):
        return f"BJ{symbol}"
    return f"SZ{symbol}"
