import random
import traceback
import matplotlib.dates as mdates
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
from PyQt5.QtWidgets import (
    QLabel,
    QHBoxLayout,
    QFrame,
    QVBoxLayout,
)
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from collections import deque
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal,QMutex, QMutexLocker
from PyQt5.QtGui import QPainter, QPixmap
from PyQt5.QtWidgets import QMainWindow, QApplication, QWidget
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator

from system.gui.base.SingleMainRootWindow import Ui_MainWindow
from system.base.config.SysConfig import config
from system.gui.resource_paths import image_path, image_url

# 设置matplotlib字体配置
plt.rcParams["font.sans-serif"] = ["DejaVu Sans"]  # 使用中文黑体  # 使用系统自带的DejaVu Sans字体和中文字体
plt.rcParams["axes.unicode_minus"] = False  # 解决负号显示问题

class DataProcessThread(QThread):
    """数据处理线程，专门负责数据获取、处理和准备图表数据"""
    dataReady = pyqtSignal(dict)  # 处理后的数据信号
    chartDataReady = pyqtSignal()  # 图表数据准备完成信号
    
    def __init__(self, GLOBAL_DATA):
        super().__init__()
        self.GLOBAL_DATA = GLOBAL_DATA
        self.running = True
        self.processed_data = {}
        # 首页图表只记录原烟气与净烟气 SO2。
        self.chart_data = {
            "so2_chart_data": deque(maxlen=5000),
        }
        self.last_sample_time = None

        
    def run(self):
        """线程主循环，处理数据并发送信号"""
        while self.running:
            try:
                # 获取最新数据
                if "data" in self.GLOBAL_DATA and self.GLOBAL_DATA["data"] and "map_control" in self.GLOBAL_DATA:
                    data = self.GLOBAL_DATA["data"][-1].copy()  # 获取最新一条数据
                    map_control = self.GLOBAL_DATA.get("map_control", {}).copy()
                    
                    if data and map_control:
                        # 处理并保存数据
                        self.processed_data = {
                            "data": data,
                            "map_control": map_control
                        }
                        
                        # 更新图表数据
                        self.update_chart_data(data, map_control)
                        
                        # 数据处理完成后，发送信号
                        self.dataReady.emit(self.processed_data)
                        self.chartDataReady.emit()
            except Exception as e:
                print(f"数据处理线程错误: {str(e)}")
                traceback.print_exc()
                
            # 线程内部暂停1秒，控制处理频率
            self.msleep(500)

    def get_chart_data(self, chart_type):
        """获取指定类型的图表数据"""
        if chart_type in self.chart_data:
            print(f"获取{chart_type}数据: {len(self.chart_data[chart_type])}个点")
            return self.chart_data[chart_type]
        return []
    # 1. 首先修改DataProcessThread类中的数据采集方法，添加降采样功能
    def update_chart_data(self, data, map_control):
        """按一分钟间隔采集原烟气与净烟气 SO2。"""
        try:
            date_str = map_control.get("date")
            if not date_str:
                return
            current_time = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")

            if (
                self.last_sample_time is not None
                and (current_time - self.last_sample_time).total_seconds() < 60
            ):
                return
            self.last_sample_time = current_time

            if not hasattr(self, "zero_count"):
                self.zero_count = {"yyq_so2": 0, "jyq_so2": 0}

            yyq_so2 = map_control.get("yyq_SO2", 0) or 0
            jyq_so2 = map_control.get("jyq_SO2", 0) or 0
            history = self.chart_data["so2_chart_data"]

            for key, value_name in (
                ("yyq_so2", "yyq_so2"),
                ("jyq_so2", "jyq_so2"),
            ):
                value = yyq_so2 if key == "yyq_so2" else jyq_so2
                if value == 0:
                    self.zero_count[key] += 1
                    if self.zero_count[key] < 3 and history:
                        fallback = history[-1].get(value_name, 0)
                        if key == "yyq_so2":
                            yyq_so2 = fallback
                        else:
                            jyq_so2 = fallback
                else:
                    self.zero_count[key] = 0

            if yyq_so2 == 0 and jyq_so2 == 0 and not history:
                return

            history.append(
                {
                    "date": date_str,
                    "yyq_so2": yyq_so2,
                    "jyq_so2": jyq_so2,
                }
            )
            self.processed_data = {"data": data, "map_control": map_control}
        except Exception as e:
            print(f"更新SO2图表数据出错: {str(e)}")
            traceback.print_exc()


