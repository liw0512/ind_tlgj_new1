import csv
import time
import struct
import traceback
from datetime import datetime
from pymodbus.server import StartSerialServer  # 改为使用串行服务器
from pymodbus.datastore import ModbusSequentialDataBlock
from pymodbus.datastore import ModbusSlaveContext, ModbusServerContext
import threading


class MokeSlaveClient:
    def __init__(self, global_data):
        self.global_data = global_data
        self._last_values = {}
        # consume_data 和前端读取的是顶层 GLOBAL_DATA 状态。
        self.global_data["connection_status"] = True

    def _to_float(self, key, value):
        if value is None:
            return self._last_values.get(key, 0.0)

        text = str(value).strip()
        if text == "" or text.lower() in {"nan", "none", "null"}:
            return self._last_values.get(key, 0.0)

        try:
            result = float(text)
        except ValueError:
            return self._last_values.get(key, 0.0)

        self._last_values[key] = result
        return result

    def run(self):

        try:
            self.global_data["connection_status"] = True

            with open(
                r"F:\xiregangchang\ind_optim_serv_xire\files\selected_30s_processed.csv",
                newline="",
            ) as csvfile:

                reader = csv.DictReader(csvfile)
                # 跳过前5万行
                for _ in range(1):
                    try:
                        next(reader)
                    except StopIteration:
                        print("文件行数少于5万行，将从头开始读取")
                        # 如果文件行数不足，重新打开文件
                        csvfile.seek(0)
                        reader = csv.DictReader(csvfile)
                        break

                print("开始从第5万条数据读取...")
                for row in reader:

                    try:
                        # 将浮点数编码为 Modbus 寄存器格式
                        floats = []
                        for key in row:
                            # print(f"key={key}, row={row}")
                            if key is not None and key != "date":
                                floats.append(self._to_float(key, row[key]))
                        if len(floats) < 14:
                            floats.extend([0.0] * (14 - len(floats)))
                        # print(f"Sent data: {floats}")

                        # 构建标准数据格式
                        std_data = {
                            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "yyq_SO2": round(floats[0], 2),
                            "jyq_SO2": round(floats[1], 2),
                            "yyq_LL": round(floats[2], 2),
                            "jyq_LL": round(floats[3], 2),
                            "yyq_O2": round(floats[4], 2),
                            "xstjy_PH": round(floats[5], 2),
                            "xstjyxhb_ADL": round(floats[6], 2),
                            "xstjyxhb_BDL": round(floats[7], 2),
                            "xstjyxhb_CDL": round(floats[8], 2),
                            "xstjyxhb_DDL": round(floats[9], 2),
                            "xstjyxhb_EDL": round(floats[10], 2),
                            "xstshsjy_MD": round(floats[11], 2),
                            "xst_YW": round(floats[12], 2),
                            "xstyhfj_ADL": round(floats[13], 2),
                            "jym": 50,
                            "connection_status": True,
                        }
                        # print(f"std_data={std_data}")

                        self.global_data["connection_status"] = True
                        self.global_data["data"].append(std_data)

                    except Exception as e:
                        traceback.print_exc()
                    finally:
                        time.sleep(1)

        except Exception as e:
            self.global_data["connection_status"] = False
            traceback.print_exc()
