"""
配置说明（自动补全）
====================
SysConfigProd：系统部署、数据库、实时周期、训练解释器和界面配置。
路径和数据库连接必须按部署机器修改；算法参数不应放在此文件。
修改后需要重启服务。数据库密码建议改为环境变量注入。
"""

config = {
    'base_path': "/home/pgrm/ind_optim_serv",  # 程序部署根目录；不同环境必须改为实际路径
    'filename': "/home/pgrm/logs",  # 日志输出目录
    'dbconnetion': "postgresql+psycopg2://root:tswcbyy5413LX@127.0.0.1:5432/ind_optim_serv",  # 数据库连接串；建议通过环境变量管理，不要提交真实生产密码
    "mod_args_path": "/home/pgrm/ind_optim_serv/files/model",  # 模型参数文件目录
    "download_path": "/home/pgrm/ind_optim_serv/files",  # 下载、历史数据和模型文件的公共目录
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
    "dump_pg_cmd": "pg_dump -U root -d ind_optim_sys |gzip > /home/pgrm"  # PostgreSQL 备份命令；执行账户必须具备权限
}
