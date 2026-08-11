from typing import Dict, Any
import json
import os
import logging

class TowerPowerCalculator:
    def __init__(self, config_path=None):
        """初始化计算器
        
        Args:
            config_path: 配置文件路径，如果为None则使用默认配置
        """
        # 电气参数全局配置
        self.line_voltage = 380  # 线电压默认值修改为380V
        self.power_factor = 0.8  # 功率因数cosφ默认值
        self.motor_efficiency = 0.9  # 电机效率μ默认值
        self.sqrt3 = 1.7312  # √3的值
        
        # 保留原有参数(为了向后兼容)
        self.load_factor = 0.85  # 负荷系数
        self.current_threshold = 20  # 电流阈值
        
        # 设备特定电压配置
        self.voltage_config = {
            "浆液循环泵": 6000,  # 所有浆液循环泵使用6000V
            "氧化风机": {
                "一级塔": 380,   # 一级塔氧化风机使用380V
                "二级塔": 6000   # 二级塔氧化风机使用6000V
            }
        }
        
        # 默认配置 - 现在每个设备都可以单独配置
        self.default_config = {
            "load_factor": 0.85,
            "current_threshold": 20,
            "equipment_config": {
                "浆液循环泵": {
                    "一级塔": {
                        "prefix": "xstjyxhb",
                        "devices": [
                            {"suffix": "A", "rated_power": 315, "efficiency": 0.9},
                            {"suffix": "B", "rated_power": 315, "efficiency": 0.9},
                            {"suffix": "C", "rated_power": 315, "efficiency": 0.9},
                            {"suffix": "D", "rated_power": 315, "efficiency": 0.9}
                        ]
                    },
                    "二级塔": {
                        "prefix": "aptjyxhb",
                        "devices": [
                            {"suffix": "A", "rated_power": 280, "efficiency": 0.88},
                            {"suffix": "B", "rated_power": 280, "efficiency": 0.88},
                            {"suffix": "C", "rated_power": 280, "efficiency": 0.88}
                        ]
                    }
                },
                "氧化风机": {
                    "一级塔": {
                        "prefix": "xstyhfj",
                        "devices": [
                            {"suffix": "A", "rated_power": 450, "efficiency": 0.92},
                            {"suffix": "B", "rated_power": 450, "efficiency": 0.92}
                        ]
                    },
                    "二级塔": {
                        "prefix": "aptyhfj",
                        "devices": [
                            {"suffix": "A", "rated_power": 450, "efficiency": 0.9},
                            {"suffix": "B", "rated_power": 450, "efficiency": 0.9}
                        ]
                    }
                },
                "搅拌器": {
                    "一级塔": {
                        "prefix": "xstjbq",
                        "devices": [
                            {"suffix": "A", "rated_power": 45, "efficiency": 0.85},
                            {"suffix": "B", "rated_power": 45, "efficiency": 0.85},
                            {"suffix": "C", "rated_power": 45, "efficiency": 0.85},
                            {"suffix": "D", "rated_power": 45, "efficiency": 0.85}
                        ]
                    },
                    "二级塔": {
                        "prefix": "aptjbq",
                        "devices": [
                            {"suffix": "A", "rated_power": 45, "efficiency": 0.85},
                            {"suffix": "B", "rated_power": 45, "efficiency": 0.85},
                            {"suffix": "C", "rated_power": 45, "efficiency": 0.85},
                            {"suffix": "D", "rated_power": 45, "efficiency": 0.85}
                        ]
                    }
                },
                "石膏排出泵": {
                    "一级塔": {
                        "prefix": "xstsgpcb",
                        "devices": [
                            {"suffix": "A", "rated_power": 55, "efficiency": 0.87}
                        ]
                    }
                    # "二级塔": {
                    #     "prefix": "aptsgpcb",
                    #     "devices": [
                    #         {}
                    #     ]
                    # }
                },
                "供浆泵": {
                    "一级塔": {
                        "prefix": "xstgjb",
                        "devices": [
                            {"suffix": "A", "rated_power": 75, "efficiency": 0.88}
                              # 不同额定功率和效率的设备
                        ]
                    },
                    "二级塔": {
                        "prefix": "aptgjb",
                        "devices": [
                            {"suffix": "A", "rated_power": 55, "efficiency": 0.87}
                             # 不同额定功率和效率的设备
                        ]
                    }
                },
                "湿磨机": {
                    "一级塔": {
                        "prefix": "xstsmj",
                        "devices": [
                            {"suffix": "A", "rated_power": 400, "efficiency": 0.89}
                        ]
                    },
                    "二级塔": {
                        "prefix": "aptsmj",
                        "devices": [
                            {"suffix": "A", "rated_power": 400, "efficiency": 0.88}
                        ]
                    }
                }
            },
            "output_mapping": {
                "一级塔": {
                    "prefix": "xst",
                    "浆液循环泵": "jyxhb",
                    "氧化风机": "yhfj",
                    "搅拌器": "jbq",
                    "石膏排出泵": "sgpcb",
                    "供浆泵": "gjb",
                    "湿磨机": "smj",
                    "总电耗": "zdh"
                },
                "二级塔": {
                    "prefix": "apt",
                    "浆液循环泵": "jyxhb",
                    "氧化风机": "yhfj",
                    "搅拌器": "jbq",
                    "石膏排出泵": "sgpcb",
                    "供浆泵": "gjb",
                    "湿磨机": "smj",
                    "总电耗": "zdh"
                }
            }
        }
        
        # 加载配置文件
        self.config = self.load_config(config_path)
        
        # 设置参数
        self.load_factor = self.config.get("load_factor", 0.85)
        self.current_threshold = self.config.get("current_threshold", 20)
        self.equipment_config = self.config.get("equipment_config", {})
        self.output_mapping = self.config.get("output_mapping", {})

    def load_config(self, config_path):
        """加载配置文件"""
        if config_path and os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                logging.info(f"成功加载配置文件: {config_path}")
                return config
            except Exception as e:
                logging.error(f"加载配置文件失败: {e}")
                logging.info("使用默认配置")
                return self.default_config
        else:
            logging.info("配置文件不存在，使用默认配置")
            return self.default_config

    def get_equipment_current(self, data: Dict[str, Any], prefix: str, suffix: str) -> float:
        """获取设备电流值，如果不存在则返回0"""
        try:
            field = f"{prefix}_{suffix}DL"
            value = data.get(field, 0)
            return float(value)
        except (ValueError, TypeError):
            return 0.0
    
    def is_equipment_running(self, current: float) -> bool:
        """判断设备是否运行，只要电流大于0就认为设备在运行"""
        return current > 0
    
    def get_device_voltage(self, device_type: str, tower_type: str) -> float:
        """获取设备的线电压
        
        Args:
            device_type: 设备类型，如"浆液循环泵"
            tower_type: 塔类型，如"一级塔"
            
        Returns:
            float: 设备对应的线电压值
        """
        # 如果是浆液循环泵，使用6000V
        if device_type == "浆液循环泵":
            return self.voltage_config["浆液循环泵"]
        
        # 如果是氧化风机，根据塔类型返回不同电压
        if device_type == "氧化风机":
            return self.voltage_config["氧化风机"].get(tower_type, self.line_voltage)
        
        # 其他设备使用默认线电压
        return self.line_voltage
    
    def calculate_device_power(self, current: float, rated_power: float, efficiency: float, device_type: str, tower_type: str) -> float:
        """
        计算设备功率，对于特定设备在没有电流值时使用额定功率
        
        Args:
            current: 电流值(A)
            rated_power: 额定功率(kW)
            efficiency: 电机效率
            device_type: 设备类型
            tower_type: 塔类型
            
        Returns:
            float: 计算得到的功率(kW)
        """
        # 特殊设备列表（这些设备在没有电流值时使用额定功率）
        special_devices = ["搅拌器", "石膏排出泵", "供浆泵","湿磨机"]
        
        # 如果是特殊设备且没有电流值，直接返回额定功率
        if device_type in special_devices and (current <= 0 or current is None):
            return rated_power
        
        # 其他设备或有电流值的情况下使用公式计算
        if current <= 0:
            return 0
        
        # 获取设备对应的线电压
        line_voltage = self.get_device_voltage(device_type, tower_type)
        
        # 使用公式计算功率: P = √3 * U * I * cosφ / μ
        power = self.sqrt3 * line_voltage * current * self.power_factor / efficiency / 1000
        
        return round(power, 2)

    def calculate_equipment_power(self, data: Dict[str, Any], tower_type: str, device_type: str) -> float:
        """计算单类设备的总电耗"""
        if device_type not in self.equipment_config or tower_type not in self.equipment_config[device_type]:
            return 0.0
            
        config = self.equipment_config[device_type][tower_type]
        prefix = config["prefix"]
        devices = config.get("devices", [])
        
        total_power = 0
        
        # 遍历每个配置的设备
        for device in devices:
            suffix = device["suffix"]
            rated_power = device["rated_power"]
            efficiency = device["efficiency"]
            
            current = self.get_equipment_current(data, prefix, suffix)
            # 传入设备类型和塔类型以确定正确的线电压
            power = self.calculate_device_power(current, rated_power, efficiency, device_type, tower_type)
            total_power += power
        
        return round(total_power, 2)

    def calculate_tower_power(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """计算塔的总电耗"""
        result = data.copy()
        
        # 获取配置中的塔类型和设备类型
        tower_types = list(self.output_mapping.keys())
        device_types = []
        for tower in self.output_mapping.values():
            for key in tower.keys():
                if key != "prefix" and key != "总电耗" and key not in device_types:
                    device_types.append(key)
        
        for tower_type in tower_types:
            if tower_type not in self.output_mapping:
                continue
                
            tower_prefix = self.output_mapping[tower_type]["prefix"]
            tower_power = {}
            
            # 计算各类设备电耗
            for device_type in device_types:
                if device_type not in self.output_mapping[tower_type]:
                    continue
                    
                power = self.calculate_equipment_power(data, tower_type, device_type)
                device_code = self.output_mapping[tower_type][device_type]
                tower_power[device_type] = power
                
                # 添加设备电耗字段，如 xstjyxhb_zdh
                result[f"{tower_prefix}{device_code}_zdh"] = power
            
            # 计算塔总电耗
            total_power = sum(tower_power.values())
            result[f"{tower_prefix}_zdh"] = total_power
        
        return result

    def save_default_config(self, path):
        """保存默认配置到文件"""
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self.default_config, f, indent=4, ensure_ascii=False)
            logging.info(f"默认配置已保存到: {path}")
            return True
        except Exception as e:
            logging.error(f"保存默认配置失败: {e}")
            return False