class ChartUpdateThread(QThread):
    """专门的图表更新线程，避免阻塞主UI线程"""
    chartUpdateReady = pyqtSignal(str, list, list, list)  # 图表类型, x数据, y数据, mean_data(可选)
    
    def __init__(self):
        super().__init__()
        self.running = True
        self.update_queue = []  # 待更新的图表队列
        self.lock = QMutex()  # 线程锁
        
    def run(self):
        """线程主循环"""
        while self.running:
            try:
                # 检查是否有待更新的图表
                with QMutexLocker(self.lock):
                    if self.update_queue:
                        chart_info = self.update_queue.pop(0)
                        self.process_chart_update(chart_info)
                
                # 线程休眠100毫秒，避免过度占用CPU
                self.msleep(100)
                
            except Exception as e:
                print(f"图表更新线程错误: {str(e)}")
                traceback.print_exc()
    
    def add_chart_update(self, chart_type, chart_data):
        """添加图表更新任务到队列"""
        with QMutexLocker(self.lock):
            self.update_queue.append({
                'chart_type': chart_type,
                'chart_data': chart_data
            })
    
    def process_chart_update(self, chart_info):
        """处理图表更新"""
        try:
            chart_type = chart_info['chart_type']
            chart_data = chart_info['chart_data']
            
            if not chart_data:
                return
            
            # 预处理数据
            x_data, y_data, mean_data = self.preprocess_chart_data(chart_type, chart_data)
            
            # 发送处理后的数据到主线程
            self.chartUpdateReady.emit(chart_type, x_data, y_data, mean_data)
            
        except Exception as e:
            print(f"处理图表更新出错: {str(e)}")
            traceback.print_exc()
    
    def preprocess_chart_data(self, chart_type, chart_data):
        """转换时间并提取当天原烟气/净烟气 SO2 数据。"""
        try:
            if chart_type != "so2_chart_data":
                return [], [], []

            current_time = datetime.now()
            day_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day_start + timedelta(days=1)
            x_data, yyq_data, jyq_data = [], [], []

            for item in chart_data:
                item_time = item.get("date")
                if isinstance(item_time, str):
                    item_time = datetime.strptime(item_time, "%Y-%m-%d %H:%M:%S")
                if item_time is None or not (day_start <= item_time <= day_end):
                    continue
                x_data.append(item_time)
                yyq_data.append(item.get("yyq_so2"))
                jyq_data.append(item.get("jyq_so2"))

            return x_data, yyq_data, jyq_data
        except Exception as e:
            print(f"预处理SO2图表数据出错: {str(e)}")
            traceback.print_exc()
            return [], [], []

