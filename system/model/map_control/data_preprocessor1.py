import pandas as pd   # 数据处理
import numpy as np    # 数值计算
from datetime import datetime
from sklearn.neighbors import LocalOutlierFactor    # 离群值检测算法
import logging        # 日志记录
import os             # 日志记录
import traceback      # 异常堆栈跟踪
plt = None  # 只为兼容原始文件结构，如需画图可自行引入matplotlib
from system.base.config.DataPreprocessorConfig import *
# 修改日志路径
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'logs')
LOG_PATH = os.path.join(LOG_DIR, 'data_preprocessor.log')

# 确保日志目录存在
os.makedirs(LOG_DIR, exist_ok=True)
# 配置日志系统
logging.basicConfig(
    level=logging.INFO,      # 日志级别为INFO
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',     # 日志格式
    handlers=[
        logging.FileHandler(LOG_PATH, encoding='utf-8'),      # 输出到文件
        logging.StreamHandler()                  # 输出到控制台
    ]
)
logger = logging.getLogger('DataPreprocessor')          # 创建名为DataPreprocessor的日志记录器

class DataPreprocessor:
    def __init__(self, config=None):
        self.config = {**DATA_PREPROCESSOR_CONFIG, **(config or {})}
        self.data_buffer = {}
        self.pump_columns = {'xst': [], 'apt': []}
        self.initialize_buffers()
        logger.info("实时数据预处理器初始化完成")

    def initialize_buffers(self):
        """初始化数据缓冲区"""
        # 为所有配置的滤波器初始化缓冲区
        for col in self.config['filter_config'].keys():
            self.data_buffer[col] = []

    def detect_pump_columns(self, data_dict):
        """动态检测泵相关的列"""
        self.pump_columns = {'xst': [],
            'apt': []}
        for key in data_dict.keys():
            for tower, prefix in self.config['pump_prefix'].items():
                if key.startswith(prefix):
                    self.pump_columns[tower].append(key)
        # 对每个塔的泵列表进行排序，确保顺序一致
        for tower in self.pump_columns:
            self.pump_columns[tower].sort()
        logger.debug(f"检测到泵列: xst={self.pump_columns['xst']}, apt={self.pump_columns['apt']}")

    def process_ph_values(self, data_dict):
        """处理PH值，合并多个PH为平均值"""
        result = data_dict.copy()
        
        # 检查是否启用PH合并
        if not self.config['feature_generation'].get('enable_ph_merge', True):
            return result
            
        # 处理吸收塔PH
        xst_phs = []
        if 'xstjy_PH1' in data_dict and data_dict['xstjy_PH1'] is not None:
            try:
                ph_value = float(data_dict['xstjy_PH1'])
                if not pd.isna(ph_value):
                    xst_phs.append(ph_value)
            except (ValueError, TypeError):
                logging.warning(f"无法转换 xstjy_PH1={data_dict['xstjy_PH1']} 为浮点数")
                
        if 'xstjy_PH2' in data_dict and data_dict['xstjy_PH2'] is not None:
            try:
                ph_value = float(data_dict['xstjy_PH2'])
                if not pd.isna(ph_value):
                    xst_phs.append(ph_value)
            except (ValueError, TypeError):
                logging.warning(f"无法转换 xstjy_PH2={data_dict['xstjy_PH2']} 为浮点数")
                
        # 处理APT塔PH
        apt_phs = []
        if 'aptjy_PH1' in data_dict and data_dict['aptjy_PH1'] is not None:
            try:
                ph_value = float(data_dict['aptjy_PH1'])
                if not pd.isna(ph_value):
                    apt_phs.append(ph_value)
            except (ValueError, TypeError):
                logging.warning(f"无法转换 aptjy_PH1={data_dict['aptjy_PH1']} 为浮点数")
                
        if 'aptjy_PH2' in data_dict and data_dict['aptjy_PH2'] is not None:
            try:
                ph_value = float(data_dict['aptjy_PH2'])
                if not pd.isna(ph_value):
                    apt_phs.append(ph_value)
            except (ValueError, TypeError):
                logging.warning(f"无法转换 aptjy_PH2={data_dict['aptjy_PH2']} 为浮点数")
        
        # 计算平均值
        if xst_phs:
            result['xstjy_PH'] = sum(xst_phs) / len(xst_phs)
        if apt_phs:
            result['aptjy_PH'] = sum(apt_phs) / len(apt_phs)
        
        return result

    def filter_realtime_data(self, data_dict):
        """先限幅，再滤波"""
        try:
            result = data_dict.copy()
            
            # 1. 限幅处理
            for col, value in data_dict.items():
                if col in self.config['limit_config']:
                    try:
                        # 转换为浮点数
                        if value in [None, '', 'N/A'] or (isinstance(value, float) and pd.isna(value)):
                            value = 0.0
                        else:
                            value = float(value)
                        
                        # 应用限幅
                        limits = self.config['limit_config'][col]
                        result[col] = np.clip(value, limits['min'], limits['max'])
                    except (ValueError, TypeError) as e:
                        logger.warning(f"限幅处理失败 {col}: {str(e)}")
                        result[col] = value
            
            # 2. 滤波处理
            for col, value in result.items():
                if col in self.config['filter_config']:
                    try:
                        # 更新缓冲区
                        if col not in self.data_buffer:
                            self.data_buffer[col] = []
                        self.data_buffer[col].append(value)
                        
                        # 维护滑动窗口
                        window_size = self.config['filter_config'][col]['params'].get('window', self.config.get('buffer_size', 100))
                        if len(self.data_buffer[col]) > window_size:
                            self.data_buffer[col].pop(0)
                        
                        # 应用滤波
                        method = self.config['filter_config'][col]['method']
                        min_required = self.config['min_data_required'][method]
                        
                        if len(self.data_buffer[col]) >= min_required:
                            data_series = pd.Series(self.data_buffer[col])
                            params = self.config['filter_config'][col]['params']
                            
                            filtered_value = self._apply_filter(data_series, method, params)
                            result[col] = filtered_value.iloc[-1] if hasattr(filtered_value, 'iloc') else filtered_value
                            logger.debug(f"{col} 滤波成功: {value} -> {result[col]}")
                    except Exception as e:
                        #logger.error(f"{col} 滤波失败: {str(e)}")
                        result[col] = value
                        
            return result
        except Exception as e:
            logger.error(f"实时数据处理失败: {str(e)}")
            logger.debug(traceback.format_exc())
            return data_dict

    def generate_features(self, data_dict):
        """生成新特征，只处理浆液循环泵状态"""
        try:
            result = data_dict.copy()
            
            # 1. 检测泵列
            self.detect_pump_columns(data_dict)
            
            # 2. 处理PH值
            result = self.process_ph_values(result)
            
            # 3. 只生成浆液循环泵状态变量
            if self.config['feature_generation'].get('enable_pump_status', True):
                xst_pump_status = []
                for tower, prefix in self.config['pump_prefix'].items():
                    pump_status = []
                    for col in self.pump_columns[tower]:
                        status = 1 if data_dict.get(col, 0) > self.config['current_threshold'] else 0
                        pump_name = col.replace(prefix, '')
                        status_key = f"{tower}_{pump_name}_status"
                        result[status_key] = status
                        pump_status.append(str(status))
                    result[f'{tower}_pump_status'] = '-'.join(pump_status)
                    if tower == 'xst':
                        xst_pump_status = pump_status
                    else:
                        xst_pump_status.extend(pump_status)
                
                # 组合泵状态（只包含浆液循环泵）
                result['combined_pump_status'] = '-'.join(xst_pump_status)
            
            # # 新增 3.5：按新公式重算原烟气量 yyq_LL（单位 m3/h）
            # try:
            #     glfl = data_dict.get('glfl', None)      # 机组送风量 t/h
            #     yyq_o2 = data_dict.get('yyq_O2', None)  # 实际氧量 %
            #     if glfl is not None and yyq_o2 is not None:
            #         glfl_val = float(glfl)
            #         yyq_o2_val = float(yyq_o2)
            #         result['yyq_LL'] = (glfl_val / 1200.0) * 1180000.0 * (21.0 - yyq_o2_val) / 15.0
            #         logger.debug(f"yyq_LL 重新计算: glfl={glfl_val}, yyq_O2={yyq_o2_val}, yyq_LL={result['yyq_LL']}")
            # except Exception as e:
            #     logger.warning(f"yyq_LL 重算失败，将沿用原值: {str(e)}")
            
            # 4. 计算液气比和脱硫效率
            if self.config['feature_generation'].get('enable_liquid_gas_ratio', True):
                try:
                    # 使用上面更新后的 yyq_LL
                    yyq_ll = result.get('yyq_LL', data_dict.get('yyq_LL', 0))
                    if yyq_ll > 0:
                        total_flow = 0
                        # 只计算浆液循环泵的流量贡献
                        for tower in self.pump_columns:
                            for pump_col in self.pump_columns[tower]:
                                if data_dict.get(pump_col, 0) > self.config['current_threshold']:
                                    base_value = self.config['pump_base_values'].get(tower, {}).get(
                                        pump_col, 
                                        self.config['default_base_values'].get(tower, 6000)
                                    )
                                    total_flow += base_value
                        
                        result['liquid_gas_ratio'] = total_flow * 1000 / yyq_ll if yyq_ll > 0 else 0
                        logger.debug(f"计算液气比: total_flow={total_flow}, yyq_LL={yyq_ll}, l/g={result['liquid_gas_ratio']}")
                    
                    # 计算脱硫效率
                    if 'yyq_SO2' in data_dict and 'jyq_SO2' in data_dict:
                        yyq_so2 = data_dict['yyq_SO2']
                        jyq_so2 = data_dict['jyq_SO2']
                        if yyq_so2 > 0 and jyq_so2 >= 0:
                            result['desulfurization_efficiency'] = (yyq_so2 - jyq_so2) / yyq_so2
                            logger.debug(f"计算脱硫效率: {result['desulfurization_efficiency']}")
                            
                except Exception as e:
                    logger.error(f"特征计算失败: {str(e)}")
                    logger.debug(traceback.format_exc())
                
            return result
            
        except Exception as e:
            logger.error(f"特征生成失败: {str(e)}")
            logger.debug(traceback.format_exc())
            return data_dict

    def _apply_filter(self, data_series, method, params):
        """应用具体的滤波方法"""
        try:
            if method == 'dynamic_ema':
                return self._dynamic_ema_filter(data_series, **params)
            elif method == 'sma_lof':
                return self._sma_lof_filter(data_series, **params)
            elif method == 'dual_filter':
                return self._dual_filter(data_series, **params)
            elif method == 'inertial_filter':
                return self._inertial_filter(data_series, **params)
            elif method == 'piecewise_kalman':
                return self._piecewise_kalman_filter(data_series, **params)
            elif method == 'hampel_slope':
                return self._hampel_slope_filter(data_series, **params)
            else:
                logger.warning(f"未知的滤波方法: {method}")
                return data_series
        except Exception as e:
            #logger.error(f"滤波方法 {method} 应用失败: {str(e)}")
            return data_series

    # 以下是原有的滤波方法实现，保持不变
    def _dynamic_ema_filter(self, data_col, beta=0.9, window=30):
        try:
            ema = np.zeros(len(data_col))
            ema[0] = data_col.iloc[0]
            for i in range(1, len(data_col)):
                ema[i] = beta * data_col.iloc[i] + (1 - beta) * ema[i - 1]
            return pd.Series(ema, index=data_col.index)
        except Exception as e:
            #logger.error(f"动态指数移动平均滤波失败: {str(e)}")
            return data_col

    def _sma_lof_filter(self, data_col, window=42, n_neighbors=20):
        try:
            # 先去除None和NaN
            data_col = data_col.dropna()
            if len(data_col) == 0:
                return data_col  # 全是空，直接返回
            smoothed = data_col.rolling(window, center=True, min_periods=1).mean().ffill().bfill()
            lof = LocalOutlierFactor(n_neighbors=n_neighbors, contamination='auto')
            outliers = lof.fit_predict(smoothed.values.reshape(-1, 1))
            valid_mask = outliers == 1
            filtered = data_col.where(valid_mask).ffill().bfill()
            return filtered
        except Exception as e:
            #logger.error(f"移动平均结合局部离群因子滤波失败: {str(e)}")
            return data_col

    def _dual_filter(self, data_col, ema_beta=0.9, median_window=30):
        try:
            ema = self._dynamic_ema_filter(data_col, beta=ema_beta)
            return ema.rolling(median_window, center=True, min_periods=1).median()
        except Exception as e:
            #logger.error(f"双滤波器组合失败: {str(e)}")
            return data_col

    def _inertial_filter(self, data_col, T=120, max_gradient=0.5):
        try:
            alpha = 1 - np.exp(-1 / T)
            filtered = np.zeros(len(data_col))
            filtered[0] = data_col.iloc[0]
            for i in range(1, len(data_col)):
                filtered[i] = alpha * data_col.iloc[i] + (1 - alpha) * filtered[i - 1]
            gradient = np.abs(np.diff(filtered))
            invalid = np.where(gradient > max_gradient)[0] + 1
            filtered[invalid] = np.nan
            return pd.Series(filtered, index=data_col.index).ffill().bfill()
        except Exception as e:
            #logger.error(f"惯性梯度滤波失败: {str(e)}")
            return data_col

    def _piecewise_kalman_filter(self, data_col, steady_noise=0.1, transient_noise=1.0):
        try:
            values = data_col.values
            n = len(values)
            filtered = np.zeros(n)
            P_values = np.zeros(n)
            
            filtered[0] = values[0]
            P_values[0] = steady_noise
            
            for i in range(1, n):
                gradient = np.abs(values[i] - values[i-1])
                Q = transient_noise if gradient > 0.5 * np.nanstd(values) else steady_noise
                
                x_pred = filtered[i-1]
                P_pred = P_values[i-1] + Q
                
                K = P_pred / (P_pred + Q)
                filtered[i] = x_pred + K * (values[i] - x_pred)
                P_values[i] = (1 - K) * P_pred
            
            return pd.Series(filtered, index=data_col.index)
        except Exception as e:
            #logger.error(f"分段卡尔曼滤波失败: {str(e)}")
            return data_col

    def _hampel_slope_filter(self, data_col, window=15, sigma=3, max_slope=0.2):
        try:
            values = data_col.values
            n = len(values)
            filtered = values.copy().astype(float)
            
            rolling_median = pd.Series(values).rolling(
                window=window, center=True, min_periods=1
            ).median().values
            
            for i in range(n):
                start = max(0, i - window//2)
                end = min(n, i + window//2 + 1)
                window_data = values[start:end]
                
                if len(window_data) >= 2:
                    x = np.arange(len(window_data))
                    slope = np.polyfit(x, window_data, 1)[0] if not np.all(np.isnan(window_data)) else 0
                    
                    if abs(slope) > max_slope:
                        filtered[i] = rolling_median[i]
                    else:
                        median = np.nanmedian(window_data)
                        mad = np.nanmedian(np.abs(window_data - median))
                        if abs(filtered[i] - median) > sigma * 1.4826 * mad:
                            filtered[i] = median
            
            return pd.Series(filtered, index=data_col.index)
        except Exception as e:
            #logger.error(f"Hampel斜率滤波失败: {str(e)}")
            return data_col
def test_data_preprocessor():
    """测试数据预处理器"""
    import pandas as pd
    import numpy as np
    from datetime import datetime
    
    # 创建预处理器实例
    preprocessor = DataPreprocessor()
    
    # 打印关键配置，帮助调试
    print("=== 配置信息 ===")
    print(f"feature_generation.enable_liquid_gas_ratio: {preprocessor.config['feature_generation'].get('enable_liquid_gas_ratio', True)}")
    print(f"current_threshold: {preprocessor.config.get('current_threshold', 'Not configured')}")
    print(f"pump_prefix: {preprocessor.config.get('pump_prefix', {})}")
    
    # 创建测试数据
    test_data = {
        'date': datetime.now(),
        'jzfh': 300.5,
        'zml': 150.2,
        'yyq_SO2': 800.2,
        'jyq_SO2': 35.0,  # 添加净烟气SO2数据
        'jyq_LL': 145.0,
        'yyq_LL': 145.0,  # 确保烟气流量存在
        'yyq_WD': 120.5,
        'yyq_YL': 2000.0,
        'xstjy_PH1': 5.8,
        'xstjy_PH2': 6.2,
        'xst_YW': 7.5,
        'xstshsjy_LL': 35.0,
        'xstjyxhb_ADL': 30.0,
        'xstjyxhb_BDL': 28.5,
        'xstjyxhb_CDL': 29.0,
        'xstjyxhb_DDL': 31.0,
        'xstxhb_AYL': 1500.0,
        'xstxhb_BYL': 1520.0,
        'xstxhb_CYL': 1480.0,
        'xstxhb_DYL': 1510.0,
        'aptjy_PH1': 4.5,
        'aptjy_PH2': 4.7,
        'apt_YW': 11.2,
        'aptshsjy_LL': 22.0,
        'aptshsjy_FMKD': 85.0,
        'aptjyxhb_ADL': 32.0,
        'aptjyxhb_BDL': 31.5,
        'aptjyxhb_CDL': 30.8
    }
    
    # 测试滤波功能
    print("=== 测试滤波功能 ===")
    filtered_data = preprocessor.filter_realtime_data(test_data)
    
    # 打印检测到的泵列
    print("\n=== 检测到的泵列 ===")
    preprocessor.detect_pump_columns(filtered_data)
    print(f"吸收塔泵列: {preprocessor.pump_columns['xst']}")
    print(f"APT塔泵列: {preprocessor.pump_columns['apt']}")
    
    # 测试特征生成功能
    print("\n=== 测试特征生成功能 ===")
    features_data = preprocessor.generate_features(filtered_data)
    
    # 打印液气比计算中间值
    print("\n=== 液气比计算调试 ===")
    yyq_ll = filtered_data.get('yyq_LL', 0)
    print(f"烟气流量(yyq_LL): {yyq_ll}")
    
    total_flow = 0
    for tower in ['xst', 'apt']:
        for pump_col in preprocessor.pump_columns[tower]:
            current = filtered_data.get(pump_col, 0)
            is_running = current > preprocessor.config.get('current_threshold', 20)
            base_value = preprocessor.config.get('pump_base_values', {}).get(tower, {}).get(
                pump_col, 
                preprocessor.config.get('default_base_values', {}).get(tower, 6000 if tower == 'xst' else 6000)
            )
            print(f"泵 {pump_col}: 电流={current}, 是否运行={is_running}, 基础值={base_value}")
            if is_running:
                total_flow += base_value
    
    print(f"总流量: {total_flow}")
    print(f"计算的液气比: {total_flow / yyq_ll if yyq_ll > 0 else 0}")
    
    # 打印所有生成的特征
    print("\n生成的所有特征:")
    for key, value in features_data.items():
        print(f"{key}: {value}")
    
    # 特别关注液气比和脱硫效率
    print("\n=== 最终结果 ===")
    print(f"液气比: {features_data.get('liquid_gas_ratio')}")
    print(f"脱硫效率: {features_data.get('desulfurization_efficiency')}")
    
    return features_data

if __name__ == "__main__":
    result = test_data_preprocessor()
