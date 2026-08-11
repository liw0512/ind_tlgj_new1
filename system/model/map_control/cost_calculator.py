import numpy as np
import pandas as pd
import time
import logging
import json
import os
from collections import deque
from typing import Dict, Any, List

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("CostCalculator")
# 在文件开头的常量定义部分添加
HIGH_VOLTAGE = 6000  # 高压设备线电压（浆液循环泵和氧化风机）
LOW_VOLTAGE = 380   # 低压设备线电压（石膏排出泵、供浆泵和搅拌器）

class CostCalculator:
    def __init__(self, config_path=None):
        """初始化成本计算器"""
        # 默认配置
        self.default_config = {
            # 电力相关参数
            "electricity": {
                "cos_phi": 0.86,    # 功率因数（cosφ）
                "mu": 0.95,         # 电机效率（μ）
                "P_elec": 0.38      # 成本电价（P电）[元/kW·h]
            },
            
            # 一级塔石灰石成本相关参数
            "limestone_primary": {
                "K": 0.9,          # 石灰石中CaCO3的纯度 [%]
                "M": 1.05,          # MCa/s钙硫摩尔比
                "P_stone": 50    # 一级塔石灰石采购价格 [元/t]
            },
            
            # 二级塔石灰石成本相关参数
            "limestone_secondary": {
                "K": 0.9,          # 石灰石中CaCO3的纯度 [%]
                "M": 1.05,          # MCa/s钙硫摩尔比
                "P_stone": 90    # 二级塔石灰石采购价格 [元/t]
            },
            
            # 排污和石膏相关参数
            "pollution": {
                "F": 0.95,       # SO2排污当量 [当量/kg]
                "P_pollute": 2000,  # 应税大气污染物税额 [元/当量]
                "eta_gypsum": 0.9,  # 石膏含湿率（η膏）
                "P_gypsum": 30,   # 石膏出售价格 [元/t]
                "C_clean_dust": 0.1  # 净烟气粉尘浓度 [mg/m3]
            },
            
            # 设备配置
            # 修改设备配置部分
            "equipment_config": {
                "吸收塔浆液循环泵": {
                    "prefix": "xstjyxhb",
                    "voltage": HIGH_VOLTAGE,
                    "devices": [
                        {"suffix": "ADL", "rated_power": 1000, "efficiency": 0.9},
                        {"suffix": "BDL", "rated_power": 1000, "efficiency": 0.9},
                        {"suffix": "CDL", "rated_power": 900, "efficiency": 0.9},
                        {"suffix": "DDL", "rated_power": 800, "efficiency": 0.9}
                    ]
                },
                "APT塔浆液循环泵": {
                    "prefix": "aptjyxhb",
                    "voltage": HIGH_VOLTAGE,
                    "devices": [
                        {"suffix": "ADL", "rated_power": 800, "efficiency": 0.9},
                        {"suffix": "BDL", "rated_power": 800, "efficiency": 0.9},
                        {"suffix": "CDL", "rated_power": 900, "efficiency": 0.9}
                       
                    ]
                },
                "一级塔搅拌器": {
                    "prefix": "xstjbq",
                    "voltage": LOW_VOLTAGE,
                    "devices": [
                        {"suffix": "ADL", "rated_power": 45, "efficiency": 0.85},
                        {"suffix": "BDL", "rated_power": 45, "efficiency": 0.85},
                        {"suffix": "CDL", "rated_power": 45, "efficiency": 0.85},
                        {"suffix": "DDL", "rated_power": 45, "efficiency": 0.85}
                    ]
                },
                "APT塔搅拌器": {
                    "prefix": "aptjbq",
                    "voltage": LOW_VOLTAGE,
                    "devices": [
                        {"suffix": "ADL", "rated_power": 45, "efficiency": 0.85},
                        {"suffix": "BDL", "rated_power": 45, "efficiency": 0.85},
                        {"suffix": "CDL", "rated_power": 45, "efficiency": 0.85},
                        {"suffix": "DDL", "rated_power": 45, "efficiency": 0.85}
                    ]
                },
                "一级塔氧化风机": {
                    "prefix": "xstyhfj",
                    "voltage": LOW_VOLTAGE,
                    "devices": [
                        {"suffix": "ADL", "rated_power": 450, "efficiency": 0.9},
                        {"suffix": "BDL", "rated_power": 450, "efficiency": 0.9}
                    ]
                },
                "APT塔氧化风机": {
                    "prefix": "aptyhfj",
                    "voltage": HIGH_VOLTAGE,
                    "devices": [
                        {"suffix": "ADL", "rated_power": 400, "efficiency": 0.9},
                        {"suffix": "BDL", "rated_power": 400, "efficiency": 0.9}
                    ]
                },
                "石膏排出泵": {
                    "prefix": "sgpcb",
                    "voltage": LOW_VOLTAGE,
                    "devices": [
                        {"suffix": "ADL", "rated_power": 55, "efficiency": 0.85}
                    ]
                },
                "一级塔供浆泵": {
                    "prefix": "xstgjb",
                    "voltage": LOW_VOLTAGE,
                    "devices": [
                        {"suffix": "ADL", "rated_power": 75, "efficiency": 0.85}
                    ]
                },
                "APT塔供浆泵": {
                    "prefix": "aptgjb",
                    "voltage": LOW_VOLTAGE,
                    "devices": [
                        {"suffix": "ADL", "rated_power": 75, "efficiency": 0.85}
                    ]
                }
            }
        }
        
        # 加载配置文件
        self.config = self.load_config(config_path)
        
        # 设置参数
        self._set_parameters()
        

    def _set_parameters(self):
        # 电力相关参数
        elec = self.config.get("electricity", {})
        self.cos_phi = elec.get("cos_phi", 1)
        self.mu = elec.get("mu", 1)
        self.P_elec = elec.get("P_elec", 1)
        
        # 一级塔石灰石参数
        limestone_primary = self.config.get("limestone_primary", {})
        self.K_primary = limestone_primary.get("K", 0.9)
        self.M_primary = limestone_primary.get("M", 1.05)
        self.P_stone_primary = limestone_primary.get("P_stone", 50)
        
        # 二级塔石灰石参数
        limestone_secondary = self.config.get("limestone_secondary", {})
        self.K_secondary = limestone_secondary.get("K", 0.9)
        self.M_secondary = limestone_secondary.get("M", 1.05)
        self.P_stone_secondary = limestone_secondary.get("P_stone", 90)
        
        # 排污和石膏相关参数
        pollution = self.config.get("pollution", {})
        self.F = pollution.get("F", 0.95)
        self.P_pollute = pollution.get("P_pollute", 1)
        self.eta_gypsum = pollution.get("eta_gypsum", 0.9)
        self.P_gypsum = pollution.get("P_gypsum", 1)
        self.C_clean_dust = pollution.get("C_clean_dust", 0.1)
        
        # 设备配置
        self.equipment_config = self.config.get("equipment_config", {})
        self.current_threshold = 20  # 添加电流阈值参数

    def load_config(self, config_path):
        """加载配置文件"""
        if config_path and os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                logger.info(f"成功加载配置文件: {config_path}")
                return config
            except Exception as e:
                logger.error(f"加载配置文件失败: {e}")
                logger.info("使用默认配置")
                return self.default_config
        else:
            logger.info("配置文件不存在，使用默认配置")
            return self.default_config
    
    def save_default_config(self, path):
        """保存默认配置到文件"""
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self.default_config, f, indent=4, ensure_ascii=False)
            logger.info(f"默认配置已保存到: {path}")
            return True
        except Exception as e:
            logger.error(f"保存默认配置失败: {e}")
            return False
        
    def get_equipment_current(self, data: Dict[str, Any], prefix: str, suffix: str) -> float:
        """获取设备电流值，如果不存在则返回0"""
        try:
            field = f"{prefix}_{suffix}"
            value = data.get(field, 0)
            return float(value)
        except (ValueError, TypeError) as e:
            logger.warning(f"无法将字段 {field} 的值 '{value}' 转换为浮点数: {e}")
            return 0.0
    
    def is_equipment_running(self, current: float) -> bool:
        """判断设备是否运行"""
        try:
            return current > self.current_threshold
        except Exception as e:
            logger.error(f"判断设备运行状态出错: {e}")
            return False
    
    def calculate_device_power(self, current: float, voltage: float, efficiency: float, rated_power: float) -> float:
        """
        使用实际电流值或额定功率计算设备功率
        
        Args:
            current: 实际电流值(A)，如果为0或None则使用额定功率
            voltage: 线电压值(V)
            efficiency: 电机效率μ
            rated_power: 额定功率(kW)
        Returns:
            float: 功率(kW)
        """
        try:
            # 如果电流小于等于0或不存在，使用额定功率
            if current <= 0:
                return rated_power
                
            # 使用公式计算功率
            # P = √3 * U * I * cosφ / μ
            power = (np.sqrt(3) * voltage * current * self.cos_phi / efficiency) / 1000  # 除以1000转换为kW
            
            return power
        except Exception as e:
            logger.error(f"计算设备功率出错: {e}, 使用额定功率: {rated_power}kW")
            return rated_power
    
    def get_previous_h_ye(self) -> float:
        """获取历史液位数据"""
        try:
            return self.h_ye_history[0] if self.h_ye_history else 0
        except Exception as e:
            logger.error(f"获取历史液位数据出错: {e}")
            return 0
    
    def calculate_total_power(self, data: Dict[str, Any]) -> float:
        """计算所有设备的总功率"""
        total_power = 0
        
        try:
            # 计算每种设备的功率
            for equip_name, config in self.equipment_config.items():
                try:
                    prefix = config["prefix"]
                    devices = config.get("devices", [])
                    
                    # 遍历每个具体设备
                    for device in devices:
                        try:
                            suffix = device["suffix"]
                            rated_power = device["rated_power"]
                            efficiency = device["efficiency"]
                            
                            current = self.get_equipment_current(data, prefix, suffix)
                            # 直接使用电流值计算功率，不再判断阈值
                            power = self.calculate_device_power(current, rated_power, efficiency)
                            total_power += power
                        except KeyError as e:
                            logger.error(f"设备 {prefix}_{suffix} 配置缺少必要参数: {e}")
                        except Exception as e:
                            logger.error(f"计算设备 {prefix}_{suffix} 功率时出错: {e}")
                except KeyError as e:
                    logger.error(f"设备配置 {equip_name} 缺少必要参数: {e}")
                except Exception as e:
                    logger.error(f"处理设备 {equip_name} 时出错: {e}")
        except Exception as e:
            logger.error(f"计算设备功率总过程出错: {e}")
            
        return total_power
    
    def calculate_cost(self, data: Dict[str, Any]) -> Dict[str, float]:
        """计算成本"""
        try:
            if not isinstance(data, dict):
                raise TypeError(f"输入数据必须是字典类型，而不是 {type(data)}")
                    
            # 初始化结果字典
            result = {**data}
            
            # 计算总功率和电成本 - 修改为使用tower_power_consumption的结果
            try:
                # 直接使用tower_power_consumption已计算好的塔总电耗
                xst_power = data.get('xst_zdh', 0)  # 一级塔总电耗
                apt_power = data.get('apt_zdh', 0)  # 二级塔总电耗
                total_power = xst_power + apt_power
                
                # 计算电成本 = 总功率 × 电价
                M_elec_hour = total_power * self.P_elec
                result['M_elec_hour'] = M_elec_hour
            
            except Exception as e:
                logger.error(f"计算电成本出错: {e}")
                result['M_elec_hour'] = 0
            
            # 计算一级塔石灰石成本
            try:
                C_raw_primary = float(data.get('yyq_SO2', 0))
                Q_raw_primary = float(data.get('yyq_LL', 0))
                C_clean_primary = float(data.get('jyq_SO2', 0))
                
                if C_raw_primary == 0:
                    M_stone_primary = 0
                else:
                    M_stone_primary = (C_raw_primary * Q_raw_primary * 
                                    (C_raw_primary - C_clean_primary) / C_raw_primary * 
                                    self.M_primary * 100 / 64 * 1 / self.K_primary / 1e9 * 
                                    self.P_stone_primary)
                
                result['M_stone_primary'] = M_stone_primary
            except Exception as e:
                logger.error(f"计算一级塔石灰石成本出错: {e}")
                result['M_stone_primary'] = 0

            # 计算二级塔石灰石成本
            try:
                C_raw_secondary = float(data.get('yyq_SO2', 0))
                Q_raw_secondary = float(data.get('yyq_LL', 0))
                C_clean_secondary = float(data.get('jyq_SO2', 0))
                
                if C_raw_secondary == 0:
                    M_stone_secondary = 0
                else:
                    M_stone_secondary = (C_raw_secondary * Q_raw_secondary * 
                                    (C_raw_secondary - C_clean_secondary) / C_raw_secondary * 
                                    self.M_secondary * 100 / 64 * 1 / self.K_secondary / 1e9 * 
                                    self.P_stone_secondary)
                
                result['M_stone_secondary'] = M_stone_secondary
            except Exception as e:
                logger.error(f"计算二级塔石灰石成本出错: {e}")
                result['M_stone_secondary'] = 0
            
            # 计算排污成本
            try:
                Q_clean = float(data.get('jyq_LL', 0))
                C_clean = float(data.get('jyq_SO2', 0))
                
                if 0 < C_clean <= 10.5:
                    P_tax = 0.25 * self.P_pollute
                elif 10.5 < C_clean <= 17.5:
                    P_tax = 0.5 * self.P_pollute
                elif 17.5 < C_clean <= 35:
                    P_tax = self.P_pollute
                else:
                    P_tax = 0
                
                M_pollute = (C_clean * Q_clean * P_tax) / self.F/1000000000
                result['M_pollute'] = M_pollute
            except Exception as e:
                logger.error(f"计算排污成本出错: {e}")
                result['M_pollute'] = 0
            
            # 计算石膏收入
            try:
                # 获取原烟气SO2浓度和流量
                C_raw = float(data.get('yyq_SO2', 0))  # 原烟气SO2浓度
                Q_raw = float(data.get('yyq_LL', 0))   # 原烟气流量
                
                # 获取净烟气SO2浓度和流量
                C_clean = float(data.get('jyq_SO2', 0))  # 净烟气SO2浓度
                Q_clean = float(data.get('jyq_LL', 0))   # 净烟气流量
                
                # 获取粉尘浓度，如果没有则使用配置中的默认值
                C_raw_dust = float(data.get('yyq_FC', 0))  # 原烟气粉尘浓度
                C_clean_dust = float(data.get('jyq_FC', self.C_clean_dust))  # 净烟气粉尘浓度
                
                if C_raw == 0 or Q_raw == 0:
                    M_gypsum = 0
                else:
                    # 按照公式计算石膏产量
                    # M膏=[(C原烟-C净烟)/C原烟×Q原烟×C原烟×172/64 + (C原烟-C净烟)/C原烟×Q原烟×C原烟×100/64×(MCa/s-1) + 
                    #     (C原烟-C净烟)/C原烟×Q原烟×C原烟×100/64×MCa/s×(1-K)/K + C净烟尘]/η膏×10^-9×P膏
                    
                    # 脱除的SO2量
                    removed_so2 = (C_raw - C_clean) / C_raw * Q_raw * C_raw
                    
                    # 第一项：脱除SO2生成的CaSO4·2H2O
                    term1 = removed_so2 * 172 / 64
                    
                    # 第二项：过量石灰石中的CaCO3
                    term2 = removed_so2 * 100 / 64 * (self.M_primary - 1)
                    
                    # 第三项：石灰石中的杂质
                    term3 = removed_so2 * 100 / 64 * self.M_primary * (1 - self.K_primary) / self.K_primary
                    
                    # 第四项：净烟气粉尘
                    term4 = C_clean_dust * Q_clean
                    
                    # 石膏产量计算
                    if self.eta_gypsum == 0:
                        M_gypsum = 0
                    else:
                        # 石膏销售收入 = 石膏产量 × 石膏售价
                        M_gypsum = (term1 + term2 + term3 + term4) / self.eta_gypsum / 1e9 * self.P_gypsum
                
                result['M_gypsum'] = M_gypsum
            except Exception as e:
                logger.error(f"计算石膏收入出错: {e}")
                result['M_gypsum'] = 0
            
            # 计算总成本 = 电成本 + 一级塔石灰石成本 + 二级塔石灰石成本 + SO2排放纳税额 - 石膏销售收入
            try:
                total_cost = (result['M_elec_hour'] + 
                            result['M_stone_primary'] + 
                            result['M_stone_secondary'] + 
                            result['M_pollute'] - 
                            result['M_gypsum'])
                result['total_cost'] = total_cost
            except Exception as e:
                logger.error(f"计算总成本出错: {e}")
                result['total_cost'] = 0
            
            return result
            
        except Exception as e:
            logger.critical(f"成本计算整体过程出错: {e}")
            return {**data}