class ExtSingleWindow(QMainWindow, Ui_MainWindow):
    """主窗口类，负责UI显示和用户交互"""
    
    def __init__(self, GLOBAL_DATA):
        try:
            super(ExtSingleWindow, self).__init__()
            super().setupUi(self)
            self._configure_two_page_mode()
            # 设置图表中文字体
            plt.rcParams["font.sans-serif"] = ["simhei"]
            self.background_image = QPixmap(image_path("bg.jpg"))
            self.setStyleSheet(None)
            self._apply_consistent_display_fonts()
            self.GLOBAL_DATA = GLOBAL_DATA
            self.prev_data = {}
            self.current_tab = 0
            
            # 加载配置
            self.gui_config = config["gui"]
            self.tower_configs = self.gui_config.get("tower", {})
            self.tower1_config = self.tower_configs.get(
                "1", {"name": "一级塔", "bump": []}
            )
            self.tower2_config = self.tower_configs.get("2")
            self.has_tower2 = bool(self.tower2_config)
            # 添加缓存变量用于状态比较
            self.prev_tower1_pump_status = {}
            self.prev_tower2_pump_status = {}
            self.prev_recommended_pump = None
            # 创建数据处理线程
            self.data_thread = DataProcessThread(GLOBAL_DATA)
            self.data_thread.dataReady.connect(self.handle_processed_data)
            # self.data_thread.chartDataReady.connect(self.update_charts)
            self.data_thread.start()
            # 创建图表更新线程
            self.chart_update_thread = ChartUpdateThread()
            self.chart_update_thread.chartUpdateReady.connect(self.handle_chart_update)
            self.chart_update_thread.start()
            # 使用单一UI更新定时器
            # 添加分离的定时器
            # 快速更新定时器 - 用于时间等需要实时更新的元素
            self.fast_ui_timer = QTimer()
            self.fast_ui_timer.timeout.connect(self.update_fast_ui_elements)
            self.fast_ui_timer.start(1000)  # 1秒更新一次
            # 慢速更新定时器 - 用于泵状态等不频繁变化的元素
            self.slow_ui_timer = QTimer()
            self.slow_ui_timer.timeout.connect(self.update_slow_ui_elements)
            self.slow_ui_timer.start(5000)  # 5秒更新一次
            # 图表更新定时器 - 每20秒更新一次
            self.chart_timer = QTimer()
            self.chart_timer.timeout.connect(self.update_charts)
            self.chart_timer.start(20000)  # 20秒更新一次图表
            # 设置标题和版本信息
            self.sys_title_label.setText(f"{self.gui_config['sys_title']}")
            self.version_label.setText(f"{self.gui_config['version']}")
            self.chart_update_counter = 0
            self.param_update_counter = 0
            # 首页显示单塔名称；建议页拆分为实时值和建议值两个模块。
            tower1_name = self.tower1_config.get("name", "一级塔")
            self.tab1_tower1_label.setText(tower1_name)
            self.tab1_tower1_label.setVisible(False)
            self.tab2_tower1_label.setText("实时值")
            self.tab2_tower2_label.setText("建议值")
            self.groupBox_2.setVisible(True)
            self.label_5.setVisible(True)
            self.label_tower1_adv_msg.setText("当前实际泵组合运行方式")
            self.label_tower2_adv_msg.setText("维持当前泵组合运行方式")

            # 设置泵列表布局
            self.setup_pump_layouts()
            
            # 只连接首页和循环泵寻优建议页。
            self.btn_home.clicked.connect(self.showTab1)
            self.btn_adv.clicked.connect(self.showTab2)
            self.showTab1()

            # 初始化保留页面所需图表
            self.init_charts()
            
            
        except Exception as e:
            traceback.print_exc()

    def _apply_consistent_display_fonts(self):
        """首页顶部使用 17px、塔体与仪表使用 16px，避免 DPI 下字号不一致。"""
        for label in self.frame.findChildren(QLabel):
            current_style = label.styleSheet().strip()
            if current_style and not current_style.endswith(";"):
                current_style += ";"
            label.setStyleSheet(f"{current_style} font-size:17px;")

        for label in self.tab1_content.findChildren(QLabel):
            current_style = label.styleSheet().strip()
            if current_style and not current_style.endswith(";"):
                current_style += ";"
            label.setStyleSheet(f"{current_style} font-size:16px;")

        for label in (
            self.label_49,
            self.tab2_tower1_ph_val,
            self.label_54,
            self.tab2_yyq_so2_val,
            self.label_58,
            self.tab2_tower2_ph_val,
        ):
            current_style = label.styleSheet().strip()
            if current_style and not current_style.endswith(";"):
                current_style += ";"
            label.setStyleSheet(
                f"{current_style} color:white; font-size:17px;"
            )
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def _configure_two_page_mode(self):
        """只保留首页和循环泵寻优建议页，并在其他初始化之前隐藏旧页面。"""
        self.current_tab = 0
        if hasattr(self, "tab1_content"):
            self.tab1_content.setVisible(True)
        if hasattr(self, "tab2_content"):
            self.tab2_content.setVisible(False)

        for object_name in ("btn_ctl", "btn_ctl_2", "tab3_content", "tab3_content_2"):
            widget = getattr(self, object_name, None)
            if widget is not None:
                widget.setVisible(False)
                widget.setEnabled(False)

    def setup_pump_layouts(self):
        """设置首页、实时值和建议值三个泵列表布局。"""
        self.tab1_tower1_pump_list_layout = QHBoxLayout(
            self.tab1_tower1_pump_list
        )
        self.tab1_tower1_pump_list_layout.setSpacing(15)
        self.tab1_tower1_pump_list_layout.setContentsMargins(5, 0, 5, 0)
        self.tab1_tower1_pump_list_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tab1_tower1_pump_list.setLayout(self.tab1_tower1_pump_list_layout)
        self.tab1_tower1_pump_list.setStyleSheet(
            "background: transparent; border: none;"
        )

        self.tab2_tower1_pump_list_layout = QHBoxLayout(
            self.tab2_tower1_pump_list
        )
        self.tab2_tower1_pump_list_layout.setSpacing(5)
        self.tab2_tower1_pump_list_layout.setContentsMargins(5, 5, 5, 5)
        self.tab2_tower1_pump_list_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tab2_tower1_pump_list.setLayout(self.tab2_tower1_pump_list_layout)

        self.tab2_tower2_pump_list_layout = QHBoxLayout(
            self.tab2_tower2_pump_list
        )
        self.tab2_tower2_pump_list_layout.setSpacing(5)
        self.tab2_tower2_pump_list_layout.setContentsMargins(5, 5, 5, 5)
        self.tab2_tower2_pump_list_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tab2_tower2_pump_list.setLayout(self.tab2_tower2_pump_list_layout)

    def init_charts(self):
        """初始化首页原烟气/净烟气 SO2 图表。"""
        self.so2_chart_figure = plt.Figure(figsize=(1, 1))
        self.so2_chart_canvas = FigureCanvas(self.so2_chart_figure)
        self.left_chart.addWidget(self.so2_chart_canvas)
        self.so2_chart_ax = self.so2_chart_figure.add_subplot(111)
        self.so2_chart_ax.yaxis.grid(True)
        self.so2_chart_ax.xaxis.grid(False)
        self.so2_chart_figure.set_facecolor("#060d2a")
        self.so2_chart_ax.set_facecolor("#060d2a")
        self._latest_so2_chart_payload = None


    def handle_chart_update(self, chart_type, x_data, y_data, mean_data):
        """处理 SO2 图表更新；当前页面不影响后台图表刷新。"""
        try:
            if chart_type != "so2_chart_data" or not x_data:
                return

            self._latest_so2_chart_payload = (
                list(x_data),
                list(y_data),
                list(mean_data),
            )
            self.update_so2_chart_with_data(x_data, y_data, mean_data)
        except Exception as e:
            print(f"处理SO2图表更新出错: {str(e)}")
            traceback.print_exc()

    def handle_processed_data(self, processed_data):
        """处理来自数据线程的已处理数据"""
        self.prev_data = processed_data  # 保存处理后的数据供后续使用
        # 立即更新UI元素，但不更新图表
        # self.update_ui_elements()

    def update_fast_ui_elements(self):
        """更新时间、通讯状态和首页保留的实时字段。"""
        try:
            self.date_time_label.setText(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

            connection_status = self.GLOBAL_DATA.get("connection_status", False)
            data_expired = False
            offline_mode = False
            if self.prev_data and self.prev_data.get("map_control"):
                map_control = self.prev_data["map_control"]
                data_expired = map_control.get("data_expired", False)
                offline_mode = map_control.get("offline_mode", False)

            if connection_status:
                self.label_status.setText("通讯正常")
            elif offline_mode and not data_expired:
                self.label_status.setText("通讯中断")
            else:
                self.label_status.setText("通讯失败")

            self.param_update_counter += 1
            if self.param_update_counter < 10:
                return
            self.param_update_counter = 0

            if not self.prev_data or not self.prev_data.get("map_control"):
                return
            data = self.prev_data.get("data") or {}
            map_control = self.prev_data["map_control"]
            self.label_top_yyq_so2.setText(
                f"{round(map_control.get('yyq_SO2', 0) or 0, 2)}mg/m3"
            )
            self.label_jyq_so2.setText(
                f"{round(map_control.get('jyq_SO2', 0) or 0, 2)}mg/m3"
            )
            self.update_tower_data(data, map_control)
        except Exception as e:
            print(f"更新快速UI元素出错: {str(e)}")
            traceback.print_exc()

    def update_slow_ui_elements(self):
        """更新不需要频繁刷新的UI元素"""
        try:
            if not self.prev_data or not self.prev_data.get("data") or not self.prev_data.get("map_control"):
                return
                    
            data = self.prev_data.get("data")
            map_control = self.prev_data.get("map_control")
            
            # 更新泵状态
            self.update_pump_status(data, map_control)
            
                        
        except Exception as e:
            print(f"更新慢速UI元素出错: {str(e)}")
            traceback.print_exc()    
    def update_tower_data(self, data, map_control):
        """更新单塔首页保留的工艺数据显示。"""
        try:
            self.label_yyq_so2.setText(
                f"{round(map_control.get('yyq_SO2', 0) or 0, 2)}mg/m3"
            )
            self.label_yyq_o2.setText(
                f"{round(map_control.get('yyq_O2', 0) or 0, 2)}%"
            )
            self.label_yyq_ll.setText(
                f"{round(map_control.get('yyq_LL', 0) or 0)}m3/h"
            )
            # 原“原烟气温度”位置改为净烟气流量 jyq_LL。
            self.label_yyq_wd.setText(
                f"{round(map_control.get('jyq_LL', 0) or 0)}m3/h"
            )

            xst_ph = map_control.get("xstjy_PH")
            if xst_ph is not None:
                self.label_yxt_ph.setText(f"{round(xst_ph, 2)}")
            self.label_yxt_jymd.setText(
                f"{round(map_control.get('xstshsjy_MD', 0) or 0)}kg/m3"
            )
            self.label_yxt_yw.setText(
                f"{round(map_control.get('xst_YW', 0) or 0, 2)}m"
            )
            self.label_xst_jyq_so2.setText(
                f"{round(map_control.get('jyq_SO2', 0) or 0, 2)}mg/m3"
            )

            self.update_tab2_data(data, map_control)
        except Exception as e:
            print(f"更新塔数据出错: {str(e)}")
            traceback.print_exc()


    def update_tab2_data(self, data, map_control):
        """上半区显示实时 pH，下半区显示建议 pH。"""
        try:
            current_ph = map_control.get("xstjy_PH")
            current_ph_text = (
                "--" if current_ph is None else f"{float(current_ph):.2f}"
            )
            self.tab2_tower1_ph_val.setText(current_ph_text)
            self.tab2_yyq_so2_val.setText(
                f"{round(map_control.get('jyq_SO2', 0) or 0, 2)}mg/m3"
            )

            # 在线模块通过 suggested_xst_ph 输出一级塔建议 pH。
            # 没有建议值时显示“--”，避免误显示为 0.0。
            suggested_ph = map_control.get("suggested_xst_ph")
            suggested_ph_text = (
                "--" if suggested_ph is None else f"{float(suggested_ph):.2f}"
            )
            self.tab2_tower2_ph_val.setText(suggested_ph_text)
        except (TypeError, ValueError):
            self.tab2_tower1_ph_val.setText("--")
            self.tab2_tower2_ph_val.setText("--")
        except Exception as e:
            print(f"更新Tab2数据出错: {str(e)}")
            traceback.print_exc()

    def update_pump_status(self, data, map_control):
        """分别更新实时泵状态和建议泵状态。"""
        try:
            pump_list = self.tower1_config.get("bump", [])
            pump_count = len(pump_list)
            if pump_count == 0:
                return

            recommended_pump = map_control.get("recommended_pump")
            if not recommended_pump:
                recommended_pump = map_control.get("combined_pump_status")

            current_bits = []
            for item in pump_list:
                code = item["code"]
                current_bits.append(
                    "1" if (map_control.get(code, 0) or 0) >= 30 else "0"
                )

            if recommended_pump:
                recommendation_text = str(recommended_pump)
                recommended_bits = (
                    recommendation_text.split("-")
                    if "-" in recommendation_text
                    else list(recommendation_text)
                )
            else:
                recommended_bits = current_bits.copy()

            recommended_bits = [
                "1" if str(value).strip() == "1" else "0"
                for value in recommended_bits[:pump_count]
            ]
            if len(recommended_bits) < pump_count:
                recommended_bits.extend(
                    current_bits[len(recommended_bits) : pump_count]
                )

            current_status = {
                item["code"]: ("on" if current_bits[index] == "1" else "off")
                for index, item in enumerate(pump_list)
            }
            recommendation_key = "-".join(recommended_bits)
            if (
                current_status == self.prev_tower1_pump_status
                and recommendation_key == self.prev_recommended_pump
            ):
                return

            self.prev_tower1_pump_status = current_status.copy()
            self.prev_recommended_pump = recommendation_key

            self.clear_pump(self.tab1_tower1_pump_list_layout)
            self.clear_pump(self.tab2_tower1_pump_list_layout)
            self.clear_pump(self.tab2_tower2_pump_list_layout)

            for index, item in enumerate(pump_list):
                name = item["name"]
                real_status = "on" if current_bits[index] == "1" else "off"
                advice_status = (
                    "on" if recommended_bits[index] == "1" else "off"
                )

                self.add_simple_pump(
                    self.tab1_tower1_pump_list_layout,
                    name,
                    real_status,
                )
                self.add_status_pump(
                    self.tab2_tower1_pump_list_layout,
                    name,
                    real_status,
                )
                self.add_status_pump(
                    self.tab2_tower2_pump_list_layout,
                    name,
                    advice_status,
                )

            self.update_pump_advice(current_bits, recommended_bits)
        except Exception as e:
            print(f"更新泵状态出错: {str(e)}")
            traceback.print_exc()

    def update_pump_advice(self, current_bits, recommended_bits):
        """更新下半区的泵组合建议说明。"""
        try:
            if current_bits == recommended_bits:
                message = "维持当前泵组合运行方式"
            else:
                message = "建议切换为推荐泵组合运行方式"
            self.label_tower2_adv_msg.setText(message)
        except Exception as e:
            print(f"更新泵建议信息出错: {str(e)}")
            traceback.print_exc()

    def update_so2_chart_with_data(self, x_data, yyq_data, jyq_data):
        """绘制当天原烟气 SO2 与净烟气 SO2。"""
        try:
            if not x_data:
                return

            self.so2_chart_ax.clear()
            if hasattr(self, "jyq_so2_ax"):
                self.jyq_so2_ax.remove()
            self.jyq_so2_ax = self.so2_chart_ax.twinx()

            yyq_valid = [value for value in yyq_data if value is not None]
            jyq_valid = [value for value in jyq_data if value is not None]

            if yyq_valid:
                self.so2_chart_ax.plot(
                    x_data,
                    yyq_data,
                    linestyle="-",
                    linewidth=0.8,
                    color="red",
                    label="原烟气SO2",
                    drawstyle="steps-post",
                )
            if jyq_valid:
                self.jyq_so2_ax.plot(
                    x_data,
                    jyq_data,
                    linestyle="--",
                    linewidth=0.8,
                    color="green",
                    label="净烟气SO2",
                    drawstyle="steps-post",
                )

            day_start = datetime.now().replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            self.so2_chart_ax.set_xlim(day_start, day_start + timedelta(days=1))
            self.so2_chart_ax.xaxis.set_major_locator(mdates.HourLocator(interval=3))
            self.so2_chart_ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
            self.so2_chart_ax.xaxis.set_minor_locator(mdates.HourLocator(interval=1))
            self.so2_chart_ax.grid(True, which="major", linestyle="-", alpha=0.3)
            self.so2_chart_ax.grid(True, which="minor", linestyle=":", alpha=0.1)

            if yyq_valid:
                yyq_max = max(yyq_valid)
                self.so2_chart_ax.set_ylim(0, max(100, yyq_max * 1.1))
            else:
                self.so2_chart_ax.set_ylim(0, 100)
            if jyq_valid:
                jyq_max = max(jyq_valid)
                self.jyq_so2_ax.set_ylim(0, max(35, jyq_max * 1.15))
            else:
                self.jyq_so2_ax.set_ylim(0, 35)

            self.so2_chart_ax.set_ylabel("原烟气SO2（mg/m3）", color="red")
            self.jyq_so2_ax.set_ylabel("净烟气SO2（mg/m3）", color="green")
            self.so2_chart_ax.tick_params(axis="x", colors="white", which="both")
            self.so2_chart_ax.tick_params(axis="y", colors="red")
            self.jyq_so2_ax.tick_params(axis="y", colors="green")
            self.so2_chart_ax.set_facecolor("#060d2a")

            lines1, labels1 = self.so2_chart_ax.get_legend_handles_labels()
            lines2, labels2 = self.jyq_so2_ax.get_legend_handles_labels()
            self.so2_chart_ax.legend(
                lines1 + lines2,
                labels1 + labels2,
                loc="upper right",
                fontsize=8,
                facecolor="white",
            )
            # 首页隐藏时也更新 Matplotlib 后台缓冲区。
            self.so2_chart_canvas.draw()
        except Exception as e:
            print(f"更新SO2图表出错: {e}")
            traceback.print_exc()

    def update_charts(self):
        """更新首页原烟气/净烟气 SO2 图表。"""
        try:
            chart_data = self.data_thread.get_chart_data("so2_chart_data")
            self.chart_update_thread.add_chart_update("so2_chart_data", chart_data)
        except Exception as e:
            print(f"更新SO2图表出错: {str(e)}")
            traceback.print_exc()


    
    
    @staticmethod
    def _navigation_button_style(active: bool) -> str:
        """选中项蓝底、未选中项透明；导航按钮不显示白色边框。"""
        background = "DodgerBlue" if active else "transparent"
        foreground = "black" if active else "white"
        return (
            f"background-color:{background};"
            f"color:{foreground};"
            "border:none;"
            "font-size:18px;"
            "font-weight:500;"
            "padding:0px 10px;"
        )

    def showTab1(self):
        """切换到首页，并立即显示后台持续更新的最新 SO2 图表。"""
        self.current_tab = 0
        self.tab1_content.setVisible(True)
        self.tab2_content.setVisible(False)
        self.btn_home.setStyleSheet(self._navigation_button_style(True))
        self.btn_adv.setStyleSheet(self._navigation_button_style(False))

        latest_payload = getattr(self, "_latest_so2_chart_payload", None)
        if latest_payload:
            x_data, yyq_data, jyq_data = latest_payload
            self.update_so2_chart_with_data(x_data, yyq_data, jyq_data)

    def showTab2(self):
        """切换到循环泵寻优建议页。"""
        self.current_tab = 1
        self.tab1_content.setVisible(False)
        self.tab2_content.setVisible(True)
        self.btn_home.setStyleSheet(self._navigation_button_style(False))
        self.btn_adv.setStyleSheet(self._navigation_button_style(True))

    def clear_pump(self, layoutObj):
        """清空泵列表布局"""
        if layoutObj is None:
            return
            
        while layoutObj.count():
            item = layoutObj.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_sub_layout(item.layout())

    def _clear_sub_layout(self, layout):
        """清空子布局"""
        if layout is None:
            return
            
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_sub_layout(item.layout())

    def add_simple_pump(self, layout_obj, name, status):
        """首页泵图标和名称使用固定像素坐标，避免名称被裁剪或左偏。"""
        frame = QFrame()
        frame.setFixedSize(105, 100)
        frame.setStyleSheet("background: transparent; border: none;")

        icon = QLabel(frame)
        icon.setGeometry(18, 0, 68, 68)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(
            f"background-image:url({image_url(f'bump_{status}.png')});"
            "background-position:center; background-repeat:no-repeat; border:none;"
        )

        name_label = QLabel(frame)
        name_label.setGeometry(0, 70, 105, 26)
        name_label.setText(name)
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_label.setStyleSheet(
            "background:transparent; color:white; border:none; font-size:17px;"
        )

        layout_obj.addWidget(frame, 0, Qt.AlignmentFlag.AlignCenter)

    def add_status_pump(self, layout_obj, name, status):
        """泵图标、状态和底部名称分别固定居中；名称位于横向底框内。"""
        frame = QFrame()
        frame.setFixedSize(120, 248)
        frame.setStyleSheet(
            f"background-image:url({image_url('bump_group.png')});"
            "background-position:center; background-repeat:no-repeat; border:none;"
        )

        icon = QLabel(frame)
        icon.setGeometry(26, 52, 68, 68)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(
            f"background-image:url({image_url(f'bump_{status}.png')});"
            "background-position:center; background-repeat:no-repeat; border:none;"
        )

        status_label = QLabel(frame)
        status_label.setGeometry(10, 132, 100, 30)
        status_label.setText("开启" if status == "on" else "关闭")
        status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_label.setStyleSheet(
            "background:transparent; color:white; border:none; font-size:17px;"
        )

        # bump_group.png 的下方横条区域位于约 y=214~247，泵名称单独放入该横条。
        name_label = QLabel(frame)
        name_label.setGeometry(5, 216, 110, 28)
        name_label.setText(name)
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_label.setStyleSheet(
            "background:transparent; color:white; border:none; font-size:17px;"
        )

        layout_obj.addWidget(frame, 0, Qt.AlignmentFlag.AlignCenter)

    def paintEvent(self, event):
        """重写paintEvent方法以绘制背景图片"""
        painter = QPainter(self)

        # 获取窗口的宽度和高度
        window_width = 1680
        window_height = 960

        # 禁用窗口缩放功能
        self.setFixedSize(window_width, window_height)
        scaled_pixmap = self.background_image.scaledToHeight(
            window_height, Qt.SmoothTransformation
        )

        # 计算图片的水平居中位置
        x = (window_width - scaled_pixmap.width()) // 2

        # 绘制背景图片
        painter.drawPixmap(x, 0, scaled_pixmap)
    def closeEvent(self, event):
        """窗口关闭事件"""
        try:
            # 停止数据处理线程
            if hasattr(self, 'data_thread'):
                self.data_thread.running = False
                self.data_thread.wait()
            
            # 停止图表更新线程
            if hasattr(self, 'chart_update_thread'):
                self.chart_update_thread.stop()
                self.chart_update_thread.wait()
            
            event.accept()
        except Exception as e:
            print(f"关闭窗口时出错: {str(e)}")
            event.accept()
