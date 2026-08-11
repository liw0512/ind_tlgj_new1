import traceback

from pymodbus.client import ModbusTcpClient
import csv
import time
import struct


class DCSMaster:
    def __init__(self, host, port, csv_file):
        self.client = ModbusTcpClient(host=host, port=port)
        self.csv_file = csv_file

    def connect(self):
        self.client.connect()

    def disconnect(self):
        self.client.close()

    def float_to_registers(self, value):
        # 将浮点数打包为 4 字节（32 位 IEEE 754 格式）
        packed = struct.pack(">f", value)
        # 拆分为两个 16 位整数
        return list(struct.unpack(">HH", packed))

    def send_data(self):
        with open(self.csv_file, newline="") as csvfile:

            reader = csv.DictReader(csvfile)
            for row in reader:

                try:
                    # 将浮点数编码为 Modbus 寄存器格式
                    data = []
                    for key in row:
                        if key != "time":
                            data.extend(self.float_to_registers(float(row[key])))
                    # 将数据写入 Modbus 寄存器
                    self.client.write_registers(0, data)
                    # print(f"Sent data: {data}")
                except Exception as e:
                    traceback.print_exc()
                finally:
                    time.sleep(1)


# 使用示例
if __name__ == "__main__":
    dcs_master = DCSMaster(
        "localhost", 9000, "/opt/ind_optim_serv/files/all_month.csv"
    )
    dcs_master.connect()
    dcs_master.send_data()
    dcs_master.disconnect()
