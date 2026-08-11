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
    QTableWidget,
    QTableWidgetItem,
    QAbstractItemView,
)
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from collections import deque
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal,QMutex, QMutexLocker
from PyQt5.QtGui import QPainter, QPixmap
from PyQt5.QtWidgets import QMainWindow, QApplication, QWidget
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator

from system.gui.base.DoubleMainRootWindow import Ui_MainWindow
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
        # 集中管理所有图表数据
        self.chart_data = {
            "jzfh_chart_data": deque(maxlen=5000),
            "tower1_ph_chart_data": deque(maxlen=5000),
            "tower2_ph_chart_data": deque(maxlen=5000),
            "jyq_so2_chart_data":deque(maxlen=5000),
            "tower1_ph_control_data": deque(maxlen=5000),
            "tower2_ph_control_data": deque(maxlen=5000),
            "so2_control_data": deque(maxlen=5000)
        }
        self.last_sample_time=None
        
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
        """更新所有图表数据，添加降采样"""
        try:
            # 获取当前时间
            current_time = datetime.strptime(map_control.get("date"), "%Y-%m-%d %H:%M:%S")
            date_str = map_control.get("date")

            # 检查是否到了采样时间
            time_to_sample = False
            if self.last_sample_time is None:
                # 如果是第一个点，直接采样
                time_to_sample = True
            else:
                # 计算与上次采样的时间差
                if (current_time - self.last_sample_time).total_seconds() >= 60:
                    time_to_sample = True

            if time_to_sample:
                # 更新上次采样时间为当前时间
                self.last_sample_time = current_time
                print(f"Adding data point at {date_str} (Interval sampling)")

                # 初始化零值计数器字典（如果不存在）
                if not hasattr(self, 'zero_count'):
                    self.zero_count = {
                        "jzfh": 0, "jyq_so2": 0, "total_cost": 0,
                        "xst_zdh": 0, "apt_zdh": 0,
                        "xstjy_PH": 0, "aptjy_PH": 0
                    }

                # 确保chart_data字典存在
                if not hasattr(self, 'chart_data'):
                    self.chart_data = {
                        "jzfh_chart_data": [],
                        "tower1_ph_chart_data": [],
                        "tower2_ph_chart_data": [],
                        "jyq_so2_chart_data": [],
                        "tower1_ph_control_data": [],
                        "tower2_ph_control_data": [],
                        "so2_control_data": []
                    }

                # 机组负荷图表数据
                jzfh_value = map_control.get("jzfh", 0)
                jyq_so2_value = map_control.get("jyq_SO2", 0)

                # 处理机组负荷值
                if jzfh_value == 0:
                    self.zero_count["jzfh"] += 1
                    if self.zero_count["jzfh"] < 3 and self.chart_data.get("jzfh_chart_data"):
                        jzfh_value = self.chart_data["jzfh_chart_data"][-1].get("today_jzfh", 0)
                else:
                    self.zero_count["jzfh"] = 0

                # 处理脱硫效率值
                if jyq_so2_value == 0:
                    self.zero_count["jyq_so2"] += 1
                    if self.zero_count["jyq_so2"] < 3 and self.chart_data.get("jzfh_chart_data"):
                        jyq_so2_value = self.chart_data["jzfh_chart_data"][-1].get("jyq_so2", 0)
                else:
                    self.zero_count["jyq_so2"] = 0

                # 第一个值为0且没有历史数据时不存储
                if not (jzfh_value == 0 and not self.chart_data.get("jzfh_chart_data")):
                    self.chart_data["jzfh_chart_data"].append({
                        "date": date_str,
                        "today_jzfh": jzfh_value,
                        "jyq_so2": jyq_so2_value
                    })

                # 运行总成本图表数据
                total_cost = map_control.get("total_cost", 0)
                if total_cost == 0:
                    self.zero_count["total_cost"] += 1
                    if self.zero_count["total_cost"] < 3 and self.chart_data.get("tower1_ph_chart_data"):
                        total_cost = self.chart_data["tower1_ph_chart_data"][-1].get("val", 0)
                else:
                    self.zero_count["total_cost"] = 0

                if total_cost and total_cost != 0:
                    self.chart_data["tower1_ph_chart_data"].append({
                        "date": date_str,
                        "val": round(total_cost, 2)
                    })

                # 一级塔电耗图表数据
                xst_zdh = map_control.get("xst_zdh", 0)
                if xst_zdh == 0:
                    self.zero_count["xst_zdh"] += 1
                    if self.zero_count["xst_zdh"] < 3 and self.chart_data.get("tower2_ph_chart_data"):
                        xst_zdh = self.chart_data["tower2_ph_chart_data"][-1].get("val", 0)
                else:
                    self.zero_count["xst_zdh"] = 0

                if xst_zdh and xst_zdh != 0:
                    self.chart_data["tower2_ph_chart_data"].append({
                        "date": date_str,
                        "val": round(xst_zdh, 2)
                    })

                # 二级塔电耗图表数据
                apt_zdh = map_control.get("apt_zdh", 0)
                if apt_zdh == 0:
                    self.zero_count["apt_zdh"] += 1
                    if self.zero_count["apt_zdh"] < 3 and self.chart_data.get("jyq_so2_chart_data"):
                        apt_zdh = self.chart_data["jyq_so2_chart_data"][-1].get("val", 0)
                else:
                    self.zero_count["apt_zdh"] = 0

                if apt_zdh and apt_zdh != 0:
                    self.chart_data["jyq_so2_chart_data"].append({
                        "date": date_str,
                        "val": round(apt_zdh, 2)
                    })

                # 一级塔PH控制回路数据
                xstjy_PH = map_control.get("xstjy_PH")
                xst_ph_mean = map_control.get("xst_ph_mean", 0)

                if xstjy_PH == 0:
                    self.zero_count["xstjy_PH"] += 1
                    if self.zero_count["xstjy_PH"] < 3 and self.chart_data.get("tower1_ph_control_data"):
                        xstjy_PH = self.chart_data["tower1_ph_control_data"][-1].get("val", 0)
                else:
                    self.zero_count["xstjy_PH"] = 0

                if xstjy_PH is not None:
                    self.chart_data["tower1_ph_control_data"].append({
                        "date": date_str,
                        "val": round(xstjy_PH, 2),
                        "mean": round(xst_ph_mean, 2)
                    })

                # 二级塔PH控制回路数据
                aptjy_PH = map_control.get("aptjy_PH")
                apt_ph_mean = map_control.get("apt_ph_mean", 0)

                if aptjy_PH == 0:
                    self.zero_count["aptjy_PH"] += 1
                    if self.zero_count["aptjy_PH"] < 3 and self.chart_data.get("tower2_ph_control_data"):
                        aptjy_PH = self.chart_data["tower2_ph_control_data"][-1].get("val", 0)
                else:
                    self.zero_count["aptjy_PH"] = 0

                if aptjy_PH is not None:
                    self.chart_data["tower2_ph_control_data"].append({
                        "date": date_str,
                        "val": round(aptjy_PH, 2),
                        "mean": round(apt_ph_mean, 2)
                    })

                # 净烟气SO2控制回路数据
                jyq_SO2 = map_control.get("jyq_SO2")
                so2_mean = map_control.get("so2_mean", 0)

                if jyq_SO2 is not None:
                    self.chart_data["so2_control_data"].append({
                        "date": date_str,
                        "val": round(jyq_SO2, 2),
                        "mean": round(so2_mean, 2)
                    })

                # 发出图表数据准备完成信号
                self.chartDataReady.emit()

            # 保存所有数据，用于实时文本显示
            self.processed_data = {
                "data": data,
                "map_control": map_control
            }
        except Exception as e:
            print(f"更新图表数据出错: {str(e)}")
            import traceback
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
        """预处理图表数据，将字符串时间转换为datetime对象，并过滤时间范围"""
        try:
            x_data = []
            y_data = []
            mean_data = []
            
            # 计算时间范围
            current_time = datetime.now()
            if chart_type == "jzfh_chart_data":
                # 机组负荷图表：只保留当天的数据
                day_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
                day_end = day_start + timedelta(days=1)
            else:
                # 其他图表：只保留最近4小时的数据
                four_hours_ago = current_time - timedelta(hours=4)
                day_start = four_hours_ago
                day_end = current_time
            
            # 过滤数据，只处理时间范围内的数据
            filtered_data = []
            for item in chart_data:
                if isinstance(item.get("date"), str):
                    item_time = datetime.strptime(item["date"], "%Y-%m-%d %H:%M:%S")
                else:
                    item_time = item.get("date")
                
                if day_start <= item_time <= day_end:
                    filtered_data.append(item)
            
            # 对过滤后的数据进行处理
            for item in filtered_data:
                if isinstance(item.get("date"), str):
                    x_data.append(datetime.strptime(item["date"], "%Y-%m-%d %H:%M:%S"))
                else:
                    x_data.append(item.get("date"))
                
                # 提取Y轴数据
                if chart_type == "jzfh_chart_data":
                    y_data.append(item.get("today_jzfh"))
                    mean_data.append(item.get("jyq_so2"))
                elif chart_type in ["tower1_ph_control_data", "tower2_ph_control_data", "so2_control_data"]:
                    y_data.append(item.get("val"))
                    mean_data.append(item.get("mean"))
                else:
                    y_data.append(item.get("val"))
                    mean_data.append(None)
            
            return x_data, y_data, mean_data
            
        except Exception as e:
            print(f"预处理图表数据出错: {str(e)}")
            traceback.print_exc()
            return [], [], []
