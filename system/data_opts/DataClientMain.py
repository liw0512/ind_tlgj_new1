import time
import traceback

from system.base.LogUntil import setup_log
from system.base.config.SysConfig import config
from system.model.Process4MapControl import ProcessForMapConsole

logging = setup_log("data_client_main")


class DataClientMain:
    """实时数据客户端上层入口。

    P4PC 自己从 GLOBAL_DATA['data'] 消费最新帧并负责过滤表/模型结果表写库。
    旧 DataClientMain.insert_data -> t_data1_rt_* 链已删除，避免重复存储同一份实时数据。

    当前正式实例只使用 ``Process4MapControl.ProcessForMapConsole``。该唯一入口
    统一编排 condition_model、MFAC 与 FAST，不再存在独立的
    ``Process4MapControlMFAC`` 生产入口，也不再额外运行第二套 Qbase/target 计算。
    """

    def __init__(self, GLOBAL_DATA):
        self.GLOBAL_DATA = GLOBAL_DATA
        self.data = []
        self.process_for_mapconsole = ProcessForMapConsole(self.GLOBAL_DATA)
        self.map_console_result = []
        self.direct = []
        self.fill_result()
        self.hour = 0
        self.mouth = 0
        self.year = 0

    def fill_result(self):
        self.direct.clear()
        for _ in range(int(config.get("send_master_redirect_data_sum", 0))):
            self.direct.append(0)

    def start(self):
        """保持主线程生命周期；实际实时处理由 P4PC 内部线程完成。"""
        while True:
            try:
                # GLOBAL_DATA 由现场客户端持续更新；P4PC 已在 __init__ 中启动消费线程。
                _ = self.GLOBAL_DATA.get("data")
            except Exception as exc:
                traceback.print_exc()
                logging.error("DataClientMain.start 异常: %s", exc)
            time.sleep(1)

    def send_cnn_to_dcs(self):
        while True:
            try:
                # 当前只保留统一 map_control 输出；真实 DCS 写入接口后续在这里接入。
                _ = self.GLOBAL_DATA.get("map_control")
            except Exception as exc:
                traceback.print_exc()
                logging.error("send_cnn_to_dcs 异常: %s", exc)
            time.sleep(20)

    def get_direct(self):
        self.direct.clear()
        self.direct.extend([self.hour, self.mouth, self.year])
        self.direct.append(self.data[1] if len(self.data) > 1 else 0)
        return self.direct