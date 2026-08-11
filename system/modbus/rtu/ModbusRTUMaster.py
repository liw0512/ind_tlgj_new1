import traceback
from pymodbus.client import ModbusSerialClient
import csv
import time
import struct


class DCSRTUMaster:
    def __init__(
        self, port, baudrate, csv_file, timeout=1, stopbits=1, bytesize=8, parity="N"
    ):
        """
        初始化Modbus RTU Master
        :param port: 串口路径，如 '/dev/ttyUSB0' 或 'COM3'
        :param baudrate: 波特率，如 9600, 19200, 38400, 57600, 115200
        :param csv_file: 数据文件路径
        :param timeout: 超时时间(秒)
        :param stopbits: 停止位
        :param bytesize: 数据位
        :param parity: 校验位，'N'无校验，'E'偶校验，'O'奇校验
        """
        self.client = ModbusSerialClient(
            port=port,
            baudrate=baudrate,
            timeout=timeout,
            stopbits=stopbits,
            bytesize=bytesize,
            parity=parity,
        )
        self.csv_file = csv_file

    def connect(self):
        """连接到RTU设备"""
        return self.client.connect()

    def disconnect(self):
        """断开连接"""
        self.client.close()

    def float_to_registers(self, value):
        """将浮点数转换为两个16位寄存器"""
        packed = struct.pack(">f", value)
        return list(struct.unpack(">HH", packed))

    def send_data(self, slave_id=1, start_address=0):
        """
        发送数据到RTU设备
        :param slave_id: 从站ID
        :param start_address: 起始寄存器地址
        """
        while True:

            with open(self.csv_file, newline="") as csvfile:
                reader = csv.DictReader(csvfile)
                for row in reader:
                    try:
                        # 将浮点数编码为Modbus寄存器格式
                        data = []
                        for key in row:
                            if key != "time":
                                data.extend(self.float_to_registers(float(row[key])))

                        # 将数据写入Modbus寄存器 - 使用slave参数替代unit
                        response = self.client.write_registers(
                            address=start_address,
                            values=data,
                            slave=slave_id,  # 修改为使用slave参数
                        )

                        # if response.isError():
                        #     print(f"Error writing registers: {response}")
                        # else:
                        #     print(f"Sent data to slave {slave_id}: {data}")

                    except Exception as e:
                        traceback.print_exc()
                    finally:
                        time.sleep(1)  # 每次发送间隔1秒


# 使用示例
if __name__ == "__main__":
    # 修改为你的串口参数
    rtu_master = DCSRTUMaster(
        port="/dev/pts/2",  # 或 'COM3' (Windows)
        baudrate=19200,
        csv_file="/opt/ind_optim_serv/files/all_month.csv",
    )

    if rtu_master.connect():
        try:
            rtu_master.send_data(slave_id=1)  # 指定从站ID
        finally:
            rtu_master.disconnect()
    else:
        print("Failed to connect to RTU device")
