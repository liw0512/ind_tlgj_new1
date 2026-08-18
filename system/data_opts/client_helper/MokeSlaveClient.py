import csv
import time
import traceback
from datetime import datetime


class MokeSlaveClient:
    """CSV 实时回放客户端。

    CSV 表头直接作为进入 GLOBAL_DATA 的标准字段名，不再依赖 floats[0]/floats[1]
    这类固定列序。新增/删除现场字段时，只要 CSV 表头已经按通讯层约定命名，P4PC
    就会收到完整字段，无需同步修改本文件或 Process4MapControl 的输入字段列表。
    """

    def __init__(self, global_data):
        self.global_data = global_data
        self._last_values = {}
        self.playback_interval_seconds = 1.0
        self.global_data["connection_status"] = True

    def _coerce_value(self, key, value):
        """数值尽量转 float；非数值状态字段保留字符串；空值沿用上一有效值。"""
        if value is None:
            return self._last_values.get(key)

        text = str(value).strip()
        if text == "" or text.lower() in {"nan", "none", "null"}:
            return self._last_values.get(key)

        try:
            result = float(text)
        except ValueError:
            result = text

        self._last_values[key] = result
        return result

    def _build_frame(self, row):
        # 测试回放使用当前系统时间，避免历史 CSV 时间导致在线数据过期判断。
        frame = {
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        for key, value in row.items():
            if key is None:
                continue
            column = str(key).strip()
            if not column or column == "date":
                continue
            frame[column] = self._coerce_value(column, value)

        # jym/通讯状态属于运行信息。CSV 若显式提供 jym 则保留其值，否则使用正常值50。
        if frame.get("jym") is None:
            frame["jym"] = 50
        frame["connection_status"] = True
        return frame

    def run(self):
        try:
            self.global_data["connection_status"] = True

            with open(
                r"F:\xiregangchang\ind_optim_serv_xire\files\selected_30s_processed.csv",
                newline="",
                encoding="utf-8-sig",
            ) as csvfile:
                reader = csv.DictReader(csvfile)

                # 保留原测试逻辑：默认跳过第一条数据。
                try:
                    next(reader)
                except StopIteration:
                    csvfile.seek(0)
                    reader = csv.DictReader(csvfile)

                print("开始按 CSV 表头动态回放测试数据...")
                for row in reader:
                    try:
                        std_data = self._build_frame(row)
                        self.global_data["connection_status"] = True
                        self.global_data["data"].append(std_data)
                    except Exception:
                        traceback.print_exc()
                    finally:
                        time.sleep(self.playback_interval_seconds)

        except Exception:
            self.global_data["connection_status"] = False
            traceback.print_exc()
