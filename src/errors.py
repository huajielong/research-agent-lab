"""Public error messages must never include provider exception text."""

WORKFLOW_ERROR = '研究服务暂时不可用，请稍后重试。'
MODEL_ERROR = '[模型错误：内部服务暂时不可用，请稍后重试]'
RESTART_ERROR = '服务已重启，此次研究已中断。可以根据原问题重新发起研究。'


def public_error(value):
    return value if value in (WORKFLOW_ERROR, RESTART_ERROR) else WORKFLOW_ERROR
