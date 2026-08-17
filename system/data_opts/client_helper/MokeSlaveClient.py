import csv
import time
import traceback
from datetime import datetime


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

    def _build_std_data(self, row):
        """按测试 CSV 的实际表头构建一帧模拟通讯数据。"""
        std_data = {
            # 模拟通讯按实时帧处理，时间使用当前时间；其余字段来自 CSV。
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        for key, value in row.items():
            if key is None or key == "date":
                continue
            std_data[key] = round(self._to_float(key, value), 2)

        # 测试数据集没有通讯状态字段，由模拟客户端统一补齐。
        std_data["jym"] = 50
        std_data["connection_status"] = True
        return std_data

    def run(self):

        try:
            self.global_data["connection_status"] = True

            with open(
                r"F:\tlgj_new\files\new_data.csv",
                encoding="utf-8-sig",
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
                        std_data = self._build_std_data(row)
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