def main():
    # 示例使用
    calculator = TowerPowerCalculator()
    
    # 保存默认配置文件
    calculator.save_default_config("tower_power_config.json")
    
    # 示例数据
    data = {
        'date': '2024-09-01 09:27:34',
        'xstyhfj_ADL': 227.812,
        'aptyhfj_ADL': 219.844,
        'xstjyxhb_ADL': 39.1026,
        'xstjyxhb_BDL': -0.0915753,
        'xstjyxhb_CDL': 46.8559,
        'xstjyxhb_DDL': 46.8559,
        'aptjyxhb_ADL': 0.0305256,
        'aptjyxhb_BDL': 42.7045,
        'aptjyxhb_CDL': -0.0915753,
        # 'xstjbq_ADL': 25.5,
        # 'aptjbq_ADL': 22.8,
        # 'xstsgpcb_ADL': 22.3,
        #'aptsgpcb_ADL': 21.7,
        # 'xstgjb_ADL': 21.5,
        # 'xstgjb_BDL': 30.2,  # 第二个供浆泵
        # 'aptgjb_ADL': 23.2,
        # 'aptgjb_BDL': 25.8,  # 第二个供浆泵
        # 'xstsmj_ADL': 35.6,
        # 'aptsmj_ADL': 32.4
    }
    
    # 计算电耗
    result = calculator.calculate_tower_power(data)
    print(result)
    # 打印结果
    print("\n计算结果:")
    for key, value in result.items():
        if 'zdh' in key:
            print(f"{key}: {value} kW")

if __name__ == "__main__":
    main()