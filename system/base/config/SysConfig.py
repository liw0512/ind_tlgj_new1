"""
配置说明（自动补全）
====================
本文件只选择当前启用的系统环境配置。
开发环境使用 SysConfigDev4Linux，测试/生产环境切换时只保留一个活动 import，
避免同时启用多套配置。
"""

# from system.base.config.SysConfigProd import config as prod
# from system.base.config.SysConfigTest import config as test
from system.base.config.SysConfigDev4Linux import config as dev_linux

config = dev_linux  # 当前生效环境：Linux 开发配置；切换环境时修改此处
