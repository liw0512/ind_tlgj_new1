"""
配置说明（自动补全）
====================
SysConfigDev4Linux：系统部署、数据库、实时周期、训练解释器和界面配置。
路径和数据库连接必须按部署机器修改；算法参数不应放在此文件。
修改后需要重启服务。数据库密码建议改为环境变量注入。
"""

from pathlib import Path


BASE_PATH = Path(__file__).resolve().parents[3]


config = {
    "base_path": str(BASE_PATH),  # 程序部署根目录；不同环境必须改为实际路径
    "filename": str(BASE_PATH / "logs"),  # 日志输出目录
    # "dbconnetion": "postgresql+psycopg2://root:tswcbyy5413LX@127.0.0.1:5432/ind_optim_sys",
    "dbconnetion": "postgresql+psycopg2://postgres:lw123@127.0.0.1:5432/xrdc",  # 数据库连接串；建议通过环境变量管理，不要提交真实生产密码
    # "dbconnetion": "postgresql+psycopg2://root:tswcbyy5413LX@v1.pgrm.top:9326/ind_optim_sys_2",
    "mod_args_path": str(BASE_PATH / "files" / "model"),  # 模型参数文件目录
    "download_path": str(BASE_PATH / "files"),  # 下载、历史数据和模型文件的公共目录
    "model_id": "29c15f1d-20af-4a32-806f-d8fe78f110f3",  # 模型服务或数据库中的模型唯一标识
    "history_id": "29c15f1d-20af-4a32-806f-d8fe78f11023",  # 历史数据源或任务唯一标识
    "critica_value": 30,  # 循环泵的判断
    "resample": "30S",  # 降采样
    "phji": 1,  # 选择那个ph计
    "send_master_data_sum": 16,  # 经过模型向dcs系统发送数据的总个数
    "send_master_redirect_data_sum": 4,  # 直接向向dcs系统发送数据的总个数
    "rtstep": 1,  # 运行时数据的频率基准值
    "rdstep": 2,  # 实时数据间隔时间
    "ptstep": 30,  # 多长时间给模型一次数据
    "mod_pre_step": 0,  # 模型预测的步长
    "search_time": 1,  # ws初始化时查询数据库前两个小时的数据
    "save_time": 2,  # 内存中的一个map中的数据保存的天数，超过这个天数则清除这个天数之前的相关数据
    "model_csv_path": str(BASE_PATH / "system" / "model" / "map_control" / "model_csv"),  # 用于模型初次训练的数据集
    "python_exe": r"D:/anaconda/envs/py3921/python.exe",  # 训练子进程使用的Python解释器路径
    "gui": {  # 界面标题、版本和塔/泵显示配置
        "sys_title": "西热钢厂1号机组寻优软件系统",  # 软件窗口标题
        "version": "版本：V1.0  构建日期：2026/8/1",  # 界面显示版本号和构建日期
        "tower": {  # 界面展示的塔结构；单塔只保留 1，双塔增加 2
            "1": {  # 配置参数 1；修改前请确认调用模块和数据单位
                "name": "一级塔",  # 界面显示名称
                "bump": [  # 界面展示的循环泵列表；code 必须与实时字段一致
                    {"code": "xstjyxhb_ADL", "name": "循环泵A"},
                    {"code": "xstjyxhb_BDL", "name": "循环泵B"},
                    {"code": "xstjyxhb_CDL", "name": "循环泵C"},
                    {"code": "xstjyxhb_DDL", "name": "循环泵D"},
                    {"code": "xstjyxhb_EDL", "name": "循环泵E"},
                ],
            },
            # "2": {
            #     "name": "二级塔",
            #     "bump": [
            #         {"code": "aptjyxhb_ADL", "name": "循环泵E"},
            #         {"code": "aptjyxhb_BDL", "name": "循环泵F"},
            #         {"code": "aptjyxhb_CDL", "name": "循环泵G"},
            #     ],
            # },
        },
    },
}
