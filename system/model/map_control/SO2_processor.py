import pandas as pd
from collections import deque
from datetime import datetime, timedelta
import os
import time
from calendar import timegm
from calendar import monthrange, timegm
# ======================== 全局常量定义 ========================
# 时间相关常量
SECONDS_PER_HOUR = 3600    # 每小时的秒数
SECONDS_PER_DAY = 86400    # 每天的秒数
SECONDS_PER_MINUTE = 60    # 每分钟的秒数
DEFAULT_HOURS_WINDOW = 1   # 默认M1计算时间窗口（小时）
MAX_CACHE_HOURS = 24       # 最大缓存时间（小时）

# 数据采集频率和缓存设置
DATA_INTERVAL_SECONDS = 1 # 数据采集间隔（秒）
MAX_CACHE_DAYS = 31       # 最大缓存天数（支持月度统计）
MAX_BUFFER_SIZE = int(MAX_CACHE_DAYS * SECONDS_PER_DAY / DATA_INTERVAL_SECONDS)  # 最大缓存条数 (31天)

# 计算相关常量
CONVERSION_FACTOR = 1e9    # 用于将mg/m³转换为t/h的转换因子（10^9）

# 时间格式化常量
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"  # 时间格式化模板

class SO2Processor:
    """
    SO2处理器类
    用于处理SO2排放数据，计算实时排放量和指定时间窗口内的排放总量
    """
    def __init__(self):
        """
        初始化SO2处理器
        """
        self.data_buffer = deque(maxlen=MAX_BUFFER_SIZE)  # 使用deque限制最大缓存条数（1天）
        self.data_count = 0  # 总处理数据计数
        
    def process_data(self, data_dict):
        """
        处理数据字典，计算M0、当天M1和当月M1，并将结果添加到原字典后返回
        
        参数:
            data_dict (dict): 包含数据的字典，需要包含SO2浓度和烟气流量相关字段
        返回:
            dict: 原始字典加上M0、M1_daily和M1_monthly三个字段
        """
        self.data_count += 1
        # 创建原始数据的浅拷贝
        result_dict = data_dict.copy()  
        
        try:
            # 确保数据字典包含时间字段
            if 'date' not in data_dict:
                data_dict['date'] = datetime.now().strftime(TIME_FORMAT)
            
            # 计算M0
            try:
                # 自动寻找SO2浓度相关字段
                so2_field_names = ['jyq_SO2', 'SO2', 'so2', 'SO2浓度', 'so2浓度', 'C净烟']
                c_net = None
                for field in so2_field_names:
                    if field in data_dict:
                        c_net = data_dict[field]
                        break
                
                # 自动寻找烟气流量相关字段
                flow_field_names = ['jyq_LL', 'LL', 'll', '烟气流量', '净烟气量', 'Q净烟']
                q_net = None
                for field in flow_field_names:
                    if field in data_dict:
                        q_net = data_dict[field]
                        break
                
                # 检查是否找到必要的字段
                if c_net is None:
                    raise ValueError(f"无法找到SO2浓度相关字段，尝试查找的字段有: {so2_field_names}")
                if q_net is None:
                    raise ValueError(f"无法找到烟气流量相关字段，尝试查找的字段有: {flow_field_names}")
                
                # 计算M0
                m0 = self.calculate_M0(c_net, q_net)
                result_dict['M0'] = m0
            except Exception as e:
                # M0计算失败时设为0，但不影响后续M1的计算
                result_dict['M0'] = 0.0
                print(f"M0计算错误: {str(e)}")
            
            # 将数据添加到缓冲区 (使用deque的maxlen自动限制大小)
            # 即使M0计算失败，仍然保存原始数据用于M1计算
            self.data_buffer.append(dict(data_dict))
            
            # 计算当天排放总量
            try:
                m1_daily = self.calculate_daily_M1()
                result_dict['M1_daily'] = m1_daily
            except Exception as e:
                result_dict['M1_daily'] = 0.0
                print(f"当天排放总量计算错误: {str(e)}")
                
            # 计算当月排放总量
            try:
                m1_monthly = self.calculate_monthly_M1()
                result_dict['M1_monthly'] = m1_monthly
            except Exception as e:
                result_dict['M1_monthly'] = 0.0
                print(f"当月排放总量计算错误: {str(e)}")
            
            return result_dict
            
        except Exception as e:
            # 处理其他未预期的错误
            print(f"数据处理过程发生未预期错误: {str(e)}")
            # 确保即使发生错误也返回原始字典，并将M0和M1设为0
            result_dict['M0'] = 0.0
            result_dict['M1_daily'] = 0.0
            result_dict['M1_monthly'] = 0.0
            return result_dict
    def calculate_M0(self, c_net, q_net):
        """
        计算SO2实时排放量 M0
        
        计算公式: M0 = (c_net * q_net) / 10^9
        
        参数:
            c_net (float): 净烟气SO2浓度（mg/m³）
            q_net (float): 净烟气量（m³/h）
        
        返回:
            float: 实时排放率（t/h）
        """
        try:
            # 确保输入数据为数值类型
            c_net = float(c_net)
            q_net = float(q_net)
            
            # 检查数值是否有效
            if c_net < 0 or q_net < 0:
                raise ValueError("SO2浓度和烟气流量不能为负值")
                
            return (c_net * q_net) / CONVERSION_FACTOR
        except Exception as e:
            print(f"M0计算错误: {str(e)}")
            return 0.0

    # def calculate_M1_period(self, start_time, end_time):
    #     """
    #     计算指定时间段内的SO2排污总量
    #
    #     Args:
    #         start_time: 开始时间（datetime对象或时间字符串）
    #         end_time: 结束时间（datetime对象或时间字符串）
    #
    #     Returns:
    #         float: 时间段内的SO2排放总量（吨）
    #     """
    #     try:
    #         if not self.data_buffer:
    #             return 0.0
    #
    #         # 处理时间参数
    #         if isinstance(start_time, str):
    #             start_time = datetime.strptime(start_time, TIME_FORMAT)
    #         if isinstance(end_time, str):
    #             end_time = datetime.strptime(end_time, TIME_FORMAT)
    #
    #         # 筛选指定时间范围内的数据
    #         filtered_data = []
    #         for item in self.data_buffer:
    #             try:
    #                 date_value = item.get('date')
    #                 if date_value:
    #                     if isinstance(date_value, str):
    #                         date_value = datetime.strptime(date_value, TIME_FORMAT)
    #
    #                     if start_time <= date_value <= end_time:
    #                         filtered_data.append((date_value, item))
    #             except Exception as e:
    #                 print(f"处理数据时间戳错误: {str(e)}")
    #                 continue
    #
    #         if not filtered_data:
    #             return 0.0
    #
    #         # 按时间排序
    #         filtered_data.sort(key=lambda x: x[0])
    #
    #         # 计算时间段内的排放总量
    #         total_emission = 0.0
    #         prev_time = None
    #         prev_rate = None
    #
    #         for current_time, item in filtered_data:
    #             try:
    #                 # 获取SO2浓度
    #                 c_net = None
    #                 for field in ['jyq_SO2', 'SO2', 'so2', 'SO2浓度', 'so2浓度', 'C净烟']:
    #                     if field in item:
    #                         c_net = float(item[field])
    #                         break
    #
    #                 # 获取烟气流量
    #                 q_net = None
    #                 for field in ['jyq_LL', 'LL', 'll', '烟气流量', '净烟气量', 'Q净烟']:
    #                     if field in item:
    #                         q_net = float(item[field])
    #                         break
    #
    #                 if c_net is not None and q_net is not None and c_net >= 0 and q_net >= 0:
    #                     current_rate = self.calculate_M0(c_net, q_net)
    #
    #                     if prev_time is not None and prev_rate is not None:
    #                         # 计算两个时间点之间的时间差（小时）
    #                         time_diff = (current_time - prev_time).total_seconds() / SECONDS_PER_HOUR
    #                         # 使用梯形积分法计算这个时间段的排放量
    #                         segment_emission = (prev_rate + current_rate) * time_diff / 2
    #                         total_emission += segment_emission
    #
    #                     prev_time = current_time
    #                     prev_rate = current_rate
    #
    #             except Exception as e:
    #                 print(f"计算单点排放量错误: {str(e)}")
    #                 continue
    #
    #         return total_emission
    #
    #     except Exception as e:
    #         print(f"计算时间段排放量错误: {str(e)}")
    #         return 0.0
    def calculate_M1_period(self, start_time, end_time):
        """
        计算指定时间段内的SO2排污总量

        Args:
            start_time: 开始时间（datetime对象或时间字符串）
            end_time: 结束时间（datetime对象或时间字符串）

        Returns:
            float: 时间段内的SO2排放总量（吨）
        """
        try:
            if not self.data_buffer:
                return 0.0

            # 处理时间参数
            if isinstance(start_time, str):
                start_time = datetime.strptime(start_time, TIME_FORMAT)
            if isinstance(end_time, str):
                end_time = datetime.strptime(end_time, TIME_FORMAT)

            # 创建数据缓冲区的副本，避免遍历过程中被修改
            data_buffer_copy = list(self.data_buffer)

            # 筛选指定时间范围内的数据
            filtered_data = []
            for item in data_buffer_copy:
                try:
                    date_value = item.get('date')
                    if date_value:
                        if isinstance(date_value, str):
                            date_value = datetime.strptime(date_value, TIME_FORMAT)

                        if start_time <= date_value <= end_time:
                            filtered_data.append((date_value, item))
                except Exception as e:
                    print(f"处理数据时间戳错误: {str(e)}")
                    continue

            if not filtered_data:
                print(f"在时间范围 {start_time} 到 {end_time} 内没有找到数据")
                return 0.0

            # 按时间排序
            filtered_data.sort(key=lambda x: x[0])

            # 计算时间段内的排放总量
            total_emission = 0.0
            prev_time = None
            prev_rate = None

            for current_time, item in filtered_data:
                try:
                    # 获取SO2浓度
                    c_net = None
                    for field in ['jyq_SO2', 'SO2', 'so2', 'SO2浓度', 'so2浓度', 'C净烟']:
                        if field in item:
                            c_net = float(item[field])
                            break

                    # 获取烟气流量
                    q_net = None
                    for field in ['jyq_LL', 'LL', 'll', '烟气流量', '净烟气量', 'Q净烟']:
                        if field in item:
                            q_net = float(item[field])
                            break

                    if c_net is not None and q_net is not None and c_net >= 0 and q_net >= 0:
                        current_rate = self.calculate_M0(c_net, q_net)

                        if prev_time is not None and prev_rate is not None:
                            # 计算两个时间点之间的时间差（小时）
                            time_diff = (current_time - prev_time).total_seconds() / SECONDS_PER_HOUR
                            # 使用梯形积分法计算这个时间段的排放量
                            segment_emission = (prev_rate + current_rate) * time_diff / 2
                            total_emission += segment_emission

                        prev_time = current_time
                        prev_rate = current_rate

                except Exception as e:
                    print(f"计算单点排放量错误: {str(e)}, 数据项: {item}")
                    continue

            return total_emission

        except Exception as e:
            import traceback
            print(f"计算时间段排放量错误: {str(e)}")
            print(traceback.format_exc())
            return 0.0
    def calculate_daily_M1(self, target_date=None):
        """
        计算指定日期的SO2排放总量，即使缓存不足也会基于现有数据计算
        """
        try:
            if target_date is None:
                target_date = datetime.now()
            elif isinstance(target_date, str):
                target_date = datetime.strptime(target_date, "%Y-%m-%d")
            
            start_time = datetime.combine(target_date.date(), datetime.min.time())
            end_time = datetime.combine(target_date.date(), datetime.max.time())
            
            return self.calculate_M1_period(start_time, end_time)
        except Exception as e:
            print(f"计算日排放量错误: {str(e)}")
            return 0.0

    def calculate_monthly_M1(self, year=None, month=None):
        """
        计算指定月份的SO2排放总量，即使缓存不足也会基于现有数据计算
        """
        try:
            if year is None or month is None:
                current_date = datetime.now()
                year = current_date.year if year is None else year
                month = current_date.month if month is None else month
            
            # 获取月份的第一天和最后一天
            _, last_day = monthrange(year, month)
            start_time = datetime(year, month, 1)
            end_time = datetime(year, month, last_day, 23, 59, 59)
            
            return self.calculate_M1_period(start_time, end_time)
        except Exception as e:
            print(f"计算月排放量错误: {str(e)}")
            return 0.0

    def _check_data_coverage(self, start_time, end_time):
        """
        检查数据缓存是否覆盖指定的时间范围
        
        返回:
            bool: 如果缓存完全覆盖指定时间范围则返回True，否则返回False
        """
        if not self.data_buffer:
            return False
        
        # 获取缓存中最早和最晚的时间
        earliest = self.data_buffer[0].get('date')
        latest = self.data_buffer[-1].get('date')
        
        if isinstance(earliest, str):
            earliest = datetime.strptime(earliest, TIME_FORMAT)
        if isinstance(latest, str):
            latest = datetime.strptime(latest, TIME_FORMAT)
        
        # 检查缓存是否完全覆盖目标时间范围
        return earliest <= start_time and latest >= end_time
    
    def get_buffer_stats(self):
        """
        获取缓冲区统计信息
        """
        if not self.data_buffer:
            return {
                "buffer_size": 0,
                "earliest_time": None,
                "latest_time": None,
                "time_span_hours": 0
            }
            
        earliest = self.data_buffer[0].get('date')
        latest = self.data_buffer[-1].get('date')
        
        if isinstance(earliest, str):
            earliest = datetime.strptime(earliest, TIME_FORMAT)
        if isinstance(latest, str):
            latest = datetime.strptime(latest, TIME_FORMAT)
            
        time_span = (latest - earliest).total_seconds() / SECONDS_PER_HOUR
        
        return {
            "buffer_size": len(self.data_buffer),
            "earliest_time": earliest,
            "latest_time": latest,
            "time_span_hours": time_span
        }
    
    def reset(self):
        """
        重置处理器状态
        """
        self.data_buffer.clear()  # 清空缓冲区
        self.data_count = 0       # 重置计数器
