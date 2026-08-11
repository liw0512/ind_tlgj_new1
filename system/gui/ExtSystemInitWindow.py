import sys
import json
from PyQt5.QtWidgets import QApplication, QMainWindow, QMessageBox, QTableWidgetItem, QWidget
from PyQt5.QtCore import Qt
import subprocess
import os

from system.gui.base.SystemInitRootWindow import Ui_SystemInit
from system.gui.ExtPumpDialogWindow import ExtPumpDialogWindow


class SystemInitWindow(QWidget, Ui_SystemInit):
    def __init__(self):
        super(SystemInitWindow, self).__init__()
        self.setupUi(self)

        # 设置窗口标题
        self.setWindowTitle('系统初始化')

        # 初始化当前页面索引
        self.current_page = 0

        # 初始化数据存储
        self.system_config = {
            'mode': 'single',  # single or double
            'type': 'boye',  # boye or flow
            'communication': {
                'mode': 'modbusRTU',  # modbusRTU or modbusTCP
                'role': 'Master',  # Master or Slave
                'port': '10800',
                'baudrate': '9600',
                'parity': '无',
                'stopbits': '1',
                'databits': '8'
            },
            'primary_range': {},
            'secondary_range': {},
            'primary_pumps': [],
            'secondary_pumps': [],
            'addresses': {}
        }

        # 连接按钮事件
        self.connect_buttons()

        # 初始化表格
        self.init_tables()

        # 加载保存的配置
        self.load_saved_config()

        # 更新配置显示
        self.update_config_display()

    def load_saved_config(self):
        """加载保存的配置并更新界面"""
        try:
            with open('settings-defaults.json', 'r', encoding='utf-8') as f:
                settings = json.load(f)

            # 设置运行模式
            if settings.get('gui', {}).get('tower') == '2':
                self.p1_tower_double.setChecked(True)
                self.system_config['mode'] = 'double'
            else:
                self.p1_tower_single.setChecked(True)
                self.system_config['mode'] = 'single'

            # 设置系统类型
            if settings.get('gui', {}).get('control_type') == 'flow':
                self.p2_type_flow.setChecked(True)
                self.system_config['type'] = 'flow'
            else:
                self.p2_type_boye.setChecked(True)
                self.system_config['type'] = 'boye'

            # 设置通信配置
            comm = settings.get('communication', {})
            if comm.get('mode') == 'modbusTCP':
                self.radioButton_modbusTCP.setChecked(True)
            else:
                self.radioButton_modbusRTU.setChecked(True)

            self.comboBox_role.setCurrentText(comm.get('role', 'Master'))
            self.lineEdit_port.setText(comm.get('port', '10800'))
            self.comboBox_baudrate.setCurrentText(comm.get('baudrate', '9600'))
            self.comboBox_parity.setCurrentText(comm.get('parity', '无'))
            self.lineEdit_stopbits.setText(comm.get('stopbits', '1'))
            self.lineEdit_databits.setText(comm.get('databits', '8'))

            # 设置一级塔参数
            primary = settings.get('primary_tower', {})

            # 设置取值范围
            self.set_spinbox_values(primary.get('load', {}), self.spinBox_load_min, self.spinBox_load_max)
            self.set_spinbox_values(primary.get('ph', {}), self.spinBox_ph_min, self.spinBox_ph_max)
            self.set_spinbox_values(primary.get('so2', {}), self.spinBox_so2_min, self.spinBox_so2_max)
            self.set_spinbox_values(primary.get('oxidation_air', {}), self.spinBox_oxidation_air_min,
                                    self.spinBox_oxidation_air_max)
            self.set_spinbox_values(primary.get('slurry_flow', {}), self.spinBox_slurry_flow_min,
                                    self.spinBox_slurry_flow_max)
            self.set_spinbox_values(primary.get('liquid_level', {}), self.spinBox_liquid_level_min,
                                    self.spinBox_liquid_level_max)
            self.set_spinbox_values(primary.get('limestone_density', {}), self.spinBox_limestone_density_min,
                                    self.spinBox_limestone_density_max)
            self.set_spinbox_values(primary.get('inlet_gas', {}), self.spinBox_inlet_gas_min,
                                    self.spinBox_inlet_gas_max)
            self.set_spinbox_values(primary.get('outlet_gas', {}), self.spinBox_outlet_gas_min,
                                    self.spinBox_outlet_gas_max)

            # 设置推荐值
            recommended = primary.get('recommended', {})
            self.set_spinbox_values(recommended.get('liquid_gas_ratio', {}), self.spinBox_liquid_gas_ratio_min,
                                    self.spinBox_liquid_gas_ratio_max)
            self.set_spinbox_values(recommended.get('ph', {}), self.spinBox_ph_recommended_min,
                                    self.spinBox_ph_recommended_max)
            self.set_spinbox_values(recommended.get('ca_s_ratio', {}), self.spinBox_ca_s_ratio_min,
                                    self.spinBox_ca_s_ratio_max)

            # 设置地址
            addresses = primary.get('addresses', {})
            self.lineEdit_load_address.setText(addresses.get('load', '0'))
            self.lineEdit_ph_address.setText(addresses.get('ph', '0'))
            self.lineEdit_raw_so2_address.setText(addresses.get('so2', '0'))
            self.lineEdit_oxidation_air_address.setText(addresses.get('oxidation_air', '0'))
            self.lineEdit_slurry_flow_address.setText(addresses.get('slurry_flow', '0'))
            self.lineEdit_liquid_level_address.setText(addresses.get('liquid_level', '0'))
            self.lineEdit_limestone_density_address.setText(addresses.get('limestone_density', '0'))
            self.lineEdit_inlet_gas_address.setText(addresses.get('inlet_gas', '0'))
            self.lineEdit_outlet_gas_address.setText(addresses.get('outlet_gas', '0'))

            # 设置电流地址
            current = addresses.get('current', {})
            self.lineEdit_current_a.setText(current.get('pump_a', '0'))
            self.lineEdit_current_b.setText(current.get('pump_b', '0'))
            self.lineEdit_current_c.setText(current.get('pump_c', '0'))
            self.lineEdit_current_d.setText(current.get('pump_d', '0'))

            # 设置二级塔参数（如果是双塔模式）
            if self.system_config['mode'] == 'double':
                secondary = settings.get('secondary_tower', {})

                # 设置取值范围
                self.set_spinbox_values(secondary.get('ph', {}), self.spinBox_ph_secondary_min,
                                        self.spinBox_ph_secondary_max)
                self.set_spinbox_values(secondary.get('so2_connection', {}), self.spinBox_so2_connection_min,
                                        self.spinBox_so2_connection_max)
                self.set_spinbox_values(secondary.get('so2_clean', {}), self.spinBox_so2_clean_min,
                                        self.spinBox_so2_clean_max)
                self.set_spinbox_values(secondary.get('oxidation_air', {}), self.spinBox_oxidation_air_secondary_min,
                                        self.spinBox_oxidation_air_secondary_max)
                self.set_spinbox_values(secondary.get('slurry_flow', {}), self.spinBox_slurry_flow_secondary_min,
                                        self.spinBox_slurry_flow_secondary_max)
                self.set_spinbox_values(secondary.get('liquid_level', {}), self.spinBox_liquid_level_secondary_min,
                                        self.spinBox_liquid_level_secondary_max)
                self.set_spinbox_values(secondary.get('limestone_density', {}),
                                        self.spinBox_limestone_density_secondary_min,
                                        self.spinBox_limestone_density_secondary_max)
                self.set_spinbox_values(secondary.get('inlet_pressure', {}), self.spinBox_inlet_pressure_min,
                                        self.spinBox_inlet_pressure_max)
                self.set_spinbox_values(secondary.get('outlet_pressure', {}), self.spinBox_outlet_pressure_min,
                                        self.spinBox_outlet_pressure_max)

                # 设置推荐值
                recommended = secondary.get('recommended', {})
                self.set_spinbox_values(recommended.get('liquid_gas_ratio', {}),
                                        self.spinBox_liquid_gas_ratio_secondary_min,
                                        self.spinBox_liquid_gas_ratio_secondary_max)
                self.set_spinbox_values(recommended.get('ph', {}), self.spinBox_ph_recommended_secondary_min,
                                        self.spinBox_ph_recommended_secondary_max)
                self.set_spinbox_values(recommended.get('ca_s_ratio', {}), self.spinBox_ca_s_ratio_secondary_min,
                                        self.spinBox_ca_s_ratio_secondary_max)

                # 设置地址
                addresses = secondary.get('addresses', {})
                self.lineEdit_ph_secondary_address.setText(addresses.get('ph', '0'))
                self.lineEdit_connection_so2_address.setText(addresses.get('so2_connection', '0'))
                self.lineEdit_clean_so2_address.setText(addresses.get('so2_clean', '0'))
                self.lineEdit_oxidation_air_secondary_address.setText(addresses.get('oxidation_air', '0'))
                self.lineEdit_slurry_flow_secondary_address.setText(addresses.get('slurry_flow', '0'))
                self.lineEdit_liquid_level_secondary_address.setText(addresses.get('liquid_level', '0'))
                self.lineEdit_limestone_density_secondary_address.setText(addresses.get('limestone_density', '0'))
                self.lineEdit_inlet_pressure_address.setText(addresses.get('inlet_pressure', '0'))
                self.lineEdit_outlet_pressure_address.setText(addresses.get('outlet_pressure', '0'))

                # 设置电流地址
                current = addresses.get('current', {})
                self.lineEdit_current_secondary_a.setText(current.get('pump_a', '0'))
                self.lineEdit_current_secondary_b.setText(current.get('pump_b', '0'))
                self.lineEdit_current_secondary_c.setText(current.get('pump_c', '0'))
                self.lineEdit_current_secondary_d.setText(current.get('pump_d', '0'))

            # 加载循环泵配置
            pumps = settings.get('pumps', {})

            # 加载一级塔循环泵
            primary_pumps = pumps.get('primary_pumps', [])
            for pump in primary_pumps:
                row = self.tableWidget_primary.rowCount()
                self.tableWidget_primary.insertRow(row)
                self.tableWidget_primary.setItem(row, 0, QTableWidgetItem(str(pump.get('name', ''))))
                self.tableWidget_primary.setItem(row, 1, QTableWidgetItem(str(pump.get('power', ''))))
                self.tableWidget_primary.setItem(row, 2, QTableWidgetItem(str(pump.get('flow', ''))))
                self.tableWidget_primary.setItem(row, 3, QTableWidgetItem(str(pump.get('head', ''))))
                self.tableWidget_primary.setItem(row, 4, QTableWidgetItem(str(pump.get('current_range', ''))))

            # 加载二级塔循环泵（如果是双塔模式）
            if self.system_config['mode'] == 'double':
                secondary_pumps = pumps.get('secondary_pumps', [])
                for pump in secondary_pumps:
                    row = self.tableWidget_secondary.rowCount()
                    self.tableWidget_secondary.insertRow(row)
                    self.tableWidget_secondary.setItem(row, 0, QTableWidgetItem(str(pump.get('name', ''))))
                    self.tableWidget_secondary.setItem(row, 1, QTableWidgetItem(str(pump.get('power', ''))))
                    self.tableWidget_secondary.setItem(row, 2, QTableWidgetItem(str(pump.get('flow', ''))))
                    self.tableWidget_secondary.setItem(row, 3, QTableWidgetItem(str(pump.get('head', ''))))
                    self.tableWidget_secondary.setItem(row, 4, QTableWidgetItem(str(pump.get('current_range', ''))))

        except FileNotFoundError:
            # 如果配置文件不存在，使用默认值
            pass
        except Exception as e:
            QMessageBox.warning(self, "警告", f"加载配置文件失败：{str(e)}")

    def set_spinbox_values(self, value_dict, min_spinbox, max_spinbox):
        """设置SpinBox的最小值和最大值"""
        if value_dict:
            try:
                if 'min' in value_dict:
                    # 确保转换为整数类型
                    min_value = int(float(str(value_dict['min'])))
                    min_spinbox.setValue(min_value)
                if 'max' in value_dict:
                    # 确保转换为整数类型
                    max_value = int(float(str(value_dict['max'])))
                    max_spinbox.setValue(max_value)
            except (ValueError, TypeError) as e:
                print(f"值转换错误: {e}")
                # 发生错误时使用默认值
                min_spinbox.setValue(0)
                max_spinbox.setValue(999999)

    def connect_buttons(self):
        """连接所有按钮事件"""
        # 连接第一页按钮事件
        self.pushButton_next_1.clicked.connect(self.next_page)
        self.pushButton_exit_1.clicked.connect(self.exit_system)
        self.p1_tower_single.toggled.connect(lambda checked: self.update_mode('single' if checked else 'double'))

        # 连接第二页按钮事件
        self.pushButton_next_2.clicked.connect(self.next_page)
        self.pushButton_prev_2.clicked.connect(self.prev_page)
        self.pushButton_exit_2.clicked.connect(self.exit_system)
        self.p2_type_boye.toggled.connect(lambda checked: self.update_type('boye' if checked else 'flow'))

        # 连接第三页按钮事件
        self.pushButton_next_3.clicked.connect(self.next_page)
        self.pushButton_prev_3.clicked.connect(self.prev_page)
        self.pushButton_exit_3.clicked.connect(self.exit_system)
        self.pushButton_test.clicked.connect(self.test_connection)
        self.radioButton_modbusRTU.toggled.connect(
            lambda checked: self.update_comm_mode('modbusRTU' if checked else 'modbusTCP'))

        # 连接第四页按钮事件
        self.pushButton_next_4.clicked.connect(self.next_page)
        self.pushButton_prev_4.clicked.connect(self.prev_page)
        self.pushButton_exit_4.clicked.connect(self.exit_system)

        # 连接第五页按钮事件
        self.pushButton_next_5.clicked.connect(self.next_page)
        self.pushButton_prev_5.clicked.connect(self.prev_page)
        self.pushButton_exit_5.clicked.connect(self.exit_system)
        self.pushButton_add_primary.clicked.connect(lambda: self.add_pump('primary'))
        self.pushButton_add_secondary.clicked.connect(lambda: self.add_pump('secondary'))

        # 连接第六页按钮事件
        self.pushButton_next_6.clicked.connect(self.next_page)
        self.pushButton_prev_6.clicked.connect(self.prev_page)
        self.pushButton_exit_6.clicked.connect(self.exit_system)

        # 连接第七页按钮事件
        self.pushButton_prev_7.clicked.connect(self.prev_page)
        self.pushButton_exit_7.clicked.connect(self.exit_system)
        self.pushButton_confirm.clicked.connect(self.confirm_setup)

    def next_page(self):
        """切换到下一页"""
        if self.current_page < self.stackedWidget.count() - 1:
            # 在第5页（循环泵设置页面）添加验证
            if self.current_page == 4:  # 当前在第5页
                if self.tableWidget_primary.rowCount() == 0:
                    QMessageBox.warning(self, "警告", "请至少添加一条一级塔循环泵记录！")
                    return
                if self.tableWidget_secondary.rowCount() == 0:
                    QMessageBox.warning(self, "警告", "请至少添加一条二级塔循环泵记录！")
                    return

            self.current_page += 1
            self.stackedWidget.setCurrentIndex(self.current_page)
            if self.current_page == 6:  # 最后一页，更新配置显示
                self.update_config_display()

    def prev_page(self):
        """切换到上一页"""
        if self.current_page > 0:
            self.current_page -= 1
            self.stackedWidget.setCurrentIndex(self.current_page)

    def exit_system(self):
        """退出系统"""
        reply = QMessageBox.question(self, '确认退出', '确定要退出系统吗？',
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            sys.exit()

    def test_connection(self):
        """测试连接"""
        # TODO: 实现连接测试逻辑
        QMessageBox.information(self, "测试结果", "连接测试成功！")

    def update_mode(self, mode):
        """更新运行模式"""
        self.system_config['mode'] = mode
        self.label_mode.setText(f"运行模式：{'单塔' if mode == 'single' else '双塔'}")

    def update_type(self, type_):
        """更新系统类型"""
        self.system_config['type'] = type_

    def update_comm_mode(self, mode):
        """更新通信模式"""
        self.system_config['communication']['mode'] = mode

    def init_tables(self):
        """初始化泵列表表格"""
        headers = ['泵名称', '功率(KW)', '循环流量(m³/h)', '扬程(mH₂O)', '电流范围 (A)']
        self.tableWidget_primary.setHorizontalHeaderLabels(headers)
        self.tableWidget_secondary.setHorizontalHeaderLabels(headers)

    def add_pump(self, pump_type):
        """添加泵"""
        dialog = ExtPumpDialogWindow(self)
        dialog.pump_data.connect(lambda data: self.handle_pump_data(data, pump_type))
        dialog.exec_()

    def handle_pump_data(self, data, pump_type):
        """处理泵数据"""
        # 选择对应的表格
        table = self.tableWidget_primary if pump_type == 'primary' else self.tableWidget_secondary

        # 添加新行
        row = table.rowCount()
        table.insertRow(row)

        # 设置表格数据
        table.setItem(row, 0, QTableWidgetItem(data['name']))
        table.setItem(row, 1, QTableWidgetItem(data['power']))
        table.setItem(row, 2, QTableWidgetItem(data['flow']))
        table.setItem(row, 3, QTableWidgetItem(data['head']))
        table.setItem(row, 4, QTableWidgetItem(data['current_range']))

        # 更新配置
        if pump_type == 'primary':
            self.system_config['primary_pumps'].append(data)
        else:
            self.system_config['secondary_pumps'].append(data)

    def update_config_display(self):
        """更新配置显示"""
        # 更新运行模式和通信方式显示
        mode_text = "单塔" if self.system_config['mode'] == 'single' else "双塔"
        type_text = "波液循环泵变频控制系统" if self.system_config['type'] == 'boye' else "供浆流量控制系统"
        self.label_mode.setText(f"运行模式：{mode_text}，系统类型：{type_text}")

        # 更新通信方式显示
        comm = self.system_config['communication']
        self.label_communication.setText(
            f"通信方式：{comm['mode']}，角色：{comm['role']}，端口：{comm['port']}，"
            f"波特率：{comm['baudrate']}，校验位：{comm['parity']}，"
            f"停止位：{comm['stopbits']}，数据位：{comm['databits']}"
        )

        # 更新取值范围显示
        range_text = "【一级塔取值范围设置】\n"
        range_text += f"机组负荷：{self.spinBox_load_min.value()} - {self.spinBox_load_max.value()} MW\n"
        range_text += f"PH值：{self.spinBox_ph_min.value()} - {self.spinBox_ph_max.value()}\n"
        range_text += f"原烟气SO2：{self.spinBox_so2_min.value()} - {self.spinBox_so2_max.value()} mg/Nm³\n"
        range_text += f"氧化风量：{self.spinBox_oxidation_air_min.value()} - {self.spinBox_oxidation_air_max.value()} mg/m3\n"
        range_text += f"供浆流量：{self.spinBox_slurry_flow_min.value()} - {self.spinBox_slurry_flow_max.value()} m³/h\n"
        range_text += f"液位高度：{self.spinBox_liquid_level_min.value()} - {self.spinBox_liquid_level_max.value()} m\n"
        range_text += f"石灰石浆液密度：{self.spinBox_limestone_density_min.value()} - {self.spinBox_limestone_density_max.value()} kg/m³\n"
        range_text += f"入口烟气量：{self.spinBox_inlet_gas_min.value()} - {self.spinBox_inlet_gas_max.value()} Nm³/h\n"
        range_text += f"出口烟气量：{self.spinBox_outlet_gas_min.value()} - {self.spinBox_outlet_gas_max.value()} Nm³/h\n"

        if self.system_config['mode'] == 'double':
            range_text += "\n【二级塔取值范围设置】\n"
            range_text += f"PH值：{self.spinBox_ph_secondary_min.value()} - {self.spinBox_ph_secondary_max.value()}\n"
            range_text += f"联络烟道SO2：{self.spinBox_so2_connection_min.value()} - {self.spinBox_so2_connection_max.value()} mg/Nm³\n"
            range_text += f"净烟气SO2：{self.spinBox_so2_clean_min.value()} - {self.spinBox_so2_clean_max.value()} mg/Nm³\n"
            range_text += f"氧化风量：{self.spinBox_oxidation_air_secondary_min.value()} - {self.spinBox_oxidation_air_secondary_max.value()} mg/m3\n"
            range_text += f"供浆流量：{self.spinBox_slurry_flow_secondary_min.value()} - {self.spinBox_slurry_flow_secondary_max.value()} m³/h\n"
            range_text += f"液位高度：{self.spinBox_liquid_level_secondary_min.value()} - {self.spinBox_liquid_level_secondary_max.value()} m\n"
            range_text += f"石灰石浆液密度：{self.spinBox_limestone_density_secondary_min.value()} - {self.spinBox_limestone_density_secondary_max.value()} kg/m³\n"
            range_text += f"入口压力：{self.spinBox_inlet_pressure_min.value()} - {self.spinBox_inlet_pressure_max.value()} Pa\n"
            range_text += f"出口压力：{self.spinBox_outlet_pressure_min.value()} - {self.spinBox_outlet_pressure_max.value()} Pa\n"

        range_text += "\n【运行推荐值设置】\n"
        range_text += "一级塔：\n"
        range_text += f"液气比：{self.spinBox_liquid_gas_ratio_min.value()} - {self.spinBox_liquid_gas_ratio_max.value()} L/m³\n"
        range_text += f"PH值：{self.spinBox_ph_recommended_min.value()} - {self.spinBox_ph_recommended_max.value()}\n"
        range_text += f"钙硫比：{self.spinBox_ca_s_ratio_min.value()} - {self.spinBox_ca_s_ratio_max.value()}\n"

        if self.system_config['mode'] == 'double':
            range_text += "二级塔：\n"
            range_text += f"液气比：{self.spinBox_liquid_gas_ratio_secondary_min.value()} - {self.spinBox_liquid_gas_ratio_secondary_max.value()} L/m³\n"
            range_text += f"PH值：{self.spinBox_ph_recommended_secondary_min.value()} - {self.spinBox_ph_recommended_secondary_max.value()}\n"
            range_text += f"钙硫比：{self.spinBox_ca_s_ratio_secondary_min.value()} - {self.spinBox_ca_s_ratio_secondary_max.value()}\n"

        self.textEdit_value_range.setPlainText(range_text)

        # 更新泵列表显示
        pump_text = "【一级塔循环泵列表】\n"
        for i in range(self.tableWidget_primary.rowCount()):
            pump_text += f"泵名称：{self.tableWidget_primary.item(i, 0).text()}\n"
            pump_text += f"功率：{self.tableWidget_primary.item(i, 1).text()} KW\n"
            pump_text += f"循环流量：{self.tableWidget_primary.item(i, 2).text()} m³/h\n"
            pump_text += f"电流范围：{self.tableWidget_primary.item(i, 3).text()} A\n\n"

        if self.system_config['mode'] == 'double':
            pump_text += "\n【二级塔循环泵列表】\n"
            for i in range(self.tableWidget_secondary.rowCount()):
                pump_text += f"泵名称：{self.tableWidget_secondary.item(i, 0).text()}\n"
                pump_text += f"功率：{self.tableWidget_secondary.item(i, 1).text()} KW\n"
                pump_text += f"循环流量：{self.tableWidget_secondary.item(i, 2).text()} m³/h\n"
                pump_text += f"电流范围：{self.tableWidget_secondary.item(i, 3).text()} A\n\n"

        self.textEdit_pump_list.setPlainText(pump_text)

        # 更新地址列表显示
        address_text = "【一级塔参数地址】\n"
        address_text += f"机组负荷：{self.lineEdit_load_address.text()}\n"
        address_text += f"PH值：{self.lineEdit_ph_address.text()}\n"
        address_text += f"原烟气SO2：{self.lineEdit_raw_so2_address.text()}\n"
        address_text += f"氧化风量：{self.lineEdit_oxidation_air_address.text()}\n"
        address_text += f"供浆流量：{self.lineEdit_slurry_flow_address.text()}\n"
        address_text += f"液位高度：{self.lineEdit_liquid_level_address.text()}\n"
        address_text += f"石灰石浆液密度：{self.lineEdit_limestone_density_address.text()}\n"
        address_text += f"入口烟气量：{self.lineEdit_inlet_gas_address.text()}\n"
        address_text += f"出口烟气量：{self.lineEdit_outlet_gas_address.text()}\n"

        address_text += "\n循环泵电流：\n"
        address_text += f"循环泵A：{self.lineEdit_current_a.text()}\n"
        address_text += f"循环泵B：{self.lineEdit_current_b.text()}\n"
        address_text += f"循环泵C：{self.lineEdit_current_c.text()}\n"
        address_text += f"循环泵D：{self.lineEdit_current_d.text()}\n"

        if self.system_config['mode'] == 'double':
            address_text += "\n【二级塔参数地址】\n"
            address_text += f"PH值：{self.lineEdit_ph_secondary_address.text()}\n"
            address_text += f"联络烟道SO2：{self.lineEdit_connection_so2_address.text()}\n"
            address_text += f"净烟气SO2：{self.lineEdit_clean_so2_address.text()}\n"
            address_text += f"氧化风量：{self.lineEdit_oxidation_air_secondary_address.text()}\n"
            address_text += f"供浆流量：{self.lineEdit_slurry_flow_secondary_address.text()}\n"
            address_text += f"液位高度：{self.lineEdit_liquid_level_secondary_address.text()}\n"
            address_text += f"石灰石浆液密度：{self.lineEdit_limestone_density_secondary_address.text()}\n"
            address_text += f"入口压力：{self.lineEdit_inlet_pressure_address.text()}\n"
            address_text += f"出口压力：{self.lineEdit_outlet_pressure_address.text()}\n"

            address_text += "\n循环泵电流：\n"
            address_text += f"循环泵A：{self.lineEdit_current_secondary_a.text()}\n"
            address_text += f"循环泵B：{self.lineEdit_current_secondary_b.text()}\n"
            address_text += f"循环泵C：{self.lineEdit_current_secondary_c.text()}\n"
            address_text += f"循环泵D：{self.lineEdit_current_secondary_d.text()}\n"

        self.textEdit_address_list.setPlainText(address_text)

    def confirm_setup(self):
        """确认完成设置"""
        reply = QMessageBox.question(
            self,
            "确认配置",
            "请确认配置信息是否正确？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            try:
                # 读取现有配置
                with open('settings-defaults.json', 'r', encoding='utf-8') as f:
                    settings = json.load(f)

                # 更新基本配置
                settings['gui']['tower'] = '2' if self.system_config['mode'] == 'double' else '1'
                settings['gui']['control_type'] = 'boye' if self.system_config['type'] == 'boye' else 'flow'

                # 更新通信配置
                settings['communication'] = {
                    'mode': self.system_config['communication']['mode'],
                    'role': self.comboBox_role.currentText(),
                    'port': self.lineEdit_port.text(),
                    'baudrate': self.comboBox_baudrate.currentText(),
                    'parity': self.comboBox_parity.currentText(),
                    'stopbits': self.lineEdit_stopbits.text(),
                    'databits': self.lineEdit_databits.text()
                }

                # 更新一级塔取值范围
                settings['primary_tower'] = {
                    'load': {
                        'min': self.spinBox_load_min.value(),
                        'max': self.spinBox_load_max.value()
                    },
                    'ph': {
                        'min': self.spinBox_ph_min.value(),
                        'max': self.spinBox_ph_max.value()
                    },
                    'so2': {
                        'min': self.spinBox_so2_min.value(),
                        'max': self.spinBox_so2_max.value()
                    },
                    'oxidation_air': {
                        'min': self.spinBox_oxidation_air_min.value(),
                        'max': self.spinBox_oxidation_air_max.value()
                    },
                    'slurry_flow': {
                        'min': self.spinBox_slurry_flow_min.value(),
                        'max': self.spinBox_slurry_flow_max.value()
                    },
                    'liquid_level': {
                        'min': self.spinBox_liquid_level_min.value(),
                        'max': self.spinBox_liquid_level_max.value()
                    },
                    'limestone_density': {
                        'min': self.spinBox_limestone_density_min.value(),
                        'max': self.spinBox_limestone_density_max.value()
                    },
                    'inlet_gas': {
                        'min': self.spinBox_inlet_gas_min.value(),
                        'max': self.spinBox_inlet_gas_max.value()
                    },
                    'outlet_gas': {
                        'min': self.spinBox_outlet_gas_min.value(),
                        'max': self.spinBox_outlet_gas_max.value()
                    },
                    'recommended': {
                        'liquid_gas_ratio': {
                            'min': self.spinBox_liquid_gas_ratio_min.value(),
                            'max': self.spinBox_liquid_gas_ratio_max.value()
                        },
                        'ph': {
                            'min': self.spinBox_ph_recommended_min.value(),
                            'max': self.spinBox_ph_recommended_max.value()
                        },
                        'ca_s_ratio': {
                            'min': self.spinBox_ca_s_ratio_min.value(),
                            'max': self.spinBox_ca_s_ratio_max.value()
                        }
                    },
                    'addresses': {
                        'load': self.lineEdit_load_address.text(),
                        'ph': self.lineEdit_ph_address.text(),
                        'so2': self.lineEdit_raw_so2_address.text(),
                        'oxidation_air': self.lineEdit_oxidation_air_address.text(),
                        'slurry_flow': self.lineEdit_slurry_flow_address.text(),
                        'liquid_level': self.lineEdit_liquid_level_address.text(),
                        'limestone_density': self.lineEdit_limestone_density_address.text(),
                        'inlet_gas': self.lineEdit_inlet_gas_address.text(),
                        'outlet_gas': self.lineEdit_outlet_gas_address.text(),
                        'current': {
                            'pump_a': self.lineEdit_current_a.text(),
                            'pump_b': self.lineEdit_current_b.text(),
                            'pump_c': self.lineEdit_current_c.text(),
                            'pump_d': self.lineEdit_current_d.text()
                        }
                    }
                }

                # 更新二级塔取值范围（如果是双塔模式）
                if self.system_config['mode'] == 'double':
                    settings['secondary_tower'] = {
                        'ph': {
                            'min': self.spinBox_ph_secondary_min.value(),
                            'max': self.spinBox_ph_secondary_max.value()
                        },
                        'so2_connection': {
                            'min': self.spinBox_so2_connection_min.value(),
                            'max': self.spinBox_so2_connection_max.value()
                        },
                        'so2_clean': {
                            'min': self.spinBox_so2_clean_min.value(),
                            'max': self.spinBox_so2_clean_max.value()
                        },
                        'oxidation_air': {
                            'min': self.spinBox_oxidation_air_secondary_min.value(),
                            'max': self.spinBox_oxidation_air_secondary_max.value()
                        },
                        'slurry_flow': {
                            'min': self.spinBox_slurry_flow_secondary_min.value(),
                            'max': self.spinBox_slurry_flow_secondary_max.value()
                        },
                        'liquid_level': {
                            'min': self.spinBox_liquid_level_secondary_min.value(),
                            'max': self.spinBox_liquid_level_secondary_max.value()
                        },
                        'limestone_density': {
                            'min': self.spinBox_limestone_density_secondary_min.value(),
                            'max': self.spinBox_limestone_density_secondary_max.value()
                        },
                        'inlet_pressure': {
                            'min': self.spinBox_inlet_pressure_min.value(),
                            'max': self.spinBox_inlet_pressure_max.value()
                        },
                        'outlet_pressure': {
                            'min': self.spinBox_outlet_pressure_min.value(),
                            'max': self.spinBox_outlet_pressure_max.value()
                        },
                        'recommended': {
                            'liquid_gas_ratio': {
                                'min': self.spinBox_liquid_gas_ratio_secondary_min.value(),
                                'max': self.spinBox_liquid_gas_ratio_secondary_max.value()
                            },
                            'ph': {
                                'min': self.spinBox_ph_recommended_secondary_min.value(),
                                'max': self.spinBox_ph_recommended_secondary_max.value()
                            },
                            'ca_s_ratio': {
                                'min': self.spinBox_ca_s_ratio_secondary_min.value(),
                                'max': self.spinBox_ca_s_ratio_secondary_max.value()
                            }
                        },
                        'addresses': {
                            'ph': self.lineEdit_ph_secondary_address.text(),
                            'so2_connection': self.lineEdit_connection_so2_address.text(),
                            'so2_clean': self.lineEdit_clean_so2_address.text(),
                            'oxidation_air': self.lineEdit_oxidation_air_secondary_address.text(),
                            'slurry_flow': self.lineEdit_slurry_flow_secondary_address.text(),
                            'liquid_level': self.lineEdit_liquid_level_secondary_address.text(),
                            'limestone_density': self.lineEdit_limestone_density_secondary_address.text(),
                            'inlet_pressure': self.lineEdit_inlet_pressure_address.text(),
                            'outlet_pressure': self.lineEdit_outlet_pressure_address.text(),
                            'current': {
                                'pump_a': self.lineEdit_current_secondary_a.text(),
                                'pump_b': self.lineEdit_current_secondary_b.text(),
                                'pump_c': self.lineEdit_current_secondary_c.text(),
                                'pump_d': self.lineEdit_current_secondary_d.text()
                            }
                        }
                    }

                # 更新循环泵配置
                # 获取一级塔循环泵配置
                primary_pumps = []
                for i in range(self.tableWidget_primary.rowCount()):
                    try:
                        pump = {
                            'name': self.tableWidget_primary.item(i, 0).text(),
                            'power': float(self.tableWidget_primary.item(i, 1).text()),
                            'flow': float(self.tableWidget_primary.item(i, 2).text()),
                            'head': float(self.tableWidget_primary.item(i, 3).text()),
                            'current_range': self.tableWidget_primary.item(i, 4).text()
                        }
                        primary_pumps.append(pump)
                    except (ValueError, AttributeError) as e:
                        print(f"一级塔泵数据转换错误: {e}")
                        continue

                # 获取二级塔循环泵配置（如果是双塔模式）
                secondary_pumps = []
                if self.system_config['mode'] == 'double':
                    for i in range(self.tableWidget_secondary.rowCount()):
                        try:
                            pump = {
                                'name': self.tableWidget_secondary.item(i, 0).text(),
                                'power': float(self.tableWidget_secondary.item(i, 1).text()),
                                'flow': float(self.tableWidget_secondary.item(i, 2).text()),
                                'head': float(self.tableWidget_secondary.item(i, 3).text()),
                                'current_range': self.tableWidget_secondary.item(i, 4).text()
                            }
                            secondary_pumps.append(pump)
                        except (ValueError, AttributeError) as e:
                            print(f"二级塔泵数据转换错误: {e}")
                            continue

                # 更新泵配置到settings
                settings['pumps'] = {
                    'primary_pumps': primary_pumps,
                    'secondary_pumps': secondary_pumps
                }

                # 保存更新后的settings
                with open('settings-defaults.json', 'w', encoding='utf-8') as f:
                    json.dump(settings, f, ensure_ascii=False, indent=4)

                QMessageBox.information(self, "完成", "系统初始化完成！")
                self.close()

                # 启动Application.py
                current_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                application_path = os.path.join(current_dir, 'Application.py')
                subprocess.run([sys.executable, application_path])

            except Exception as e:
                QMessageBox.critical(self, "错误", f"保存配置失败：{str(e)}")


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = SystemInitWindow()
    window.show()
    sys.exit(app.exec_())