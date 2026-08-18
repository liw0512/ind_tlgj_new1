import os
import logging
from logging.handlers import TimedRotatingFileHandler
from system.base.config.SysConfig import config
import sys


def _make_console_encoding_safe(stream):
    """保留当前控制台编码，但让不可编码字符安全转义而不是触发日志异常。"""
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(errors="backslashreplace")
        except (OSError, ValueError):
            pass


_make_console_encoding_safe(sys.stdout)
_make_console_encoding_safe(sys.stderr)

# 在文件开头添加
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    encoding='utf-8'  # 指定编码为utf-8
)
def setup_log(log_name):
    # 创建logger对象。传入logger名字
    logger = logging.getLogger(log_name)
    log_path = os.path.join(config["filename"], log_name + ".log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    if logger.handlers:
        return logger
    # 创建处理器：sh为控制台处理器
    sh = logging.StreamHandler()
    # 设置日志记录等级
    logger.setLevel(logging.INFO)
    # interval 滚动周期，
    # when="MIDNIGHT", interval=1 表示每天0点为更新点，每天生成一个文件
    # backupCount  表示日志保存个数
    file_handler = TimedRotatingFileHandler(
        filename=log_path, when="D", backupCount=30, encoding="utf-8"
    )
    # 定义日志输出格式
    file_handler.setFormatter(
        logging.Formatter(
            "[%(asctime)s] [%(process)d] [%(levelname)s] - %(module)s.%(funcName)s (%(filename)s:%(lineno)d) - %(message)s"
        )
    )
    sh.setFormatter(
        logging.Formatter(
            "[%(asctime)s] [%(process)d] [%(levelname)s] - %(module)s.%(funcName)s (%(filename)s:%(lineno)d) - %(message)s"
        ))
    logger.addHandler(file_handler)
    logger.addHandler(sh)
    return logger
