import time
import struct
import traceback
from datetime import datetime
from pymodbus.server import StartSerialServer  # 改为使用串行服务器
from pymodbus.datastore import ModbusSequentialDataBlock
from pymodbus.datastore import ModbusSlaveContext, ModbusServerContext
import threading


class ModbusRTUSlaveClient:
    def __init__(self, port, baudrate, global_data: dict,
                 stopbits=1, bytesize=8, parity='N', timeout=1):
        """
        初始化Modbus RTU Slave
        :param port: 串口路径，如 '/dev/ttyUSB0' 或 'COM3'
        :param baudrate: 波特率，如 9600, 19200, 38400, 57600, 115200
        :param global_data: 全局数据字典
        :param stopbits: 停止位
        :param bytesize: 数据位
        :param parity: 校验位，'N'无校验，'E'偶校验，'O'奇校验
        :param timeout: 超时时间(秒)
        """
        self.port = port
        self.baudrate = baudrate
        self.global_data = global_data
        self.timeout = timeout
        self.stopbits = stopbits
        self.bytesize = bytesize
        self.parity = parity

        # 初始化 Modbus 寄存器
        self.store = ModbusSlaveContext(
            di=ModbusSequentialDataBlock(0, [0] * 100),
            co=ModbusSequentialDataBlock(0, [0] * 100),
            hr=ModbusSequentialDataBlock(0, [0] * 100),  # 保持寄存器，用于存储浮点数
            ir=ModbusSequentialDataBlock(0, [0] * 100))
        self.context = ModbusServerContext(slaves=self.store, single=True)

    def registers_to_float(self, registers):
        """将两个16位寄存器转换为浮点数"""
        packed = struct.pack('>HH', *registers)
        return struct.unpack('>f', packed)[0]

    def start(self):
        """启动RTU服务器"""
        self.server = StartSerialServer(
            context=self.context,
            port=self.port,
            baudrate=self.baudrate,
            timeout=self.timeout,
            stopbits=self.stopbits,
            bytesize=self.bytesize,
            parity=self.parity
        )
        print(f"RTU Slave server started on {self.port} at {self.baudrate} baud")

    def stop(self):
        """停止服务器"""
        if hasattr(self, 'server'):
            self.server.shutdown()
            print("RTU Slave server stopped")

    def run(self):
        """运行服务器和数据处理循环"""
        server_thread = threading.Thread(target=self.start)
        server_thread.daemon = True  # 设置为守护线程
        server_thread.start()

        try:
            while True:
                # 从保持寄存器读取数据并解码为浮点数
                registers = self.store.getValues(3, 0, 30)  # 从保持寄存器读取30个值(15个浮点数)
                floats = [self.registers_to_float(registers[i:i + 2]) for i in range(0, len(registers), 2)]
                print(f"Received data: {floats}")

                # 构建标准数据格式
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
                    "yxt_yhfl": 0,

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
                    "jyxhb_DL7": round(floats[10], 2),
                    "xst_yhfl": 0,
                }
                #print(f"std_data={std_data}")

                self.global_data["data"].append(std_data)
                time.sleep(1)

        except Exception as e:
            traceback.print_exc()
            self.stop()
            server_thread.join()

