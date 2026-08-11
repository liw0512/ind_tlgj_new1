import numpy as np
from collections import deque

class PHStatCalc(object):
    def __init__(self, stack_len=5, comm_T=1):  # 由于已经降采样到30s，这里直接使用30
        # 计算不同时间段所需的数据长度
        self.comm_T = comm_T  # 采样间隔30s
        self.N_4h = int(4 * 3600 / self.comm_T)  # 4小时数据长度
        self.N_8h = int(8 * 3600 / self.comm_T)  # 8小时数据长度
        self.N_1d = int(24 * 3600 / self.comm_T)  # 1天数据长度
        self.N_7d = int(7 * 24 * 3600 / self.comm_T)  # 7天数据长度

        # 初始化数据存储队列
        self.ph_stack_apt = deque(maxlen=self.N_7d)  # 吸收塔PH值队列(原xst)
        self.ph_stack_xst = deque(maxlen=self.N_7d)  # 预洗塔PH值队列(原apt)
        self.s_out_stack = deque(maxlen=self.N_7d)   # 出口SO2浓度队列

    def calculate_stats(self, data_dict):
        """
        计算PH值和SO2浓度的统计特征
        :param data_dict: 包含必要数据的字典
        :return: 包含统计特征的字典
        """
        # 获取当前值
        ph_apt = data_dict.get("aptjy_PH", 0)  # 吸收塔PH均值(原xst)
        ph_xst = data_dict.get("xstjy_PH", 0)  # 预洗塔PH均值(原apt)
        s_out = data_dict.get("jyq_SO2", 0)    # 净烟气SO2浓度

        # 添加新数据
        self.ph_stack_apt.append(ph_apt)
        self.ph_stack_xst.append(ph_xst)
        self.s_out_stack.append(s_out)

        # 转换为numpy数组便于计算
        ph_apt_array = np.array(self.ph_stack_apt)
        ph_xst_array = np.array(self.ph_stack_xst)
        s_out_array = np.array(self.s_out_stack)

        # 计算统计特征
        stats = {}
        
        # 实时均值和标准差
        stats.update({
            "apt_ph_mean": np.round(np.mean(ph_apt_array), 3),
            "apt_ph_std": np.round(np.std(ph_apt_array), 3),
            "xst_ph_mean": np.round(np.mean(ph_xst_array), 3),
            "xst_ph_std": np.round(np.std(ph_xst_array), 3),
            "so2_mean": np.round(np.mean(s_out_array), 3),
            "so2_std": np.round(np.std(s_out_array), 3)
        })

        # 计算不同时间段的统计特征
        time_periods = {
            '4h': self.N_4h,
            '8h': self.N_8h,
            '1d': self.N_1d,
            '7d': self.N_7d
        }

        for period, n in time_periods.items():
            if len(ph_apt_array) >= n:
                stats.update({
                    f"apt_ph_{period}_mean": np.round(np.mean(ph_apt_array[-n:]), 3),
                    f"apt_ph_{period}_std": np.round(np.std(ph_apt_array[-n:]), 3),
                    f"xst_ph_{period}_mean": np.round(np.mean(ph_xst_array[-n:]), 3),
                    f"xst_ph_{period}_std": np.round(np.std(ph_xst_array[-n:]), 3),
                    f"so2_{period}_mean": np.round(np.mean(s_out_array[-n:]), 3),
                    f"so2_{period}_std": np.round(np.std(s_out_array[-n:]), 3)
                })
            else:
                # 数据不足时使用所有可用数据
                stats.update({
                    f"apt_ph_{period}_mean": stats["apt_ph_mean"],
                    f"apt_ph_{period}_std": stats["apt_ph_std"],
                    f"xst_ph_{period}_mean": stats["xst_ph_mean"],
                    f"xst_ph_{period}_std": stats["xst_ph_std"],
                    f"so2_{period}_mean": stats["so2_mean"],
                    f"so2_{period}_std": stats["so2_std"]
                })

        return stats
# 添加测试代码
if __name__ == "__main__":
    # 创建测试数据
    np.random.seed(42)  # 设置随机种子，确保结果可重现
    
    # 生成20个随机测试数据
    test_data = []
    for i in range(20):
        data = {
            "aptjy_PH": np.random.uniform(5.0, 6.5),    # 吸收塔PH值范围通常在5.0-6.5
            "xstjy_PH": np.random.uniform(4.5, 6.0),    # 预洗塔PH值范围通常在4.5-6.0
            "jyq_SO2": np.random.uniform(10, 100)       # SO2浓度范围假设在10-100
        }
        test_data.append(data)
    
    # 创建PHStatCalc实例
    ph_calc = PHStatCalc(comm_T=30)
    
    # 测试数据处理和输出
    print("模拟20个数据点的处理过程：")
    print("-" * 50)
    
    for i, data in enumerate(test_data, 1):
        print(f"\n处理第{i}个数据点:")
        print(f"输入数据: {data}")
        
        # 计算统计特征
        stats = ph_calc.calculate_stats(data)
        
        # 输出关键统计值
        print("\n计算结果:")
        print(f"实时统计:")
        print(f"  吸收塔PH均值: {stats['apt_ph_mean']:.3f}")
        print(f"  预洗塔PH均值: {stats['xst_ph_mean']:.3f}")
        print(f"  净烟气SO2均值: {stats['so2_mean']:.3f}")
        
        # 每5个数据点输出一次详细统计信息
        if i % 5 == 0:
            print("\n详细统计信息:")
            print("4小时统计:")
            print(f"  吸收塔PH均值(4h): {stats.get('apt_ph_4h_mean', 'N/A')}")
            print(f"  预洗塔PH均值(4h): {stats.get('xst_ph_4h_mean', 'N/A')}")
            print(f"  SO2均值(4h): {stats.get('so2_4h_mean', 'N/A')}")
            
            print("\n8小时统计:")
            print(f"  吸收塔PH均值(8h): {stats.get('apt_ph_8h_mean', 'N/A')}")
            print(f"  预洗塔PH均值(8h): {stats.get('xst_ph_8h_mean', 'N/A')}")
            print(f"  SO2均值(8h): {stats.get('so2_8h_mean', 'N/A')}")
            
            print("\n标准差信息:")
            print(f"  吸收塔PH标准差: {stats['apt_ph_std']:.3f}")
            print(f"  预洗塔PH标准差: {stats['xst_ph_std']:.3f}")
            print(f"  SO2标准差: {stats['so2_std']:.3f}")
            print("-" * 50)