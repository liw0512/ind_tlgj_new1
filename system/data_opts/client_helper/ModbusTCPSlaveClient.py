import time
import struct
import traceback
from datetime import datetime

from pymodbus.server import StartTcpServer
from pymodbus.datastore import ModbusSequentialDataBlock
from pymodbus.datastore import ModbusSlaveContext, ModbusServerContext
import threading

class ModbusTCPSlaveClient:

    def __init__(self, host, port, global_data: dict):
        self.host = host
        self.port = port
        self.global_data = global_data

        # 初始化 Modbus 寄存器
        self.store = ModbusSlaveContext(
            di=ModbusSequentialDataBlock(0, [0]*100),
            co=ModbusSequentialDataBlock(0, [0]*100),
            hr=ModbusSequentialDataBlock(0, [0]*100),  # 保持寄存器，用于存储浮点数
            ir=ModbusSequentialDataBlock(0, [0]*100))
        self.context = ModbusServerContext(slaves=self.store, single=True)

    def registers_to_float(self, registers):
        # 将两个 16 位整数组合为 4 字节
        packed = struct.pack('>HH', *registers)
        # 解包为浮点数
        return struct.unpack('>f', packed)[0]

    def start(self):
        self.server = StartTcpServer(context=self.context, address=(self.host, self.port))
        print(f"Slave server started at {self.host}:{self.port}")

    def stop(self):
        self.server.shutdown()
        print("Slave server stopped")

    def run(self):

        server_thread = threading.Thread(target=self.start)
        server_thread.start()

        try:
            while True:

                # 模拟从寄存器中读取数据并解码为浮点数
                registers = self.store.getValues(3, 0, 30)  # 从保持寄存器读取 16 个值（8 个浮点数）
                floats = [self.registers_to_float(registers[i:i+2]) for i in range(0, len(registers), 2)]
                print(f"Received data: {floats}")

                std_data = {
                    "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "jzfh": round(floats[0], 2),

                    # 一级塔参数
                    "yyq_SO2": round(floats[1], 2),
                    "yyq_O2": 0,
                    "yyq_LL": 0,
                    "yyq_WD": 0,
                    "yxt_sgjy_PH1": round(floats[4], 2),
                    "yxt_sgjy_PH2": round(floats[5], 2),
                    "yxt_sgjy_MD": 0,
                    "yst_YW": 0,
                    "jyxhb_DL1": round(floats[11], 2),
                    "jyxhb_DL2": round(floats[12], 2),
                    "jyxhb_DL3": round(floats[13], 2),
                    "jyxhb_DL4": round(floats[14], 2),

                    # 联络烟道
                    "llyd_SO2": 0,

                    # 二级塔参数
                    "jyq_SO2": round(floats[2], 2),
                    "jyq_LL": 0,
                    "xst_sgjy_PH1": round(floats[6], 2),
                    "xst_sgjy_PH2": round(floats[7], 2),
                    "xst_sgjy_MD": 0,
                    "xst_YW": 0,
                    "jyxhb_DL5": round(floats[8], 2),
                    "jyxhb_DL6": round(floats[9], 2),
                    "jyxhb_DL7": round(floats[10], 2)
                }
               #print(f"std_data={std_data}")

                self.global_data["data"].append(std_data)

                time.sleep(1)
        except Exception as e:
            traceback.print_exc()
            self.stop()
            server_thread.join()