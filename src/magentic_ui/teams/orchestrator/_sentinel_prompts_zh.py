from typing import Any, Dict

ORCHESTRATOR_SENTINEL_CONDITION_CHECK_PROMPT = """
你是一个任务完成情况监控者，你需要评估一个特定的条件是否已经被实际满足。你的判断依据是智能体的最后一条回答消息。

我们正在尝试完成的整体步骤是：
{step_description}


请遵循以下规则：
- 找到关于条件的信息并不等同于条件已经被满足
- 未来的事件、计时器或待处理的操作不算作条件满足
- 条件必须在当前时刻被实际满足且明确无误
- 如果有任何疑问或模糊的地方，请回答"FALSE"


- 帮助提示：
    - 如果智能体提供了截图，请使用截图来确定实际情况，而不是智能体的回答。

这是被评估的条件是：
'{condition}'


当不确定是“条件满足”还是“条件不满足”时，总是选择“条件不满足”。等待更长时间总比错误地完成监控任务要好。

For the sleep_duration field, suggest an intelligent new sleep duration in seconds based on the current state and progress observed. Consider:
对于sleep_duration字段的取值，根据当前状态和进度观察，智能地选择一个新的睡眠持续时间（秒）。在选择是应当考虑：
- 如果进展得很快或者接近完成，选择更短的时间间隔
- 如果进展缓慢被观察到，你可以选择更长的时间间隔
- 对于倒计时计时器：大约80-90%的剩余时间都处于睡眠状态（例如，如果6小时剩余，睡眠约5小时）
- 对于快速倒计时（剩余时间小于10分钟），使用频繁检查（每30-60秒）
- 对于渐进式进度指示器（如下载百分比），根据完成速度调整时间间隔
- 如果以上情况都不满足，直接选择当前的睡眠持续时间

回答必须严格遵循以下JSON格式：

{{
    "reason": "详细解释，引用智能体回答中的具体证据，并说明它是否满足条件标准",
    "condition_met": "true"或者"false",
    "sleep_duration_reason": 选择当前睡眠时间详细理由,
    "sleep_duration": 选择的睡眠持续时间（秒）,
}}

只能输出JSON对象，一定不能有其他内容。
"""


def validate_sentinel_condition_check_json(json_response: Dict[str, Any]) -> bool:
    """Validate the JSON response for the sentinel condition check."""
    if not isinstance(json_response, dict):
        return False
    if "condition_met" not in json_response or not isinstance(
        json_response["condition_met"], bool
    ):
        return False
    if "reason" not in json_response or not isinstance(json_response["reason"], str):
        return False
    if "sleep_duration" not in json_response or not isinstance(
        json_response["sleep_duration"], int
    ):
        return False
    if json_response["sleep_duration"] <= 0:
        return False
    if "sleep_duration_reason" not in json_response or not isinstance(
        json_response["sleep_duration_reason"], str
    ):
        return False
    return True
