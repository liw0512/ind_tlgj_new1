"""
配置说明（自动补全）
====================
本文件管理原始测点限幅、实时滤波、泵状态识别和派生特征生成。
换厂时必须核对每个测点的物理量程、采样周期、泵电流阈值和泵基准值；
过滤窗口按样本点计算，采样周期改变后应同步重新评估窗口大小。
"""

DATA_PREPROCESSOR_CONFIG = {
    # 限幅配置 - 从Process4MapControl.py移植过来
    'limit_config': {  # 各现场测点物理限幅；min/max 超出后按预处理逻辑截断或判异常
        'jzfh': {'min': 0.0, 'max': 650},  # 机组负荷
        'yyq_SO2': {'min': 0.0, 'max': 5000},  # 原烟气SO2
        'yyq_O2': {'min': 0.0, 'max': 21},    # 原烟气O2
        'yyq_LL': {'min': 0.0, 'max': 2250000},   # 原烟气流量
        'yyq_WD': {'min': 0.0, 'max': 150},    # 原烟气温度
        'xstjy_PH': {'min': 0.0, 'max': 14},  # 一级塔浆液PH
        'xstshsjy_MD': {'min': 1000.0, 'max': 1500.0},  # 一级塔石灰石浆液密度
        'xst_YW': {'min': 0.0, 'max': 12},    # 一级塔液位
        'xstjyxhb_ADL': {'min': 0.0, 'max': 100},  # 一级塔循环泵电流值
        'xstjyxhb_BDL': {'min': 0.0, 'max': 100},  # 一级塔循环泵电流值
        'xstjyxhb_CDL': {'min': 0.0, 'max': 100},  # 一级塔循环泵电流值
        'xstjyxhb_DDL': {'min': 0.0, 'max': 100},  # 一级塔循环泵电流值
        'xstyhfj_ADL': {'min': 0.0, 'max': 60},  # 一级塔氧化风机电流
        'xstyhfj_BDL': {'min': 0.0, 'max': 60},  # 一级塔氧化风机电流
        'jyq_SO2': {'min': 0.0, 'max': 100},  # 净烟气SO2
        'jyq_LL': {'min': 0.0, 'max': 2250000},   # 净烟气流量
        'zml': {'min': 0.0, 'max': 500},   # 总煤量
        'yyq_FC': {'min': 0.0, 'max': 50},    # 原烟气粉尘浓度
        'jyq_FC': {'min': 0.0, 'max': 10},    # 净烟气粉尘浓度
        'xstshsjy_LL': {'min': 0.0, 'max': 100},  # 一级塔石灰石浆液流量
        'xstgjb_ADL': {'min': 0.0, 'max': 60},  # 一级塔供浆泵电流
        'xstylsy_ND': {'min': 0.0, 'max': 50000},  # 亚硫酸盐浓度
        'xstjy_MD': {'min': 1000.0, 'max': 1500},  # 一级塔浆液密度
    },

    # 优化后的滤波配置
    'filter_config': {  # 各字段滤波算法及参数；窗口单位通常为样本点
        'xstshsjy_MD': {'method': 'hampel_slope', 'params': {'window': 10, 'sigma': 2, 'max_slope': 0.15}},  # 配置参数 xstshsjy_MD；修改前请确认调用模块和数据单位
        'aptshsjy_MD': {'method': 'hampel_slope', 'params': {'window': 10, 'sigma': 2, 'max_slope': 0.15}},  # 配置参数 aptshsjy_MD；修改前请确认调用模块和数据单位
        'yyq_SO2': {'method': 'sma_lof', 'params': {'window': 20, 'n_neighbors': 10}},  # 配置参数 yyq_SO2；修改前请确认调用模块和数据单位
        'jyq_SO2': {'method': 'sma_lof', 'params': {'window': 20, 'n_neighbors': 10}},  # 配置参数 jyq_SO2；修改前请确认调用模块和数据单位
        'llyd_SO2': {'method': 'sma_lof', 'params': {'window': 20, 'n_neighbors': 10}},  # 配置参数 llyd_SO2；修改前请确认调用模块和数据单位
        'yyq_FC': {'method': 'piecewise_kalman', 'params': {'steady_noise': 0.05, 'transient_noise': 0.5}},  # 配置参数 yyq_FC；修改前请确认调用模块和数据单位
        'jyq_FC': {'method': 'piecewise_kalman', 'params': {'steady_noise': 0.05, 'transient_noise': 0.5}},  # 配置参数 jyq_FC；修改前请确认调用模块和数据单位
        'yyq_O2': {'method': 'dual_filter', 'params': {'ema_beta': 0.85, 'median_window': 10}},  # 配置参数 yyq_O2；修改前请确认调用模块和数据单位
        'yyq_LL': {'method': 'hampel_slope', 'params': {'window': 12, 'sigma': 2.5, 'max_slope': 0.2}},  # 配置参数 yyq_LL；修改前请确认调用模块和数据单位
        'jyq_LL': {'method': 'hampel_slope', 'params': {'window': 12, 'sigma': 2.5, 'max_slope': 0.2}},  # 配置参数 jyq_LL；修改前请确认调用模块和数据单位
        'yyq_WD': {'method': 'inertial_filter', 'params': {'T': 60, 'max_gradient': 0.3}},  # 配置参数 yyq_WD；修改前请确认调用模块和数据单位
        'xst_YW': {'method': 'inertial_filter', 'params': {'T': 90, 'max_gradient': 0.4}},  # 配置参数 xst_YW；修改前请确认调用模块和数据单位
        'xstshsjy_LL': {'method': 'dual_filter', 'params': {'ema_beta': 0.8, 'median_window': 12}},  # 配置参数 xstshsjy_LL；修改前请确认调用模块和数据单位
        'apt_YW': {'method': 'inertial_filter', 'params': {'T': 90, 'max_gradient': 0.4}},  # 配置参数 apt_YW；修改前请确认调用模块和数据单位
        'aptshsjy_LL': {'method': 'dual_filter', 'params': {'ema_beta': 0.8, 'median_window': 12}}  # 配置参数 aptshsjy_LL；修改前请确认调用模块和数据单位
    },

    # 其他配置保持不变
    'pump_prefix': {  # 按塔识别循环泵电流字段的前缀
        'xst': 'xstjyxhb_',  # 配置参数 xst；修改前请确认调用模块和数据单位
        'apt': 'aptjyxhb_'  # 配置参数 apt；修改前请确认调用模块和数据单位
    },
    
    'ph_config': {  # 每座塔参与合并的 pH 测点字段列表；单塔可保留 xst 并禁用 apt 数据
        'xst': ['xstjy_PH1', 'xstjy_PH2'],  # 配置参数 xst；修改前请确认调用模块和数据单位
        'apt': ['aptjy_PH1', 'aptjy_PH2']  # 配置参数 apt；修改前请确认调用模块和数据单位
    },
    
    'pump_base_values': {  # 泵开启时用于归一化或特征生成的基准值，需按现场标定
        'xst': {  # 配置参数 xst；修改前请确认调用模块和数据单位
            'xstjyxhb_ADL': 6600,  # 配置参数 xstjyxhb_ADL；修改前请确认调用模块和数据单位
            'xstjyxhb_BDL': 6600,  # 配置参数 xstjyxhb_BDL；修改前请确认调用模块和数据单位
            'xstjyxhb_CDL': 6600,  # 配置参数 xstjyxhb_CDL；修改前请确认调用模块和数据单位
            'xstjyxhb_DDL': 6600,  # 配置参数 xstjyxhb_DDL；修改前请确认调用模块和数据单位
            'xstjyxhb_EDL': 6600  # 配置参数 xstjyxhb_EDL；修改前请确认调用模块和数据单位
        },
        # 'apt': {
        #     'aptjyxhb_ADL': 6400,
        #     'aptjyxhb_BDL': 6400,
        #     'aptjyxhb_CDL': 6400
        # }
    },
    
    'current_threshold': 10.0,  # 泵运行/停运判断电流阈值，单位与现场电流字段一致
    'buffer_size': 100,  # 实时滤波缓存最大样本数
    
    'min_data_required': {  # 各滤波算法开始输出稳定结果所需的最少样本数
        'dynamic_ema': 5,  # 动态 EMA 启动所需最少样本数
        'sma_lof': 10,  # SMA+LOF 启动所需最少样本数
        'dual_filter': 6,  # 双重滤波启动所需最少样本数
        'inertial_filter': 6,  # 惯性滤波启动所需最少样本数
        'piecewise_kalman': 6,  # 分段卡尔曼滤波启动所需最少样本数
        'hampel_slope': 10  # Hampel+斜率滤波启动所需最少样本数
    },
    
    'default_base_values': {  # 未找到具体泵基准值时按塔使用的默认值
        'xst': 6600,  # 配置参数 xst；修改前请确认调用模块和数据单位
        'apt': 6600  # 配置参数 apt；修改前请确认调用模块和数据单位
    },
    
    'feature_generation': {  # 预处理后是否生成 pH 合并、泵状态和液气比特征
        'enable_ph_merge': True,  # 是否生成每座塔合并后的 pH 特征
        'enable_pump_status': True,  # 是否根据电流生成各泵 0/1 状态和组合状态
        'enable_liquid_gas_ratio': True  # 是否生成液气比特征；需要流量字段完整
    }
}