def main():
    # 示例使用
    calculator = CostCalculator()
    
    # 保存默认配置文件
    calculator.save_default_config("cost_calculator_config.json")
    
    # 示例数据
    data = {
        'date': '2024-09-01 09:27:34',
        # 一级塔数据
        'yyq_SO2': 1200.0,      # 一级塔入口SO2浓度
        'yyq_LL': 120.0,        # 一级塔入口烟气流量
        'jyq_SO2': 35.0,        # 一级塔出口SO2浓度
        'jyq_LL': 100.0,        # 一级塔出口烟气流量
        
        # # APT塔数据
        # 'apt_in_SO2': 35.0,     # APT塔入口SO2浓度
        # 'apt_in_LL': 100.0,     # APT塔入口烟气流量
        # 'apt_out_SO2': 15.0,    # APT塔出口SO2浓度
        # 'apt_out_LL': 95.0,     # APT塔出口烟气流量
        'yyq_FC': 100.0,        # 原烟气粉尘浓度
        'jyq_FC': 10.0,        # 净烟气粉尘浓度
        # 一级塔设备电流
        'xstyhfj_ADL': 227.812,
        'aptyhfj_ADL': 219.844,
        'xstjyxhb_ADL': 39.1026,
        'xstjyxhb_BDL': -0.0915753,
        'xstjyxhb_CDL': 46.8559,
        'xstjyxhb_DDL': 46.8559,
        'aptjyxhb_ADL': 0.0305256,
        'aptjyxhb_BDL': 42.7045,
        'aptjyxhb_CDL': -0.0915753,
        # tower_power_consumption计算的总电耗
        'xst_zdh': 350.0,
        'apt_zdh': 280.0
    }
    
    # 计算成本
    result = calculator.calculate_cost(data)
    
    # 打印结果
    print("\n计算结果:")
    print(f"电成本: {result['M_elec_hour']:.2f} 元/h")
    print(f"一级塔石灰石成本: {result['M_stone_primary']:.2f} 元/h")
    print(f"APT塔石灰石成本: {result['M_stone_secondary']:.2f} 元/h")
    print(f"排污成本: {result['M_pollute']:.2f} 元/h")
    print(f"石膏收入: {result['M_gypsum']:.2f} 元/h")
    print(f"总成本: {result['total_cost']:.2f} 元/h")
    
    # 打印设备功率详情（如果需要）
    if 'power_details' in result:
        print("\n设备功率详情:")
        for device, power in result['power_details'].items():
            print(f"{device}: {power:.2f} kW")

if __name__ == "__main__":
    main()