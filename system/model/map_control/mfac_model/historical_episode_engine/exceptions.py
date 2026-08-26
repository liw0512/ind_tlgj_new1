class SlurryPolicyError(Exception):
    """模块基础异常。"""


class ConfigurationError(SlurryPolicyError):
    """配置错误。"""


class InputDataError(SlurryPolicyError):
    """输入数据错误。"""


class SnapshotError(SlurryPolicyError):
    """快照读写错误。"""