# 示例用法
if __name__ == "__main__":
    # 创建处理器实例
    processor = SO2Processor()

    # 示例1：不指定时间范围（默认计算最近1小时）
    result1 = processor.calculate_M1()  # 将自动计算最近1小时的数据

    # 示例2：指定具体时间范围（使用字符串格式）
    start_time = "2025-06-07 10:00:00"
    end_time = "2025-06-07 11:00:00"
    result2 = processor.calculate_M1(start_time=start_time, end_time=end_time)

    # 示例3：使用datetime对象指定时间范围
    from datetime import datetime, timedelta
    current_time = datetime.now()
    start_time = current_time - timedelta(hours=2)  # 2小时前
    end_time = current_time - timedelta(hours=1)    # 1小时前
    result3 = processor.calculate_M1(start_time=start_time, end_time=end_time)

    # 示例4：计算特定时段的排放量（例如：上午8点到9点）
    specific_date = datetime.now().strftime("%Y-%m-%d")  # 获取当前日期
    start_time = f"{specific_date} 08:00:00"
    end_time = f"{specific_date} 09:00:00"
    result4 = processor.calculate_M1(start_time=start_time, end_time=end_time)

    # 打印统计信息
    stats = processor.get_buffer_stats()
    print(f"缓冲区统计信息: {stats}")