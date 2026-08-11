import time
import struct
import traceback
import logging
from datetime import datetime
from tkinter.constants import ROUND
# 从 pymodbus 导入主站客户端相关模块，而不是服务器模块
from pymodbus.client import ModbusTcpClient
import threading



class ModbusTCPClient:

    def __init__(self, host, port, global_data: dict):
        self.host = host
        self.port = port
        self.global_data = global_data
        self.client = None
        self.lock = threading.Lock()  # 添加锁以确保线程安全
        self.previous_values = {}  # 添加缓存字典来存储上一次的有效值
        self.is_connected=False
    def registers_to_float(self, registers):
        # 将两个 16 位整数组合为 4 字节
        packed = struct.pack(">HH", *registers)
        # 解包为浮点数
        return struct.unpack(">f", packed)[0]

    def float_to_registers(self, float_value):
        # 使用struct将浮点数直接打包为IEEE 754格式
        packed = struct.pack(">f", float_value)
        high, low = struct.unpack(">HH", packed)
        return high, low

    # def connect(self):
    #     """建立或重新建立连接"""
    #     with self.lock:
    #         try:
    #             if self.client and self.client.is_socket_open():
    #                 self.client.close()
    #
    #             print(f"尝试连接到服务器 {self.host}:{self.port}...")
    #             self.client = ModbusTcpClient(host=self.host, port=self.port, timeout=5)
    #             connected = self.client.connect()
    #
    #             if connected:
    #                 print(f"主站客户端已成功连接到服务器 {self.host}:{self.port}")
    #             else:
    #                 print(f"连接服务器失败 {self.host}:{self.port}")
    #             return connected
    #         except Exception as e:
    #             print(f"连接过程中发生错误: {e}")
    #             traceback.print_exc()
    #             self.client = None
    #             return False
    def connect(self):
        """建立或重新建立连接"""
        with self.lock:
            try:
                if self.client and self.client.is_socket_open():
                    self.client.close()

                print(f"尝试连接到服务器 {self.host}:{self.port}...")
                self.client = ModbusTcpClient(host=self.host, port=self.port, timeout=5)
                connected = self.client.connect()
                # 更新连接状态并保存到global_data
                self.is_connected = connected
                self.global_data["connection_status"] = connected

                if connected:
                    print(f"主站客户端已成功连接到服务器 {self.host}:{self.port}")
                else:
                    print(f"连接服务器失败 {self.host}:{self.port}")
                return connected
            except Exception as e:
                print(f"连接过程中发生错误: {e}")
                traceback.print_exc()
                self.client = None
                self.is_connected = False
                self.global_data["connection_status"] = False
                return False
    def start(self):
        self.connect()

    def stop(self):
        if self.client and self.client.is_socket_open():
            self.client.close()
            print("主站客户端已断开连接")

    def read_dcs(self):
        try:
            if not self.client or not self.client.is_socket_open():
                print("读取DCS错误：客户端未连接。")
                return None

            # 定义多个地址区段
            address_ranges = [
                {"start": 101, "count": 24},  # DPU2-G3-150 区段
                {"start": 125, "count": 22},  # DPU2-G3-174 区段
                {"start": 147, "count": 40}   # DPU2-G3-196 区段
            ]
            
            all_registers = []
            
            # 对每个区段进行读取
            for range_info in address_ranges:
                start_address = range_info["start"]
                count = range_info["count"]
                
                # 每次最多读取20个寄存器
                chunks = [(start_address + i, min(20, count - i)) 
                        for i in range(0, count, 20)]
                
                for chunk_start, chunk_size in chunks:
                    response = self.client.read_holding_registers(
                        address=chunk_start, count=chunk_size, slave=1
                    )
                    
                    if not response.isError():
                        all_registers.extend(response.registers)
                    else:
                        print(f"Error reading registers at address {chunk_start}: {response}")
                        return None
            
            # 转换为浮点数 - 假设每两个寄存器代表一个浮点数
            floats = []
            for i in range(0, len(all_registers), 2):
                if i + 1 < len(all_registers):
                    floats.append(self.registers_to_float(all_registers[i:i+2]))
                else:
                    # 处理奇数个寄存器的情况
                    floats.append(0.0)
            
            # 增加数据长度校验
            if len(floats) < 40:
                print(f"接收到的数据长度不足 (expected >= 40, got {len(floats)})，可能导致索引错误。")
                return None
                    
            print(f"接收到数据: {floats}")
            return floats
        except Exception as e:
            print(f"读取DCS寄存器时发生错误: {str(e)}")
            traceback.print_exc()
            # 发生异常时，认为连接可能已断开
            if self.client:
                self.client.close()
            return None

    def safe_get(self, dictionary, key, default=0):
        """
        安全获取值，处理异常值和突变值

        Args:
            dictionary: 要获取值的字典
            key: 字典的键
            default: 默认值

        Returns:
            处理后的值
        """
        value = dictionary.get(key, None)
        prev_value = self.previous_values.get(key, None)

        # 检查值是否存在且类型正确
        if value is None or not isinstance(value, (int, float)):
            return prev_value if prev_value is not None else default

        # 检查是否为负值（大部分工业数据应为非负）
        if value < 0:
            return prev_value if prev_value is not None else default

        # 获取突变计数器，如果不存在则初始化为0
        anomaly_count = self.previous_values.get(f"{key}_anomaly_count", 0)

        # 检查是否为零值但之前不为零（可能是突变）
        if value == 0 and prev_value is not None and prev_value > 0:
            # 可以增加日志记录
            logging.warning(f"检测到{key}突变为0，使用上一次有效值: {prev_value}")
            return prev_value

        # 检查突变（如果前一个值存在）
        if prev_value is not None:
            # 根据不同参数设置不同的变化阈值
            if key in ["jyq_SO2", "yyq_SO2", "llyd_SO2"]:
                change_threshold = 2.0  # SO2允许更大波动
            else:
                change_threshold = 0.5  # 默认50%

            # 如果变化超过阈值且不是从0变为非0
            if prev_value > 0 and abs(value - prev_value) / prev_value > change_threshold:
                anomaly_count += 1
                self.previous_values[f"{key}_anomaly_count"] = anomaly_count

                # 如果连续3次出现类似突变，则接受新值作为真实变化
                if anomaly_count >= 3:
                    logging.warning(f"{key}连续{anomaly_count}次出现大幅变化，接受新值: {value}")
                    self.previous_values[key] = value
                    self.previous_values[f"{key}_anomaly_count"] = 0
                    return value
                else:
                    logging.warning(
                        f"检测到{key}突变: {prev_value} -> {value}，使用上一次有效值 (异常计数: {anomaly_count}/3)")
                    return prev_value
            else:
                # 正常值，重置异常计数
                if anomaly_count > 0:
                    self.previous_values[f"{key}_anomaly_count"] = 0

        # 记录当前有效值
        self.previous_values[key] = value
        return value
    def run(self):
        self.start()
        while True:
            try:
                if self.client and self.client.is_socket_open():
                    # 从服务器读取寄存器
                    floats = self.read_dcs()
                    if floats is None:
                        # read_dcs 内部已处理错误，这里等待一下，然后重试
                        time.sleep(2)
                        continue
                        
                    # 根据截图中的映射关系创建数据字典
                    # 以下映射是基于截图中的序号和描述
                    # std_data = {
                    #     "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    #     "jzfh": round(floats[0], 2),          # 1号: #1机组负荷 (150)
                    #     "yyq_SO2": round(floats[1], 2),       # 2号: #1一级塔原烟气SO2含量 (151)
                    #     "yyq_O2": round(floats[2], 2),        # 3号: #1一级塔原烟气O2含量 (152)
                    #     "yyq_LL": round(floats[3], 2),        # 4号: #1一级塔原烟气流量 (153)
                    #     "yyq_WD": round(floats[4], 2),        # 5号: #1一级塔原烟气温度 (154)
                    #     # 注意: 序号6空缺 (155)
                    #     "xstjy_PH1": round(floats[6], 2),     # 7号: #1一级塔石膏浆液PH值1 (156)
                    #     "xstjy_PH2": round(floats[7], 2),     # 8号: #1一级塔石膏浆液PH值2 (157)
                    #     "xstshsjy_MD": round(floats[8], 2),   # 9号: #1一级塔石膏浆液密度 (158)
                    #     "xst_YW": round(floats[9], 2),        # 10号: #1一级塔吸收液位 (159)
                    #     "xstjyxhb_ADL": round(floats[10], 2), # 11号: #1一级塔1A浆液循环泵电流 (160)
                    #     "xstjyxhb_BDL": round(floats[11], 2), # 12号: #1一级塔1B浆液循环泵电流 (161)
                    #     "xstjyxhb_CDL": round(floats[12], 2), # 13号: #1一级塔1C浆液循环泵电流 (162)
                    #     "xstjyxhb_DDL": round(floats[13], 2), # 14号: #1一级塔1D浆液循环泵电流 (163)
                    #     "xstyhfj_ADL": round(floats[15], 2),  # 15号: #1一级塔氧化风量 (164-165)
                    #     "xstyhfj_BDL": round(floats[16], 2),  # 16号: #1一级塔氧化风机A电流 (166)
                        
                    #     # 从第17项开始 (DPU2-G3-174区域)
                    #     "llyd_SO2": round(floats[18], 2),     # 7号: #1机联络烟道SO2 (174+6)
                    #     "jyq_SO2": round(floats[19], 2),      # 8号: #1二级塔净烟气SO2 (174+7)
                    #     "jyq_LL": round(floats[20], 2),       # 9号: #1二级塔净烟气流量 (174+8)
                    #     "aptjy_PH1": round(floats[21], 2),    # 10号: #1二级塔石膏浆液PH值1 (174+9)
                    #     "aptjy_PH2": round(floats[22], 2),    # 11号: #1二级塔石膏浆液PH值2 (174+10)
                    #     "aptshsjy_MD": round(floats[23], 2),  # 12号: #1二级塔石膏浆液密度 (174+11)
                    #     "apt_YW": round(floats[24], 2),       # 1号: #1二级塔吸收塔液位 (196)
                    #     "aptjyxhb_ADL": round(floats[25], 2), # 2号: #1二级塔1E浆液循环泵电流 (196+1)
                    #     "aptjyxhb_BDL": round(floats[26], 2), # 3号: #1二级塔2F浆液循环泵电流 (196+2)
                    #     "aptjyxhb_CDL": round(floats[27], 2), # 4号: #1二级塔2G浆液循环泵电流 (196+3)
                    #     "aptyhfj_ADL": round(floats[29], 2),  # 6号: #1二级塔氧化风机C电流 (196+5)
                    #     "aptyhfj_BDL": round(floats[30], 2),  # 7号: #1二级塔氧化风机D电流 (196+6)
                        
                    #     # 从后面的区域
                    #     "xstylsy_ND": round(floats[32], 2),   # 9号: #吸收塔亚硫酸盐浓度 (196+8)
                    #     "zml": round(floats[33], 2),          # 10号: #1机总煤量 (196+9)
                    #     "yyq_FC": round(floats[34], 2),       # 11号: #1机原烟气粉尘浓度 (196+10)
                    #     "jyq_FC": round(floats[35], 2),       # 12号: #1机净烟气粉尘浓度 (196+11)
                    #     "xstshsjy_LL": round(floats[36], 2),  # 13号: #1一级塔石灰石浆液流量 (196+12)
                    #     "xstgjb_ADL": round(floats[37], 2),   # 14号: #1一级塔供浆泵电流 (196+13)
                    #     "aptshsjy_LL": round(floats[38], 2),  # 15号: #1二级塔石灰石浆液流量 (196+14)
                    #     "aptgjb_ADL": round(floats[39], 2),   # 16号: #2二级塔供浆泵电流 (196+15)
                    # }
                    
                    # print(f"-------获取到dcs数据 std_data={std_data}-----")
                    # self.global_data["data"].append(std_data)
                    # 先创建临时字典存储原始值
                    temp_dict = {
                        "jzfh": floats[0],                # 1号: #1机组负荷 (150)
                        "yyq_SO2": floats[1],             # 2号: #1一级塔原烟气SO2含量 (151)
                        "yyq_O2": floats[2],              # 3号: #1一级塔原烟气O2含量 (152)
                        "yyq_LL": floats[3],              # 4号: #1一级塔原烟气流量 (153)
                        "yyq_WD": floats[4],              # 5号: #1一级塔原烟气温度 (154)
                        "glfl":floats[5],
                        "xstjy_PH": floats[6],           # 7号: #1一级塔石膏浆液PH值1 (156)
                        # "xstjy_PH2": floats[7],           # 8号: #1一级塔石膏浆液PH值2 (157)
                        "xstshsjy_MD": floats[7],         # 9号: #1一级塔石膏浆液密度 (158)
                        "xst_YW": floats[9],              # 10号: #1一级塔吸收液位 (159)
                        "xstjyxhb_ADL": floats[10],       # 11号: #1一级塔1A浆液循环泵电流 (160)
                        "xstjyxhb_BDL": floats[11],       # 12号: #1一级塔1B浆液循环泵电流 (161)
                        "xstjyxhb_CDL": floats[12],       # 13号: #1一级塔1C浆液循环泵电流 (162)
                        "xstjyxhb_DDL": floats[13],       # 14号: #1一级塔1D浆液循环泵电流 (163)
                        "xstyhfj_ADL": floats[15],        # 15号: #1一级塔氧化风量 (164-165)
                        "xstyhfj_BDL": floats[16],        # 16号: #1一级塔氧化风机A电流 (166)
                        
                        # 从第17项开始 (DPU2-G3-174区域)
                        "llyd_SO2": floats[18],           # 7号: #1机联络烟道SO2 (174+6)
                        "jyq_SO2": floats[19],            # 8号: #1二级塔净烟气SO2 (174+7)
                        "jyq_LL": floats[20],             # 9号: #1二级塔净烟气流量 (174+8)
                        "aptjy_PH": floats[21],          # 10号: #1二级塔石膏浆液PH值1 (174+9)
                        # "aptjy_PH2": floats[22],          # 11号: #1二级塔石膏浆液PH值2 (174+10)
                        "aptshsjy_MD": floats[22],        # 12号: #1二级塔石膏浆液密度 (174+11)
                        "apt_YW": floats[24],             # 1号: #1二级塔吸收塔液位 (196)
                        "aptjyxhb_ADL": floats[25],       # 2号: #1二级塔1E浆液循环泵电流 (196+1)
                        "aptjyxhb_BDL": floats[26],       # 3号: #1二级塔2F浆液循环泵电流 (196+2)
                        "aptjyxhb_CDL": floats[27],       # 4号: #1二级塔2G浆液循环泵电流 (196+3)
                        "aptyhfj_ADL": floats[29],        # 6号: #1二级塔氧化风机C电流 (196+5)
                        "aptyhfj_BDL": floats[30],        # 7号: #1二级塔氧化风机D电流 (196+6)
                        
                        # 从后面的区域
                        "xstylsy_ND": floats[32],         # 9号: #吸收塔亚硫酸盐浓度 (196+8)
                        "zml": floats[33],                # 10号: #1机总煤量 (196+9)
                        "yyq_FC": floats[34],             # 11号: #1机原烟气粉尘浓度 (196+10)
                        "jyq_FC": floats[35],             # 12号: #1机净烟气粉尘浓度 (196+11)
                        "xstshsjy_LL": floats[36],        # 13号: #1一级塔石灰石浆液流量 (196+12)
                        "apt_FMKD": floats[37],         # 14号: #1一级塔供浆泵电流 (196+13)
                        "aptshsjy_LL": floats[38],        # 15号: #1二级塔石灰石浆液流量 (196+14)
                        "xst_FMKD": floats[39],         # 16号: #2二级塔供浆泵电流 (196+15)
                        "jym":floats[41],
                    }

                    # 使用safe_get方法处理每个值
                    std_data = {
                        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "jzfh": round(self.safe_get(temp_dict, "jzfh"), 2),               # 1号: #1机组负荷 (150)
                        "yyq_SO2": round(self.safe_get(temp_dict, "yyq_SO2"), 2),         # 2号: #1一级塔原烟气SO2含量 (151)
                        "yyq_O2": round(self.safe_get(temp_dict, "yyq_O2"), 2),           # 3号: #1一级塔原烟气O2含量 (152)
                        "yyq_LL": round(self.safe_get(temp_dict, "yyq_LL"), 2),           # 4号: #1一级塔原烟气流量 (153)
                        "yyq_WD": round(self.safe_get(temp_dict, "yyq_WD"), 2),           # 5号: #1一级塔原烟气温度 (154)
                        # 注意: 序号6空缺 (155)
                        "glfl": round(self.safe_get(temp_dict, "glfl"), 2),
                        "xstjy_PH": round(self.safe_get(temp_dict, "xstjy_PH"), 2),     # 7号: #1一级塔石膏浆液PH值1 (156)
                        # "xstjy_PH2": round(self.safe_get(temp_dict, "xstjy_PH2"), 2),     # 8号: #1一级塔石膏浆液PH值2 (157)
                        "xstshsjy_MD": round(self.safe_get(temp_dict, "xstshsjy_MD"), 2), # 9号: #1一级塔石膏浆液密度 (158)
                        "xst_YW": round(self.safe_get(temp_dict, "xst_YW"), 2),           # 10号: #1一级塔吸收液位 (159)
                        "xstjyxhb_ADL": round(temp_dict.get("xstjyxhb_ADL",0), 2), # 11号: #1一级塔1A浆液循环泵电流 (160)
                        "xstjyxhb_BDL": round(temp_dict.get("xstjyxhb_BDL",0), 2), # 12号: #1一级塔1B浆液循环泵电流 (161)
                        "xstjyxhb_CDL": round(temp_dict.get("xstjyxhb_CDL",0), 2), # 13号: #1一级塔1C浆液循环泵电流 (162)
                        "xstjyxhb_DDL": round(temp_dict.get("xstjyxhb_DDL",0), 2), # 14号: #1一级塔1D浆液循环泵电流 (163)
                        "xstyhfj_ADL": round(temp_dict.get("xstyhfj_ADL",0), 2),   # 15号: #1一级塔氧化风量 (164-165)
                        "xstyhfj_BDL": round(temp_dict.get("xstyhfj_BDL",0), 2),   # 16号: #1一级塔氧化风机A电流 (166)
                        
                        # 从第17项开始 (DPU2-G3-174区域)
                        "llyd_SO2": round(self.safe_get(temp_dict, "llyd_SO2"), 2),        # 7号: #1机联络烟道SO2 (174+6)
                        "jyq_SO2": round(self.safe_get(temp_dict, "jyq_SO2"), 2),          # 8号: #1二级塔净烟气SO2 (174+7)
                        "jyq_LL": round(self.safe_get(temp_dict, "jyq_LL"), 2),            # 9号: #1二级塔净烟气流量 (174+8)
                        "aptjy_PH": round(self.safe_get(temp_dict, "aptjy_PH"), 2),      # 10号: #1二级塔石膏浆液PH值1 (174+9)
                        # "aptjy_PH2": round(self.safe_get(temp_dict, "aptjy_PH2"), 2),      # 11号: #1二级塔石膏浆液PH值2 (174+10)
                        "aptshsjy_MD": round(self.safe_get(temp_dict, "aptshsjy_MD"), 2),  # 12号: #1二级塔石膏浆液密度 (174+11)
                        "apt_YW": round(self.safe_get(temp_dict, "apt_YW"), 2),            # 1号: #1二级塔吸收塔液位 (196)
                        "aptjyxhb_ADL": round(temp_dict.get("aptjyxhb_ADL",0), 2), # 2号: #1二级塔1E浆液循环泵电流 (196+1)
                        "aptjyxhb_BDL": round(temp_dict.get("aptjyxhb_BDL",0), 2), # 3号: #1二级塔2F浆液循环泵电流 (196+2)
                        "aptjyxhb_CDL": round(temp_dict.get("aptjyxhb_CDL",0), 2), # 4号: #1二级塔2G浆液循环泵电流 (196+3)
                        "aptyhfj_ADL": round(temp_dict.get("aptyhfj_ADL",0), 2),   # 6号: #1二级塔氧化风机C电流 (196+5)
                        "aptyhfj_BDL": round(temp_dict.get("aptyhfj_BDL",0), 2),   # 7号: #1二级塔氧化风机D电流 (196+6)
                        
                        # 从后面的区域
                        "xstylsy_ND": round(self.safe_get(temp_dict, "xstylsy_ND"), 2),     # 9号: #吸收塔亚硫酸盐浓度 (196+8)
                        "zml": round(self.safe_get(temp_dict, "zml"), 2),                    # 10号: #1机总煤量 (196+9)
                        "yyq_FC": round(temp_dict.get("yyq_FC",0), 2),              # 11号: #1机原烟气粉尘浓度 (196+10)
                        "jyq_FC": round(temp_dict.get("jyq_FC",0), 2),              # 12号: #1机净烟气粉尘浓度 (196+11)
                        "xstshsjy_LL": round(temp_dict.get("xstshsjy_LL",0), 2),    # 13号: #1一级塔石灰石浆液流量 (196+12)
                        "apt_FMKD": round(temp_dict.get("apt_FMKD",0), 2),      # 14号: #1一级塔供浆泵电流 (196+13)
                        "aptshsjy_LL": round(temp_dict.get("aptshsjy_LL",0), 2),    # 15号: #1二级塔石灰石浆液流量 (196+14)
                        "xst_FMKD": round(temp_dict.get("xst_FMKD",0), 2),      # 16号: #2二级塔供浆泵电流 (196+15)
                        "jym":round(temp_dict.get("jym"),0)
                    }

                    print(f"-------获取到dcs数据 std_data={std_data}-----")
                    self.global_data["data"].append(std_data)
                else:
                    print("客户端未连接，将在5秒后尝试重连...")
                    time.sleep(5)
                    self.connect()
                time.sleep(1)
            except IndexError as ie:
                print(f"数据处理时发生索引错误: {ie}。可能是由于读取到的数据长度不一致。")
                traceback.print_exc()
                time.sleep(1) # 稍作等待后继续
            except Exception as e:
                print(f"数据读取/处理主循环发生未知错误: {str(e)}")
                traceback.print_exc()
                # 发生任何其他错误都尝试重连
                if self.client:
                    self.client.close()
                time.sleep(5)
                self.connect()

    def send_heart(self):
        heart_address = 425  # 修改为新的心跳地址824
        heart_value = 1.0    # 固定为浮点数1.0
        while True:
            try:
                if self.client and self.client.is_socket_open():
                    # 将浮点数1.0转换为两个寄存器值
                    high, low = self.float_to_registers(heart_value)
                    # 写入两个连续的寄存器，表示一个浮点数
                    response = self.client.write_registers(heart_address, values=[high, low], slave=1)
                    if response.isError():
                         logging.error(f"心跳数据写入失败: {response}")
                         # 写入失败可能意味着连接有问题，尝试重连
                         self.connect()
                    else:
                        logging.info(f"==========心跳数据[: {heart_value}]发送到地址{heart_address}===========")
                    
                    heart_value = 2.0 if heart_value == 1.0 else 1.0
                else:
                    print("心跳线程：客户端未连接，将在5秒后尝试重连...")
                    time.sleep(5)
                    self.connect()
            except Exception as e:
                logging.error(f"心跳数据发送失败: {str(e)}")
                traceback.print_exc()
                if self.client:
                    self.client.close()
            finally:
                time.sleep(5)

    def send_cnn_to_dcs(self):
        while True:
            try:
                logging.info("-------写入寄存器---------")
                map_control_result = self.global_data.get("map_control")

                if map_control_result is not None and self.client and self.client.is_socket_open():
                    # 添加一个辅助函数来处理None值
                    # 增强局部safe_get函数，保持函数名不变
                    def safe_get(dictionary, key, default=0):
                        """从字典中获取值，处理None和0值"""
                        value = dictionary.get(key, None)
                        # 简单处理None和0值情况
                        if value is None or value == 0:
                            # 在局部函数中也可以访问类的属性
                            prev_value = self.previous_values.get(f"write_{key}", None)
                            if prev_value is not None:
                                return prev_value
                            return default
                        # 记录有效值用于下次调用
                        self.previous_values[f"write_{key}"] = value
                        return value

                    def safe_get_for_yhfl_jzgj(dictionary, key, default=0):
                        """从字典中获取值，处理None和0值，并限制变化斜率，但允许连续多次超限后接受变化"""
                        value = dictionary.get(key, None)
                        # 获取上一次的有效值
                        prev_value = self.previous_values.get(f"write_{key}", None)

                        # 获取连续超限计数
                        exceed_count_key = f"write_{key}_exceed_count"
                        exceed_count = self.previous_values.get(exceed_count_key, 0)

                        # 简单处理None情况
                        if value is None:
                            # 重置超限计数
                            self.previous_values[exceed_count_key] = 0
                            return prev_value if prev_value is not None else default

                        # 如果有前一个值，则计算并限制变化率
                        if prev_value is not None:
                            # 针对不同参数使用不同的变化限制
                            if "base_flow" in key or "fan_flow" in key:
                                # 根据参数类型选择合适的变化限制方式
                                if key == "xst_base_flow":  # 一级塔基准供浆量 (10-20)
                                    max_change_rate = 0.05  # 15%的变化率
                                    max_change = prev_value * max_change_rate
                                elif key == "apt_base_flow":  # 二级塔基准供浆量 (0-3)
                                    max_change_rate = 0.15  # 稍微提高变化率
                                    # 同时设置最小变化量，避免数值小时变化过于缓慢
                                    max_change = max(prev_value * max_change_rate, 0.4)
                                else:
                                    # 其他风机流量等参数仍使用15%变化率
                                    max_change_rate = 0.15
                                    max_change = prev_value * max_change_rate

                                actual_change = value - prev_value

                                # 检查变化量是否超过限制
                                if abs(actual_change) > max_change:
                                    # 增加超限计数
                                    exceed_count += 1
                                    self.previous_values[exceed_count_key] = exceed_count

                                    # 如果连续超限次数达到阈值，接受新值
                                    if exceed_count > 3:  # 连续3次超限则接受真实变化
                                        logging.warning(f"{key}连续{exceed_count}次变化超限，接受真实值: {value}")
                                        self.previous_values[exceed_count_key] = 0  # 重置计数器
                                    else:
                                        # 否则仍然限制变化率
                                        direction = 1 if actual_change > 0 else -1
                                        value = prev_value + (direction * max_change)
                                        logging.warning(
                                            f"{key}变化过快: {prev_value} -> {dictionary.get(key)}, 平滑调整为: {value} (超限计数: {exceed_count}/3)")
                                else:
                                    # 变化在允许范围内，重置计数器
                                    self.previous_values[exceed_count_key] = 0

                        # 记录有效值用于下次调用
                        if value != 0:  # 只有非零值才记录
                            self.previous_values[f"write_{key}"] = value

                        return value
                    float_params_group1 = [
                        ("一级塔PH值建议", safe_get(map_control_result, "suggested_xst_ph")),
                        ("二级塔PH值建议", safe_get(map_control_result, "suggested_apt_ph")),
                        ("一级塔基准供浆量", safe_get_for_yhfl_jzgj(map_control_result, "xst_base_flow")),
                        ("二级塔泡沫高度", 0),
                        ("一级塔理论氧化风机量", safe_get_for_yhfl_jzgj(map_control_result, "xst_fan_flow_mode1")),
                        ("二级塔理论氧化风机量", safe_get_for_yhfl_jzgj(map_control_result, "apt_fan_flow_mode1")),
                        ("二级塔基准供浆量", safe_get_for_yhfl_jzgj(map_control_result, "apt_base_flow")),
                        # ("SO2实时排放", safe_get(map_control_result, "M0")),
                        # ("SO2日排放", safe_get(map_control_result, "M1_daily")),
                        # ("SO2月排放", safe_get(map_control_result, "M1_monthly")),
                        # ("一级塔理论氧化风机量(模式1)", 0),
                        # ("一级塔理论氧化风机量(模式2)", 0),
                        # ("二级塔理论氧化风机量(模式1)", 0),
                        # ("二级塔理论氧化风机量(模式2)", 0),
                        # ("一级塔浆液循环泵总电耗", safe_get(map_control_result, "xstjyxhb_zdh")),
                        # ("一级塔氧化风机总电耗", safe_get(map_control_result, "xstyhfj_zdh")),
                        # ("一级塔搅拌器总电耗", safe_get(map_control_result, "xstjbq_zdh")),
                        # ("一级塔石膏排出泵", safe_get(map_control_result, "xstsgpcb_zdh")),
                        # ("一级塔供浆泵总电耗", safe_get(map_control_result, "xstgjb_zdh")),
                        # ("一级塔湿磨机总电耗", safe_get(map_control_result, "xstsmj_zdh")),
                        # ("一级塔总电耗", safe_get(map_control_result, "xst_zdh")),
                        # ("二级塔浆液循环泵总电耗", safe_get(map_control_result, "aptjyxhb_zdh")),
                        # ("二级塔氧化风机总电耗", safe_get(map_control_result, "aptyhfj_zdh")),
                        # ("二级塔石膏排出泵", safe_get(map_control_result, "aptsgpcb_zdh")),
                        # ("二级塔搅拌器总电耗", safe_get(map_control_result, "aptjbq_zdh")),
                        # ("二级塔供浆泵总电耗", safe_get(map_control_result, "aptgjb_zdh")),
                        # ("二级塔湿磨机总电耗", safe_get(map_control_result, "aptsmj_zdh")),
                        # ("二级塔总电耗", safe_get(map_control_result, "apt_zdh")),
                        # ("小时电成本", safe_get(map_control_result, "M_elec_hour")),
                        # ("脱硫小时总成本", safe_get(map_control_result, "total_cost")),
                    ]

                    # 第二组浮点数参数 - DPU2-G3-798 (从序号2，地址800开始)
                    # float_params_group2 = [
                    #     ("一级塔石灰石成本", safe_get(map_control_result, "M_stone_primary")),  # 序号2
                    #     ("二级塔石灰石成本", safe_get(map_control_result, "M_stone_secondary")),  # 序号3
                    #     ("脱硫排污成本", safe_get(map_control_result, "M_pollute")),  # 序号4
                    #     ("石膏销售成本", safe_get(map_control_result, "M_gypsum")),  # 序号5
                    #     ("脱硫总成本", safe_get(map_control_result, "total_cost")),  # 序号6
                    # ]
                    # 写入第一组浮点数参数 (DPU2-G3-598)
                    address = 301
                    for param_name, value in float_params_group1:
                        high, low = self.float_to_registers(float(value))
                        response = self.client.write_registers(address, values=[high, low], slave=1)
                        if response.isError():
                            logging.error(f"写入 {param_name} 到地址 {address} 失败: {response}")
                            break
                        logging.info(f"写入 {param_name}={value} 到地址 {address}")
                        address += 2
                    else:
                        # 写入第二组浮点数参数 (DPU2-G3-798)
                        # address = 411
                        # for param_name, value in float_params_group2:
                        #     high, low = self.float_to_registers(float(value))
                        #     response = self.client.write_registers(address, values=[high, low], slave=1)
                        #     if response.isError():
                        #         logging.error(f"写入 {param_name} 到地址 {address} 失败: {response}")
                        #         break
                        #     logging.info(f"写入 {param_name}={value} 到地址 {address}")
                        #     address += 2
                        # else:
                        #      # 写入泵状态建议 (从序号7开始)
                        try:
                            pump_status_advice_str = map_control_result.get("recommended_pump")
                            if pump_status_advice_str is None:
                                pump_status_advice_str = map_control_result.get("combined_pump_status")
                            if pump_status_advice_str is None:
                                pump_status_advice_str = "0-0-0-0-0-0-0"

                            pump_status_advice = [int(s) for s in pump_status_advice_str.split("-")]

                            pump_address = 411
                            for i, p_value in enumerate(pump_status_advice):
                                float_value = float(p_value)
                                high, low = self.float_to_registers(float_value)
                                response = self.client.write_registers(pump_address + i*2, values=[high, low], slave=1)
                                if response.isError():
                                    logging.error(f"写入泵{i+1}建议失败: {response}")
                                    break
                                logging.info(f"写入泵{i+1}建议={float_value} 到地址 {pump_address + i*2}")
                        except ValueError as e:
                            logging.error(f"泵组合数据格式错误: {pump_status_advice_str}, {e}")
                        except Exception as e:
                            logging.error(f"处理泵状态建议时出错: {str(e)}")
                            traceback.print_exc()

                        logging.info("成功写入寄存器")
                elif map_control_result is None:
                    logging.debug("map_control中无数据，跳过写入")
                else:
                    print("写入线程：客户端未连接，将在5秒后尝试重连...")
                    time.sleep(5)
                    self.connect()

            except Exception as e:
                logging.error(f"向dcs写数据出现错误: {str(e)}")
                traceback.print_exc()
                if self.client:
                    self.client.close()
            finally:
                time.sleep(1)