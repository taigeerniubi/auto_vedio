"""提供无需模型密钥的脚本会话，复现工具调用与异常反馈。"""
from copy import deepcopy
import json

from shiguang.agent.contracts import ToolCall


def tc(name, arguments=None, call_id=None):
    """构造一条模拟模型工具调用。

    Args:
        name: 工具名称。
        arguments: 参数对象或用于测试无效 JSON 的字符串。
        call_id: 可选的调用编号。

    Returns:
        可供执行器消费的模型工具调用。
    """
    return ToolCall(call_id or name, name, arguments if isinstance(arguments, str) else json.dumps(arguments or {}))


class ScriptSession:
    def __init__(self, steps):
        """保存预设步骤及测试观测结果。

        Args:
            steps: 每轮返回的工具调用或可调用步骤。
        """
        self.steps = iter(steps)
        self.received = []
        self.closed = False

    def step(self, results, timeout):
        """记录工具反馈并执行下一轮模拟行为。

        Args:
            results: 执行器反馈。
            timeout: 剩余请求时间。

        Returns:
            下一批模拟工具调用。
        """
        self.received.append(deepcopy(results))
        step = next(self.steps)
        return step(results) if callable(step) else step

    def close(self):
        """记录连接已经释放。"""
        self.closed = True