class ExtDoubleWindow(QMainWindow, Ui_MainWindow):
    """主窗口类，负责UI显示和用户交互"""
    
    def __init__(self, GLOBAL_DATA):
        try:
            super(ExtDoubleWindow, self).__init__()
            super().setupUi(self)
            # 设置图表中文字体
            plt.rcParams["font.sans-serif"] = ["simhei"]
            self.background_image = QPixmap(image_path("bg.jpg"))
            self.setStyleSheet(None)
            self.GLOBAL_DATA = GLOBAL_DATA
            self.prev_data = {}
            self.current_tab = 0
            
            # 加载配置
            self.gui_config = config["gui"]
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
            # 设置塔标签文本
            self.tab1_tower1_label.setText(f"{self.gui_config['tower']['1']['name']}")
            self.tab1_tower2_label.setText(f"{self.gui_config['tower']['2']['name']}")
            self.tab2_tower1_label.setText(f"{self.gui_config['tower']['1']['name']}")
            self.tab2_tower2_label.setText(f"{self.gui_config['tower']['2']['name']}")
            self.tab3_tower1_label.setText(f"{self.gui_config['tower']['1']['name']}总功率")
            self.tab3_tower2_label.setText(f"{self.gui_config['tower']['2']['name']}总功率")
            
            # 设置泵列表布局
            self.setup_pump_layouts()
            
            # 设置标签页显示状态
            self.tab1_content.setVisible(True)
            self.tab2_content.setVisible(False)
            self.tab3_content.setVisible(False)
            self.tab3_content_2.setVisible(False)
            
            # 连接按钮事件
            self.btn_home.clicked.connect(self.showTab1)
            self.btn_adv.clicked.connect(self.showTab2)
            # self.btn_ctl.clicked.connect(self.showTab3)
            # self.btn_ctl_2.clicked.connect(self.showTab4)

            # 隐藏第3、4个标签页按钮和内容
            self.btn_ctl.setVisible(False)
            self.btn_ctl_2.setVisible(False)
            self.tab3_content.setVisible(False)
            self.tab3_content_2.setVisible(False)

            # 设置按钮样式
            self.btn_home.setStyleSheet("background-color:DodgerBlue;color:black;")
            self.btn_adv.setStyleSheet("background-color:transparent;color:white;")
            self.btn_ctl.setStyleSheet("background-color:transparent;color:white;")
            self.btn_ctl_2.setStyleSheet("background-color:transparent;color:white;")
            
            
            # 初始化所有图表
            self.init_charts()
            
            # 设置表格样式
            self.setup_tables()
            
        except Exception as e:
            traceback.print_exc()

    def setup_pump_layouts(self):
        """设置泵列表布局"""
        # 定义首页一级塔的泵列表布局对象
        self.tab1_tower1_pump_list_layout = QHBoxLayout(self.tab1_tower1_pump_list)
        self.tab1_tower1_pump_list_layout.setSpacing(5)
        self.tab1_tower1_pump_list_layout.setContentsMargins(2, 2, 2, 2)
        self.tab1_tower1_pump_list.setLayout(self.tab1_tower1_pump_list_layout)
        self.tab1_tower1_pump_list.setStyleSheet("background: transparent; border: none;")
        
        # 定义首页二级塔的泵列表布局对象
        self.tab1_tower2_pump_list_layout = QHBoxLayout(self.tab1_tower2_pump_list)
        self.tab1_tower2_pump_list_layout.setSpacing(5)
        self.tab1_tower2_pump_list_layout.setContentsMargins(2, 2, 2, 2)
        self.tab1_tower2_pump_list.setLayout(self.tab1_tower2_pump_list_layout)
        self.tab1_tower2_pump_list.setStyleSheet("background: transparent; border: none;")
        
        # 定义一级塔的泵列表布局对象
        self.tab2_tower1_pump_list_layout = QHBoxLayout(self.tab2_tower1_pump_list)
        self.tab2_tower1_pump_list_layout.setSpacing(15)
        self.tab2_tower1_pump_list_layout.setContentsMargins(20, 20, 20, 20)
        self.tab2_tower1_pump_list.setLayout(self.tab2_tower1_pump_list_layout)
        
        # 定义二级塔的泵列表布局对象
        self.tab2_tower2_pump_list_layout = QHBoxLayout(self.tab2_tower2_pump_list)
        self.tab2_tower2_pump_list_layout.setSpacing(15)
        self.tab2_tower2_pump_list_layout.setContentsMargins(20, 20, 20, 20)
        self.tab2_tower2_pump_list.setLayout(self.tab2_tower2_pump_list_layout)

    def init_charts(self):
        """初始化所有图表"""
        # 初始化机组负荷图表
        jzfh_chart_figure = plt.Figure(figsize=(1, 1))
        self.jzfh_chart_canvas = FigureCanvas(jzfh_chart_figure)
        self.left_chart.addWidget(self.jzfh_chart_canvas)
        self.jzfh_chart_ax = jzfh_chart_figure.add_subplot(111)
        self.jzfh_chart_ax.yaxis.grid(True)
        self.jzfh_chart_ax.xaxis.grid(False)
        jzfh_chart_figure.set_facecolor("#060d2a")
        self.jzfh_chart_ax.set_facecolor("#060d2a")
        
        # TAB 3 图表初始化
        ph1_chart_figure = plt.Figure(figsize=(1, 1))
        self.ph1_chart_canvas = FigureCanvas(ph1_chart_figure)
        self.ph1_chart.addWidget(self.ph1_chart_canvas)
        self.ph1_chart_ax = ph1_chart_figure.add_subplot(111)
        self.ph1_chart_ax.yaxis.grid(True)
        self.ph1_chart_ax.xaxis.grid(False)
        ph1_chart_figure.set_facecolor("#060d2a")
        self.ph1_chart_ax.set_facecolor("#060d2a")
        
        ph2_chart_figure = plt.Figure(figsize=(1, 1))
        self.ph2_chart_canvas = FigureCanvas(ph2_chart_figure)
        self.ph2_chart.addWidget(self.ph2_chart_canvas)
        self.ph2_chart_ax = ph2_chart_figure.add_subplot(111)
        self.ph2_chart_ax.yaxis.grid(True)
        self.ph2_chart_ax.xaxis.grid(False)
        ph2_chart_figure.set_facecolor("#060d2a")
        self.ph2_chart_ax.set_facecolor("#060d2a")
        
        so2_chart_figure = plt.Figure(figsize=(1, 1))
        self.so2_chart_canvas = FigureCanvas(so2_chart_figure)
        self.so2_chart.addWidget(self.so2_chart_canvas)
        self.so2_chart_ax = so2_chart_figure.add_subplot(111)
        self.so2_chart_ax.yaxis.grid(True)
        self.so2_chart_ax.xaxis.grid(False)
        so2_chart_figure.set_facecolor("#060d2a")
        self.so2_chart_ax.set_facecolor("#060d2a")
        
        # TAB 4 图表初始化
        ph1_chart_figure_2 = plt.Figure(figsize=(1, 1))
        self.ph1_chart_canvas_2 = FigureCanvas(ph1_chart_figure_2)
        self.ph1_chart_2.addWidget(self.ph1_chart_canvas_2)
        self.ph1_chart_ax_2 = ph1_chart_figure_2.add_subplot(111)
        self.ph1_chart_ax_2.yaxis.grid(True)
        self.ph1_chart_ax_2.xaxis.grid(False)
        ph1_chart_figure_2.set_facecolor("#060d2a")
        self.ph1_chart_ax_2.set_facecolor("#060d2a")
        
        ph2_chart_figure_2 = plt.Figure(figsize=(1, 1))
        self.ph2_chart_canvas_2 = FigureCanvas(ph2_chart_figure_2)
        self.ph2_chart_2.addWidget(self.ph2_chart_canvas_2)
        self.ph2_chart_ax_2 = ph2_chart_figure_2.add_subplot(111)
        self.ph2_chart_ax_2.yaxis.grid(True)
        self.ph2_chart_ax_2.xaxis.grid(False)
        ph2_chart_figure_2.set_facecolor("#060d2a")
        self.ph2_chart_ax_2.set_facecolor("#060d2a")
        
        so2_chart_figure_2 = plt.Figure(figsize=(1, 1))
        self.so2_chart_canvas_2 = FigureCanvas(so2_chart_figure_2)
        self.so2_chart_2.addWidget(self.so2_chart_canvas_2)
        self.so2_chart_ax_2 = so2_chart_figure_2.add_subplot(111)
        self.so2_chart_ax_2.yaxis.grid(True)
        self.so2_chart_ax_2.xaxis.grid(False)
        so2_chart_figure_2.set_facecolor("#060d2a")
        self.so2_chart_ax_2.set_facecolor("#060d2a")

    def setup_tables(self):
        """设置表格样式"""
        for tab in [self.tower1_ph_table, self.tower2_ph_table, self.jyq_so2_table]:
            tab.setEditTriggers(QAbstractItemView.NoEditTriggers)
            tab.verticalHeader().setVisible(False)
            tab.setRowCount(2)
            tab.setColumnCount(5)
            tab.setMaximumWidth(380)
            # 设置列宽
            tab.setColumnWidth(0, 100)
            tab.setColumnWidth(1, 70)
            tab.setColumnWidth(2, 70)
            tab.setColumnWidth(3, 70)
            tab.setColumnWidth(4, 70)
            tab.setHorizontalHeaderLabels(
                ["项目", "前 4 小时", "前 8 小时", "前 1 天", "前 7 天"]
            )
            # 设置表头样式
            header = tab.horizontalHeader()
            header.setStyleSheet("QHeaderView::section { background-color: #060c28; color: white; border: none; }")
            
            # 设置表格整体样式
            tab.setStyleSheet("""
                QTableWidget {
                    background-color: #060c28; 
                    color: white;
                    selection-background-color: #060c28;
                    selection-color: white;
                    gridline-color: #060c28;
                    border: none;
                }
                QTableWidget::item {
                    background-color: #060c28;
                    border: none;
                    text-align: center;
                }
                QTableWidget::item:selected {
                    background-color: #060c28;
                    color: white;
                }
            """)
    def handle_chart_update(self, chart_type, x_data, y_data, mean_data):
        """处理来自图表更新线程的更新结果"""
        try:
            if not x_data or not y_data:
                return
                
            # 根据图表类型调用相应的更新方法
            if chart_type == "jzfh_chart_data":
                self.update_jzfh_chart_with_data(x_data, y_data, mean_data)
            elif chart_type == "tower1_ph_chart_data":
                self.update_total_cost_chart_with_data(x_data, y_data)
            elif chart_type == "tower2_ph_chart_data":
                self.update_tower1_elec_chart_with_data(x_data, y_data)
            elif chart_type == "jyq_so2_chart_data":
                self.update_tower2_elec_chart_with_data(x_data, y_data)
            elif chart_type == "tower1_ph_control_data":
                self.update_tower1_ph_chart_with_data(x_data, y_data, mean_data)
            elif chart_type == "tower2_ph_control_data":
                self.update_tower2_ph_chart_with_data(x_data, y_data, mean_data)
            elif chart_type == "so2_control_data":
                self.update_jyq_so2_chart_with_data(x_data, y_data, mean_data)
                
        except Exception as e:
            print(f"处理图表更新出错: {str(e)}")
            traceback.print_exc()
    def handle_processed_data(self, processed_data):
        """处理来自数据线程的已处理数据"""
        self.prev_data = processed_data  # 保存处理后的数据供后续使用
        # 立即更新UI元素，但不更新图表
        # self.update_ui_elements()

    def update_fast_ui_elements(self):
        """更新需要频繁刷新的UI元素"""
        try:
            # 更新时间 - 每秒更新
            datetime_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.date_time_label.setText(datetime_str)
            # 更新连接状态
            connection_status = self.GLOBAL_DATA.get("connection_status", False)
            data_expired = False
            offline_mode = False

            # 先获取map_control
            if self.prev_data and self.prev_data.get("map_control"):
                map_control = self.prev_data.get("map_control")
                data_expired = map_control.get("data_expired", False)
                offline_mode = map_control.get("offline_mode", False)

            if connection_status:
                self.label_status.setText("通讯正常")
                # self.label_status.setStyleSheet("color: green;")
            elif offline_mode and not data_expired:
                self.label_status.setText("通讯中断")
                # self.label_status.setStyleSheet("color: orange;")
            else:
                self.label_status.setText("通讯失败")
                # self.label_status.setStyleSheet("color: red;")
            # 其他参数每10秒更新一次
            self.param_update_counter += 1
            if self.param_update_counter >= 10:
                self.param_update_counter = 0
                
                if not self.prev_data or not self.prev_data.get("data") or not self.prev_data.get("map_control"):
                    return
                        
                data = self.prev_data.get("data")
                map_control = self.prev_data.get("map_control")
                
                # 更新关键参数
                self.label_jzfh.setText(f"{round(map_control.get('jzfh', 0))}MW")
                self.label_jyq_so2.setText(f"{round(map_control.get('jyq_SO2', 0),2)}mg/m3")
                # self.label_status.setText("通讯正常")
                
                # 更新塔数据
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
        """更新塔数据显示"""
        try:
            # tower 1
            self.label_yyq_so2.setText(f"{round(map_control.get('yyq_SO2'))}mg/m3")
            self.label_yyq_o2.setText(f"{round(map_control.get('yyq_O2'))}%")
            self.label_yyq_ll.setText(f"{round(map_control.get('yyq_LL'))}m3/h")
            
            self.label_yyq_wd.setText(
                    f"{round(map_control.get('yyq_WD'), 2)}℃")

            if map_control.get("xstjy_PH"):
                self.label_yxt_ph.setText(f"{round(map_control.get('xstjy_PH'), 2)}")
            self.label_yxt_jymd.setText(f"{round(map_control.get('xstshsjy_MD',0))}kg/m3")
            self.label_yxt_yw.setText(f"{round(map_control.get('xst_YW'), 2)}m")
            if map_control.get("xst_fan_flow_mode1"):
                self.label_yxt_yhfl_1.setText(
                    f"{round(map_control.get('xst_fan_flow_mode1',0), 2)} m3/h"
                )
            if map_control.get("xst_base_flow"):
                self.label_xst_jzgj.setText(
                    f"{round(map_control.get('xst_base_flow',0), 2)} m3/h"
                )

            # 联络烟道
            try:
                llyd_value = map_control.get('llyd_SO2', 0)
                self.label_llyd.setText(f"{llyd_value}mg/m3")
            except AttributeError:
                print("Warning: label_llyd control not found")
            except Exception as e:
                print(f"Error updating llyd value: {e}")
                
            # tower 2
            if map_control.get("aptjy_PH"):
                self.label_xst_ph.setText(f"{round(map_control.get('aptjy_PH'), 2)}")
            self.label_xst_yw.setText(f"{round(map_control.get('apt_YW'), 2)}m")
            self.label_xst_jymd.setText(f"{round(map_control.get('aptshsjy_MD',0))}kg/m3")
            self.label_xst_jyq_so2.setText(f"{round(map_control.get('jyq_SO2'), 2)}mg/m3")
            if map_control.get("apt_fan_flow_mode1"):
                self.label_apt_yhfl_1.setText(
                    f"{round(map_control.get('apt_fan_flow_mode1',2), 2)} m3/h"
                )
            if map_control.get("M0"):
                self.label_apt_so2.setText(f"{round(map_control.get('M0',2), 2)} t/h")
            if map_control.get("M1_daily"):
                self.label_apt_pfzl.setText(
                    f"{round(map_control.get('M1_daily',2), 2)} t/h"
                )
            if map_control.get("M1_monthly"):
                self.label_apt_pfzl_2.setText(
                    f"{round(map_control.get('M1_monthly',2), 2)} t/h"
                )
            if map_control.get("apt_base_flow"):
                self.label_apt_jzgj.setText(
                    f"{round(map_control.get('apt_base_flow',2), 2)} m3/h"
                )
            
            # 更新Tab2数据
            self.update_tab2_data(data, map_control)
        except Exception as e:
            print(f"更新塔数据出错: {str(e)}")
            traceback.print_exc()

    def update_tab2_data(self, data, map_control):
        """更新Tab2数据"""
        try:
            if map_control.get("xstjy_PH"):
                self.tab2_tower1_ph_val.setText(
                    f"{round(map_control.get('xstjy_PH'), 2)}"
                )
            self.tab2_yyq_so2_val.setText(f"{map_control.get('jyq_SO2')}mg/m3")
            if map_control.get("aptjy_PH"):
                self.tab2_tower2_ph_val.setText(
                    f"{round(map_control.get('aptjy_PH'), 2)}"
                )
            self.tab2_tower2_jyq_so2_val.setText(f"{round(map_control.get('jyq_SO2'), 2)}mg/m3")
            
            # 吸收塔PH建议值处理
            xst_ph = map_control.get("suggested_xst_ph")
            xst_ph_text = f"建议{round(xst_ph, 2) if xst_ph is not None else 0.00}"
            self.tab2_tower1_ph_adv.setText(xst_ph_text)
            
            # APT塔PH建议值处理
            apt_ph = map_control.get("suggested_apt_ph")
            apt_ph_text = f"建议{round(apt_ph, 2) if apt_ph is not None else 0.00}"
            self.tab2_tower2_ph_adv.setText(apt_ph_text)
        except Exception as e:
            print(f"更新Tab2数据出错: {str(e)}")
            traceback.print_exc()

    def update_pump_status(self, data, map_control):
        """更新泵状态显示"""
        try:
            tower1_bump_list = self.gui_config["tower"]["1"]["bump"]
            tower2_bump_list = self.gui_config["tower"]["2"]["bump"]
            
            # 获取推荐泵状态
            recommended_pump = map_control.get("recommended_pump")
            bump_total = len(tower1_bump_list) + len(tower2_bump_list)
            
            # 处理None值情况，使用当前实际泵状态作为建议值
            if recommended_pump is None:
                # 从combined_pump_status获取当前泵状态或构建
                combined_pump_status = map_control.get("combined_pump_status")
                if combined_pump_status:
                    recommended_pump = combined_pump_status
                else:
                    # 构建泵状态字符串
                    pump_status_list = []
                    # 添加一级塔和二级塔泵状态
                    for item in tower1_bump_list + tower2_bump_list:
                        code = item["code"]
                        status = "1" if map_control.get(code, 0) >= 30 else "0"
                        pump_status_list.append(status)
                    recommended_pump = "-".join(pump_status_list)
            
            # 检查泵状态是否发生变化
            current_tower1_pump_status = {}
            current_tower2_pump_status = {}
            
            # 获取当前泵状态
            for item in tower1_bump_list:
                code = item["code"]
                current_tower1_pump_status[code] = "on" if map_control.get(code, 0) >= 30 else "off"
            
            for item in tower2_bump_list:
                code = item["code"]
                current_tower2_pump_status[code] = "on" if map_control.get(code, 0) >= 30 else "off"
            
            # 检查是否需要更新泵状态显示
            tower1_changed = (current_tower1_pump_status != self.prev_tower1_pump_status)
            tower2_changed = (current_tower2_pump_status != self.prev_tower2_pump_status)
            recommendation_changed = (recommended_pump != self.prev_recommended_pump)
            
            # 如果泵状态或建议没有变化，则不更新显示
            if not (tower1_changed or tower2_changed or recommendation_changed):
                return
                
            # 保存当前状态供下次比较
            self.prev_tower1_pump_status = current_tower1_pump_status.copy()
            self.prev_tower2_pump_status = current_tower2_pump_status.copy()
            self.prev_recommended_pump = recommended_pump
            
            # 清除并重新创建泵显示
            self.clear_pump(self.tab1_tower1_pump_list_layout)
            self.clear_pump(self.tab1_tower2_pump_list_layout)
            self.clear_pump(self.tab2_tower1_pump_list_layout)
            self.clear_pump(self.tab2_tower2_pump_list_layout)
            
            # 将推荐泵状态字符串转换为列表
            pump_status = recommended_pump.split("-")
            if len(pump_status) < bump_total:
                pump_status = pump_status + ["0"] * (bump_total - len(pump_status))
            # 添加一级塔泵 - 首页
            for i, item in enumerate(tower1_bump_list):
                name = item["name"]
                code = item["code"]
                real_val = "on" if map_control.get(code) >= 30 else "off"
                self.add_simple_pump(self.tab1_tower1_pump_list_layout, name, real_val)
            
            # 添加二级塔泵 - 首页
            for i, item in enumerate(tower2_bump_list):
                name = item["name"]
                code = item["code"]
                real_val = "on" if map_control.get(code) >= 30 else "off"
                self.add_simple_pump(self.tab1_tower2_pump_list_layout, name, real_val)
            
            # 添加一级塔泵 - Tab2
            t1_idx = 0
            for item in tower1_bump_list:
                name = item["name"]
                code = item["code"]
                real_val = "on" if map_control.get(code) >= 30 else "off"
                
                adv_val = real_val
                if recommended_pump:
                    adv_val_raw = pump_status[t1_idx]
                    adv_val = "on" if adv_val_raw == "1" else "off"
                
                self.add_pump(self.tab2_tower1_pump_list_layout, name, real_val, adv_val)
                t1_idx += 1
            
            # 添加二级塔泵 - Tab2
            t2_idx = len(tower1_bump_list)
            for item in tower2_bump_list:
                name = item["name"]
                code = item["code"]
                real_val = "on" if map_control.get(code) >= 30 else "off"
                
                adv_val = real_val
                if recommended_pump:
                    adv_val_raw = pump_status[t2_idx]
                    adv_val = "on" if adv_val_raw == "1" else "off"
                
                self.add_pump(self.tab2_tower2_pump_list_layout, name, real_val, adv_val)
                t2_idx += 1
            
            # 更新泵组合建议信息
            self.update_pump_advice(data, pump_status, tower1_bump_list, tower2_bump_list)
            
            print("泵状态已更新")  # 添加日志，便于调试
        except Exception as e:
            print(f"更新泵状态出错: {str(e)}")
            traceback.print_exc()
    def update_pump_advice(self, data, pump_status, tower1_bump_list, tower2_bump_list):
        """更新泵组合建议信息"""
        try:
            # 获取当前运行状态 - 一级塔
            tower1_pump_curr = []
            for item in tower1_bump_list:
                code = item["code"]
                tower1_pump_curr.append(1 if data.get(code, 0) >= 30 else 0)
            
            # 获取建议状态 - 一级塔
            tower1_pump_adv = [int(pump_status[i]) for i in range(len(tower1_bump_list))]
            
            # 比较建议状态和当前状态 - 一级塔
            if tower1_pump_curr == tower1_pump_adv:
                self.label_tower1_adv_msg.setText("维持当前泵组合方式")
            else:
                self.label_tower1_adv_msg.setText("建议切换循环泵运行方式")
            
            # 获取当前运行状态 - 二级塔
            tower2_pump_curr = []
            for item in tower2_bump_list:
                code = item["code"]
                tower2_pump_curr.append(1 if data.get(code, 0) >= 30 else 0)
            
            # 获取建议状态 - 二级塔
            tower2_pump_adv = [int(pump_status[i + len(tower1_bump_list)]) for i in range(len(tower2_bump_list))]
            
            # 比较建议状态和当前状态 - 二级塔
            if tower2_pump_curr == tower2_pump_adv:
                self.label_tower2_adv_msg.setText("维持当前泵组合方式")
            else:
                self.label_tower2_adv_msg.setText("建议切换循环泵运行方式")
        except Exception as e:
            print(f"更新泵建议信息出错: {str(e)}")
            traceback.print_exc()

    def update_tab3_data(self, data, map_control):
        """更新Tab3(成本统计)数据"""
        try:
            self.tab3_xsdcb.setText(f"{round(map_control.get('M_elec_hour', 0), 2)} 元")
            self.tab3_so2_pfnse.setText(f"{round(map_control.get('M_pollute', 0), 2)} 元")
            self.tab3_xsshscb.setText(f"{round(map_control.get('M_stone_primary', 0), 2)} 元")
            self.tab3_xsscb.setText(f"{round(map_control.get('M_stone_secondary', 0), 2)} 元")
            self.tab3_sgxssr.setText(f"{round(map_control.get('M_gypsum', 0), 2)} 元")

            self.tab3_tower1_jyxhb.setText(f"{round(map_control.get('xstjyxhb_zdh', 0), 2)} kW")
            self.tab3_tower1_yhfj.setText(f"{round(map_control.get('xstyhfj_zdh', 0), 2)} kW")
            self.tab3_tower1_jbj.setText(f"{round(map_control.get('xstjbq_zdh', 0), 2)} kW")
            self.tab3_tower1_sgpcb.setText(f"{round(map_control.get('xstsgpcb_zdh', 0), 2)} kW")
            self.tab3_tower1_gj.setText(f"{round(map_control.get('xstgjb_zdh', 0), 2)} kW")
            self.tab3_tower1_gyjt.setText(f"{round(map_control.get('xstsmj_zdh', 0), 2)} kW")

            self.tab3_tower2_jyxhb.setText(f"{round(map_control.get('aptjyxhb_zdh', 0), 2)} kW")
            self.tab3_tower2_yhfj.setText(f"{round(map_control.get('aptyhfj_zdh', 0), 2)} kW")
            self.tab3_tower2_jbj.setText(f"{round(map_control.get('aptjbq_zdh', 0), 2)} kW")
            self.tab3_tower2_sgpcb.setText(f"{round(map_control.get('aptsgpcb_zdh', 0), 2)} kW")
            self.tab3_tower2_gj.setText(f"{round(map_control.get('aptgjb_zdh', 0), 2)} kW")
            self.tab3_tower2_gyjt.setText(f"{round(map_control.get('aptsmj_zdh', 0), 2)} kW")
        except Exception as e:
            print(f"更新Tab3数据出错: {str(e)}")
            traceback.print_exc()

    def update_tab4_tables(self, map_control):
        """更新Tab4(实时控制性能评估)表格数据"""
        try:
            tower1_ph_table_data = [
                [
                    "均值",
                    str(map_control.get("xst_ph_4h_mean", "")),
                    str(map_control.get("xst_ph_8h_mean", "")),
                    str(map_control.get("xst_ph_1d_mean", "")),
                    str(map_control.get("xst_ph_7d_mean", "")),
                ],
                [
                    "标准方差",
                    str(map_control.get("xst_ph_4h_std", "")),
                    str(map_control.get("xst_ph_8h_std", "")),
                    str(map_control.get("xst_ph_1d_std", "")),
                    str(map_control.get("xst_ph_7d_std", "")),
                ],
            ]
            
            tower2_ph_table_data = [
                [
                    "均值",
                    str(map_control.get("apt_ph_4h_mean", "")),
                    str(map_control.get("apt_ph_8h_mean", "")),
                    str(map_control.get("apt_ph_1d_mean", "")),
                    str(map_control.get("apt_ph_7d_mean", "")),
                ],
                [
                    "标准方差",
                    str(map_control.get("apt_ph_4h_std", "")),
                    str(map_control.get("apt_ph_8h_std", "")),
                    str(map_control.get("apt_ph_1d_std", "")),
                    str(map_control.get("apt_ph_7d_std", "")),
                ],
            ]
            
            jyq_so2_table_data = [
                [
                    "均值",
                    str(map_control.get("so2_4h_mean", "")),
                    str(map_control.get("so2_8h_mean", "")),
                    str(map_control.get("so2_1d_mean", "")),
                    str(map_control.get("so2_7d_mean", "")),
                ],
                [
                    "标准方差",
                    str(map_control.get("so2_4h_std", "")),
                    str(map_control.get("so2_8h_std", "")),
                    str(map_control.get("so2_1d_std", "")),
                    str(map_control.get("so2_7d_std", "")),
                ],
            ]

            self.update_table(tower1_ph_table_data, self.tower1_ph_table)
            self.update_table(tower2_ph_table_data, self.tower2_ph_table)
            self.update_table(jyq_so2_table_data, self.jyq_so2_table)
        except Exception as e:
            print(f"更新Tab4表格数据出错: {str(e)}")
            traceback.print_exc()
    def update_jzfh_chart_with_data(self, x_data, y_data, so2_data):
        """使用预处理数据更新机组负荷图表"""
        try:
            if not x_data or not y_data:
                return

            # 仅当当前标签页是首页时才进行绘制
            if self.current_tab == 0:
                # 清空之前的绘图
                self.jzfh_chart_ax.clear()
                if hasattr(self, "ax2"):
                    self.ax2.remove()
                self.ax2 = self.jzfh_chart_ax.twinx()

                # 设置当天的起始时间和结束时间
                current_time = datetime.now()
                day_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
                day_end = day_start + timedelta(days=1)

                # 绘制机组负荷折线图（数据已在后台线程中过滤为当天数据）
                if x_data and any(val is not None for val in y_data):
                    self.jzfh_chart_ax.plot(
                        x_data, y_data,
                        linestyle="-", linewidth=0.5,
                        color="red", label="今日负荷",
                        drawstyle='steps-post'
                    )
                
                # 处理原烟气SO2数据
                if so2_data and any(val is not None for val in so2_data):
                    valid_indices = [i for i, val in enumerate(so2_data) if val is not None]
                    if valid_indices:
                        x_valid = [x_data[i] for i in valid_indices]
                        so2_valid = [so2_data[i] for i in valid_indices]
                        self.ax2.plot(
                            x_valid, so2_valid,
                            linestyle="--", linewidth=0.5,
                            color="green", label="净烟气 SO2",
                            drawstyle='steps-post'
                        )

                # 固定x轴范围为当天完整24小时
                self.jzfh_chart_ax.set_xlim(day_start, day_end)

                # 设置3小时间隔的主刻度
                self.jzfh_chart_ax.xaxis.set_major_locator(mdates.HourLocator(interval=3))
                self.jzfh_chart_ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
                
                # 设置1小时间隔的次要刻度
                self.jzfh_chart_ax.xaxis.set_minor_locator(mdates.HourLocator(interval=1))
                
                # 添加网格线
                self.jzfh_chart_ax.grid(True, which='major', linestyle='-', alpha=0.3)
                self.jzfh_chart_ax.grid(True, which='minor', linestyle=':', alpha=0.1)

                # 设置固定的Y轴范围：0-650
                self.jzfh_chart_ax.set_ylim(0, 650)

                # 设置SO2的Y轴范围
                if so2_data and max([v for v in so2_data if v is not None], default=0) > 0:
                    so2_values = [v for v in so2_data if v is not None]
                    so2_min, so2_max = min(so2_values), max(so2_values)
                    padding = (so2_max - so2_min) * 0.05 if so2_max > so2_min else 1.0
                    self.ax2.set_ylim(max(0, so2_min - padding), so2_max + padding)
                else:
                    self.ax2.set_ylim(0, 100)

                # 设置样式
                self.jzfh_chart_ax.tick_params(axis="x", colors="white", which='both')
                self.jzfh_chart_ax.tick_params(axis="y", colors="white")
                self.ax2.tick_params(axis="y", colors="green")

                # 显示图例
                lines1, labels1 = self.jzfh_chart_ax.get_legend_handles_labels()
                lines2, labels2 = self.ax2.get_legend_handles_labels()
                self.jzfh_chart_ax.legend(
                    lines1 + lines2,
                    labels1 + labels2,
                    loc="upper right",
                    fontsize=8,
                    facecolor="white"
                )

                # 使用draw_idle()提高性能
                self.jzfh_chart_canvas.draw_idle()

        except Exception as e:
            print(f"更新机组负荷图表出错: {e}")
            traceback.print_exc()
    def update_total_cost_chart_with_data(self, x_data, y_data):
        """使用预处理数据更新运行总成本图表"""
        try:
            if not x_data or not y_data:
                return

            # 清空之前的绘图
            self.ph1_chart_ax.clear()

            # 获取当前时间和4小时前的时间
            current_time = datetime.now()
            four_hours_ago = current_time - timedelta(hours=4)

            # 绘制实时值折线图
            self.ph1_chart_ax.plot(
                x_data, y_data,
                marker=None, linestyle="-", linewidth=0.5,
                color="red", label="运行总成本",
                zorder=1
            )

            # 设置固定的4小时窗口
            self.ph1_chart_ax.set_xlim(four_hours_ago, current_time)
            
            # 设置30分钟间隔的主刻度
            self.ph1_chart_ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=30))
            self.ph1_chart_ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

            # 设置Y轴范围
            if y_data:
                y_min, y_max = min(y_data), max(y_data)
                padding = (y_max - y_min) * 0.1
                self.ph1_chart_ax.set_ylim(max(0, y_min - padding), y_max + padding)

            # 设置样式
            self.ph1_chart_ax.tick_params(axis="x", colors="white")
            self.ph1_chart_ax.tick_params(axis="y", colors="white")
            self.ph1_chart_ax.grid(True, linestyle='--', alpha=0.3)

            # 显示图例
            self.ph1_chart_ax.legend(loc="upper right", fontsize=8, facecolor="white")

            # 刷新画布
            self.ph1_chart_canvas.draw_idle()

        except Exception as e:
            print(f"更新运行总成本图表出错: {e}")
            traceback.print_exc()

    def update_tower1_elec_chart_with_data(self, x_data, y_data):
        """使用预处理数据更新一级塔电耗图表"""
        try:
            if not x_data or not y_data:
                return

            # 清空之前的绘图
            self.ph2_chart_ax.clear()

            # 获取当前时间和4小时前的时间
            current_time = datetime.now()
            four_hours_ago = current_time - timedelta(hours=4)

            # 绘制实时值折线图
            self.ph2_chart_ax.plot(
                x_data, y_data,
                marker=None, linestyle="-", linewidth=0.5,
                color="red", label="运行总电耗",
                zorder=1
            )

            # 设置固定的4小时窗口
            self.ph2_chart_ax.set_xlim(four_hours_ago, current_time)
            
            # 设置30分钟间隔的主刻度
            self.ph2_chart_ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=30))
            self.ph2_chart_ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

            # 设置Y轴范围
            if y_data:
                y_min, y_max = min(y_data), max(y_data)
                padding = (y_max - y_min) * 0.1
                self.ph2_chart_ax.set_ylim(max(0, y_min - padding), y_max + padding)

            # 设置样式
            self.ph2_chart_ax.tick_params(axis="x", colors="white")
            self.ph2_chart_ax.tick_params(axis="y", colors="white")
            self.ph2_chart_ax.grid(True, linestyle='--', alpha=0.3)

            # 显示图例
            self.ph2_chart_ax.legend(loc="upper right", fontsize=8, facecolor="white")

            # 刷新画布
            self.ph2_chart_canvas.draw_idle()

        except Exception as e:
            print(f"更新一级塔电耗图表出错: {e}")
            traceback.print_exc()

    def update_tower2_elec_chart_with_data(self, x_data, y_data):
        """使用预处理数据更新二级塔电耗图表"""
        try:
            if not x_data or not y_data:
                return

            # 清空之前的绘图
            self.so2_chart_ax.clear()

            # 获取当前时间和4小时前的时间
            current_time = datetime.now()
            four_hours_ago = current_time - timedelta(hours=4)

            # 绘制实时值折线图
            self.so2_chart_ax.plot(
                x_data, y_data,
                marker=None, linestyle="-", linewidth=0.5,
                color="red", label="运行总电耗",
                zorder=1
            )

            # 设置固定的4小时窗口
            self.so2_chart_ax.set_xlim(four_hours_ago, current_time)
            
            # 设置30分钟间隔的主刻度
            self.so2_chart_ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=30))
            self.so2_chart_ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

            # 设置Y轴范围
            if y_data:
                y_min, y_max = min(y_data), max(y_data)
                padding = (y_max - y_min) * 0.1
                self.so2_chart_ax.set_ylim(max(0, y_min - padding), y_max + padding)

            # 设置样式
            self.so2_chart_ax.tick_params(axis="x", colors="white")
            self.so2_chart_ax.tick_params(axis="y", colors="white")
            self.so2_chart_ax.grid(True, linestyle='--', alpha=0.3)

            # 显示图例
            self.so2_chart_ax.legend(loc="upper right", fontsize=8, facecolor="white")

            # 刷新画布
            self.so2_chart_canvas.draw_idle()

        except Exception as e:
            print(f"更新二级塔电耗图表出错: {e}")
            traceback.print_exc()

    def update_tower1_ph_chart_with_data(self, x_data, y_data, mean_data):
        """使用预处理数据更新一级塔PH控制回路图表"""
        try:
            if not x_data or not y_data:
                return

            # 清空之前的绘图
            self.ph1_chart_ax_2.clear()

            # 获取当前时间和4小时前的时间
            current_time = datetime.now()
            four_hours_ago = current_time - timedelta(hours=4)

            # 绘制实时值折线图
            self.ph1_chart_ax_2.plot(
                x_data, y_data,
                marker=None, linestyle="-", linewidth=0.5,
                color="red", label="实时值", zorder=1
            )

            # 绘制均值折线图
            if mean_data and any(val is not None for val in mean_data):
                self.ph1_chart_ax_2.plot(
                    x_data, mean_data,
                    marker=None, linestyle="--", linewidth=0.5,
                    color="green", label="均值", zorder=2
                )

            # 设置固定的4小时窗口
            self.ph1_chart_ax_2.set_xlim(four_hours_ago, current_time)
            
            # 设置30分钟间隔的主刻度
            self.ph1_chart_ax_2.xaxis.set_major_locator(mdates.MinuteLocator(interval=30))
            self.ph1_chart_ax_2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

            # 设置Y轴范围
            if y_data and mean_data:
                y_min = min(min(y_data), min([v for v in mean_data if v is not None]))
                y_max = max(max(y_data), max([v for v in mean_data if v is not None]))
                padding = (y_max - y_min) * 0.1
                self.ph1_chart_ax_2.set_ylim(max(0, y_min - padding), y_max + padding)

            # 设置样式
            self.ph1_chart_ax_2.tick_params(axis="x", colors="white")
            self.ph1_chart_ax_2.tick_params(axis="y", colors="white")
            self.ph1_chart_ax_2.grid(True, linestyle='--', alpha=0.3)

            # 显示图例
            self.ph1_chart_ax_2.legend(loc="upper right", fontsize=8, facecolor="white")

            # 刷新画布
            self.ph1_chart_canvas_2.draw_idle()

        except Exception as e:
            print(f"更新一级塔PH控制回路图表出错: {e}")
            traceback.print_exc()

    def update_tower2_ph_chart_with_data(self, x_data, y_data, mean_data):
        """使用预处理数据更新二级塔PH控制回路图表"""
        try:
            if not x_data or not y_data:
                return

            # 清空之前的绘图
            self.ph2_chart_ax_2.clear()

            # 获取当前时间和4小时前的时间
            current_time = datetime.now()
            four_hours_ago = current_time - timedelta(hours=4)

            # 绘制实时值折线图
            self.ph2_chart_ax_2.plot(
                x_data, y_data,
                marker=None, linestyle="-", linewidth=0.5,
                color="red", label="实时值", zorder=1
            )

            # 绘制均值折线图
            if mean_data and any(val is not None for val in mean_data):
                self.ph2_chart_ax_2.plot(
                    x_data, mean_data,
                    marker=None, linestyle="--", linewidth=0.5,
                    color="green", label="均值", zorder=2
                )

            # 设置固定的4小时窗口
            self.ph2_chart_ax_2.set_xlim(four_hours_ago, current_time)
            
            # 设置30分钟间隔的主刻度
            self.ph2_chart_ax_2.xaxis.set_major_locator(mdates.MinuteLocator(interval=30))
            self.ph2_chart_ax_2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

            # 设置Y轴范围
            if y_data and mean_data:
                y_min = min(min(y_data), min([v for v in mean_data if v is not None]))
                y_max = max(max(y_data), max([v for v in mean_data if v is not None]))
                padding = (y_max - y_min) * 0.1
                self.ph2_chart_ax_2.set_ylim(max(0, y_min - padding), y_max + padding)

            # 设置样式
            self.ph2_chart_ax_2.tick_params(axis="x", colors="white")
            self.ph2_chart_ax_2.tick_params(axis="y", colors="white")
            self.ph2_chart_ax_2.grid(True, linestyle='--', alpha=0.3)

            # 显示图例
            self.ph2_chart_ax_2.legend(loc="upper right", fontsize=8, facecolor="white")

            # 刷新画布
            self.ph2_chart_canvas_2.draw_idle()

        except Exception as e:
            print(f"更新二级塔PH控制回路图表出错: {e}")
            traceback.print_exc()

    def update_jyq_so2_chart_with_data(self, x_data, y_data, mean_data):
        """使用预处理数据更新净烟气SO2控制回路图表"""
        try:
            if not x_data or not y_data:
                return

            # 清空之前的绘图
            self.so2_chart_ax_2.clear()

            # 获取当前时间和4小时前的时间
            current_time = datetime.now()
            four_hours_ago = current_time - timedelta(hours=4)

            # 绘制实时值折线图
            self.so2_chart_ax_2.plot(
                x_data, y_data,
                marker=None, linestyle="-", linewidth=0.5,
                color="red", label="实时值", zorder=1
            )

            # 绘制均值折线图
            if mean_data and any(val is not None for val in mean_data):
                self.so2_chart_ax_2.plot(
                    x_data, mean_data,
                    marker=None, linestyle="--", linewidth=0.5,
                    color="green", label="均值", zorder=2
                )

            # 设置固定的4小时窗口
            self.so2_chart_ax_2.set_xlim(four_hours_ago, current_time)
            
            # 设置30分钟间隔的主刻度
            self.so2_chart_ax_2.xaxis.set_major_locator(mdates.MinuteLocator(interval=30))
            self.so2_chart_ax_2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

            # 设置Y轴范围
            if y_data and mean_data:
                y_min = min(min(y_data), min([v for v in mean_data if v is not None]))
                y_max = max(max(y_data), max([v for v in mean_data if v is not None]))
                padding = (y_max - y_min) * 0.1
                self.so2_chart_ax_2.set_ylim(max(0, y_min - padding), y_max + padding)

            # 设置样式
            self.so2_chart_ax_2.tick_params(axis="x", colors="white")
            self.so2_chart_ax_2.tick_params(axis="y", colors="white")
            self.so2_chart_ax_2.grid(True, linestyle='--', alpha=0.3)

            # 显示图例
            self.so2_chart_ax_2.legend(loc="upper right", fontsize=8, facecolor="white")

            # 刷新画布
            self.so2_chart_canvas_2.draw_idle()

        except Exception as e:
            print(f"更新净烟气SO2控制回路图表出错: {e}")
            traceback.print_exc()

    def update_charts(self):
        """分组更新图表，每组20秒更新一次"""
        try:
            # 使用计数器确定当前应该更新哪组图表
            group = self.chart_update_counter % 3
            
            print(f"更新图表组 {group}")
            
            if group == 0:
                # 第一组：首页机组负荷图表
                jzfh_chart_data = self.data_thread.get_chart_data("jzfh_chart_data")
                self.chart_update_thread.add_chart_update("jzfh_chart_data", jzfh_chart_data)
                
            elif group == 1:
                # 第二组：Tab3成本统计图表
                tower1_ph_chart_data = self.data_thread.get_chart_data("tower1_ph_chart_data")
                tower2_ph_chart_data = self.data_thread.get_chart_data("tower2_ph_chart_data")
                jyq_so2_chart_data = self.data_thread.get_chart_data("jyq_so2_chart_data")
                
                self.chart_update_thread.add_chart_update("tower1_ph_chart_data", tower1_ph_chart_data)
                self.chart_update_thread.add_chart_update("tower2_ph_chart_data", tower2_ph_chart_data)
                self.chart_update_thread.add_chart_update("jyq_so2_chart_data", jyq_so2_chart_data)
                
            elif group == 2:
                # 第三组：Tab4实时控制性能评估图表
                tower1_ph_control_data = self.data_thread.get_chart_data("tower1_ph_control_data")
                tower2_ph_control_data = self.data_thread.get_chart_data("tower2_ph_control_data")
                so2_control_data = self.data_thread.get_chart_data("so2_control_data")
                
                self.chart_update_thread.add_chart_update("tower1_ph_control_data", tower1_ph_control_data)
                self.chart_update_thread.add_chart_update("tower2_ph_control_data", tower2_ph_control_data)
                self.chart_update_thread.add_chart_update("so2_control_data", so2_control_data)
                
            # 更新计数器
            self.chart_update_counter += 1
            
        except Exception as e:
            print(f"分组更新图表出错: {str(e)}")
            traceback.print_exc()
    def update_table(self, data: list, tab: QTableWidget):
        """更新表格数据"""
        try:
            if not data or not tab:
                return
                
            # 确保数据行数与表格行数匹配
            for row_index, row_data in enumerate(data):
                for col_index, col_value in enumerate(row_data):
                    # 创建或更新单元格
                    item = QTableWidgetItem(str(col_value))
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    tab.setItem(row_index, col_index, item)
                    
        except Exception as e:
            print(f"更新表格出错: {str(e)}")
            traceback.print_exc()
    
    
    def showTab1(self):
        """切换到首页"""
        self.current_tab = 0
        self.tab1_content.setVisible(True)
        self.tab2_content.setVisible(False)
        self.tab3_content.setVisible(False)
        self.tab3_content_2.setVisible(False)

        self.btn_home.setStyleSheet("background-color:DodgerBlue;color:black;")
        self.btn_adv.setStyleSheet("background-color:transparent;color:white;")
        self.btn_ctl.setStyleSheet("background-color:transparent;color:white;")
        self.btn_ctl_2.setStyleSheet("background-color:transparent;color:white;")

    def showTab2(self):
        """切换到寻优建议页"""
        self.current_tab = 1
        self.tab1_content.setVisible(False)
        self.tab2_content.setVisible(True)
        self.tab3_content.setVisible(False)
        self.tab3_content_2.setVisible(False)

        self.btn_home.setStyleSheet("background-color:transparent;color:white;")
        self.btn_adv.setStyleSheet("background-color:DodgerBlue;color:black;")
        self.btn_ctl.setStyleSheet("background-color:transparent;color:white;")
        self.btn_ctl_2.setStyleSheet("background-color:transparent;color:white;")

    def showTab3(self):
        """切换到运行成本统计页"""
        self.current_tab = 2
        self.tab1_content.setVisible(False)
        self.tab2_content.setVisible(False)
        self.tab3_content.setVisible(True)
        self.tab3_content_2.setVisible(False)

        self.btn_home.setStyleSheet("background-color:transparent;color:white;")
        self.btn_adv.setStyleSheet("background-color:transparent;color:white;")
        self.btn_ctl.setStyleSheet("background-color:DodgerBlue;color:black;")
        self.btn_ctl_2.setStyleSheet("background-color:transparent;color:white;")

    def showTab4(self):
        """切换到实时控制性能评估页"""
        self.current_tab = 3
        self.tab1_content.setVisible(False)
        self.tab2_content.setVisible(False)
        self.tab3_content.setVisible(False)
        self.tab3_content_2.setVisible(True)

        self.btn_home.setStyleSheet("background-color:transparent;color:white;")
        self.btn_adv.setStyleSheet("background-color:transparent;color:white;")
        self.btn_ctl.setStyleSheet("background-color:transparent;color:white;")
        self.btn_ctl_2.setStyleSheet("background-color:DodgerBlue;color:black;")
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

    def add_simple_pump(self, pObj, name, status):
        """为首页添加简化版的泵显示"""
        f = QFrame()
        f.setFixedSize(80, 100)  # 设置合适的尺寸
        f.setStyleSheet("background: none;")
        
        f_l = QVBoxLayout(f)
        f_l.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        f_l.setContentsMargins(2, 2, 2, 2)
        f_l.setSpacing(2)
        
        # 泵图标
        l1 = QLabel(f)
        l1.setAlignment(Qt.AlignmentFlag.AlignCenter)
        l1.setFixedSize(68, 68)
        l1.setStyleSheet(
            f"background-image:url({image_url(f'bump_{status}.png')});"
        )
        f_l.addWidget(l1)
        
        # 泵名称标签
        l2 = QLabel(f)
        l2.setText(name)
        l2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        l2.setStyleSheet("background:none; color:white;")
        f_l.addWidget(l2)
        
        pObj.addWidget(f)

    def add_pump(self, pObj, name, real_val, adv_val):
        """添加泵显示（用于寻优建议页）"""
        layoutxxx = QHBoxLayout()
        layoutxxx.setSpacing(15)
        layoutxxx.setContentsMargins(20, 20, 20, 20)

        f = QFrame()
        f.setFixedSize(120, 248)
        f.setStyleSheet(
            f"background-image:url({image_url('bump_group.png')});"
        )

        f_l = QVBoxLayout(f)
        f_l.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        l1 = QLabel(f)
        l1.setAlignment(Qt.AlignmentFlag.AlignCenter)
        l1.setFixedSize(68, 68)
        l1.setStyleSheet(
            f"background-image:url({image_url(f'bump_{real_val}.png')});"
        )
        f_l.addWidget(l1)

        l2 = QLabel(f)
        l2.setText("开启" if real_val == "on" else "关闭")
        l2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        l2.setFixedSize(68, 68)
        l2.setStyleSheet("background:none;")
        f_l.addWidget(l2)

        l3 = QLabel(f)
        l3.setAlignment(Qt.AlignmentFlag.AlignCenter)
        l3.setFixedSize(68, 68)
        l3.setStyleSheet(
            f"background-image:url({image_url(f'bump_{adv_val}.png')});"
        )
        f_l.addWidget(l3)

        l4 = QLabel(f)
        l4.setText("开启" if adv_val == "on" else "关闭")
        l4.setAlignment(Qt.AlignmentFlag.AlignCenter)
        l4.setFixedSize(68, 68)
        l4.setStyleSheet("background:none;")
        f_l.addWidget(l4)

        l5 = QLabel(f)
        l5.setText(name)
        l5.setAlignment(Qt.AlignmentFlag.AlignCenter)
        l5.setFixedSize(68, 68)
        l5.setStyleSheet("background:none;")
        f_l.addWidget(l5)

        layoutxxx.addWidget(f)
        pObj.addLayout(layoutxxx)
    
    # 3. 修改图表绘制方法，实现固定4小时窗口和30分钟间隔

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
