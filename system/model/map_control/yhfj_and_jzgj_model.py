# -*- coding: utf-8 -*-
"""
整合模块：基准供浆量与氧化风机量计算系统
包含：
1. JiZhunGongJinag - 基准供浆量计算模块(支持吸收塔和APT塔)
2. YangHuaFengJi - 氧化风机量计算模块(支持吸收塔和APT塔)
3. ProcessControl - 主控制系统
"""

import numpy as np
from collections import deque
import pandas as pd
import time


class JiZhunGongJiang:
    """基准供浆量计算类"""
    def __init__(self):
        # 全局参数设置
        self.K = 0.0011   #k系数
        self.B = -1.0059  #C密度修正参数
        
    def get_ca_s_ratio(self, ph):
        """根据PH值获取钙硫比"""
        # PH值与钙硫比对照表
        ph_ca_s_map = {
            6.0: 1.7,
            5.8: 1.6,
            5.6: 1.5,
            5.4: 1.4,
            5.2: 1.3,
            5.0: 1.2,
            4.8: 1.1
        }
        
        # 找到最接近的PH值
        ph_values = list(ph_ca_s_map.keys())
        closest_ph = min(ph_values, key=lambda x: abs(x - ph))
        return ph_ca_s_map[closest_ph]
        
    # 修改 JiZhunGongJiang 类中的 cal_ban_flow 方法

    # def cal_ban_flow(self, params):
    #     """
    #     计算基准供浆量
    #     Args:
    #         params: dict, 包含以下参数：
    #             - jzfh: float, 机组负荷(T)
    #             - yyq_SO2: float, 原烟气SO2(c1)
    #             - jyq_SO2: float, 净烟气SO2(c2)
    #             - xstshsjy_MD: float, 吸收塔石灰石浆液密度(ρ)，默认为1200
    #             - aptshsjy_MD: float, APT塔石灰石浆液密度(ρ)，默认为1200
    #             - llyd_SO2: float, 联络烟道SO2(c3)
    #             - xstjy_PH: float, 吸收塔浆液PH
    #             - aptjy_PH: float, APT塔浆液PH
    #     Returns:
    #         dict: 包含吸收塔和APT塔的基准供浆量
    #     """
    #     # 参数检查
    #     required_params = ['jzfh', 'yyq_SO2', 'jyq_SO2', 'xstjy_PH', 'aptjy_PH','llyd_SO2']
    #     for param in required_params:
    #         if param not in params:
    #             raise ValueError(f"缺少必要参数: {param}")
        
    #     # 获取石灰石浆液密度，如果未提供则默认为1200 kg/m³
    #     xstshsjy_MD = params.get('xstshsjy_MD', params.get('shsjy_MD', 1200))
    #     aptshsjy_MD = params.get('aptshsjy_MD', params.get('shsjy_MD', 1200))
    #     if xstshsjy_MD==0:
    #         xstshsjy_MD=1200
    #     if aptshsjy_MD==0:
    #         aptshsjy_MD=1200
        
    #     # 获取吸收塔和APT塔的钙硫比
    #     ca_s_xst = 1.0
    #     ca_s_apt = 1.0
        
    #     # 获取参数
    #     T = params['jzfh']        # 机组负荷
    #     c1 = params['yyq_SO2']    # 原烟气SO2浓度
    #     c2 = params['jyq_SO2']    # 净烟气SO2浓度
    #     c3 = params['llyd_SO2']   # 联络烟道SO2浓度
        
    #     # 吸收塔基准供浆量计算 - 使用公式: ((c1-c3)×T/650×2250000×100/64×Ca/S)/((k×ρ1+C)×1000000×0.9×ρ1)
    #     xst_flow = ((c1 - 20) * T / 650 * 2250000 * 100 / 64 * ca_s_xst) / \
    #         ((self.K * xstshsjy_MD + self.B) * 1000000 * 0.9 * xstshsjy_MD)
            
    #     # APT塔基准供浆量计算 - 使用公式: ((c3-c2)×T/650×2250000×100/64×Ca/S)/((k×ρ2+C)×1000000×0.9×ρ2)
    #     apt_flow = ((c3 - 16) * T / 650 * 2250000 * 100 / 64 * ca_s_apt) / \
    #             ((self.K * aptshsjy_MD + self.B) * 1000000 * 0.9 * aptshsjy_MD)
        
    #     return {
    #         'xst_base_flow': float(xst_flow),  # 吸收塔基准供浆量
    #         'apt_base_flow': float(apt_flow)   # APT塔基准供浆量
    #     }
    def cal_ban_flow(self, params):
        """
        计算基准供浆量
        Args:
            params: dict, 包含以下参数：
                - jzfh: float, 机组负荷(T) - 保留用于兼容性
                - glfl: float, 机组送风量(t/h)
                - yyq_SO2: float, 原烟气SO2(c1)
                - yyq_O2: float, 实际氧量(%)
                - jyq_SO2: float, 净烟气SO2(c2)
                - xstshsjy_MD: float, 吸收塔石灰石浆液密度(ρ)，默认为1200
                - aptshsjy_MD: float, APT塔石灰石浆液密度(ρ)，默认为1200
                - llyd_SO2: float, 联络烟道SO2(c3)
                - xstjy_PH: float, 吸收塔浆液PH
                - aptjy_PH: float, APT塔浆液PH
        Returns:
            dict: 包含吸收塔和APT塔的基准供浆量
        """
        # 参数检查
        required_params = ['glfl', 'yyq_SO2', 'yyq_O2', 'jyq_SO2', 'xstjy_PH', 'aptjy_PH','llyd_SO2']
        for param in required_params:
            if param not in params:
                raise ValueError(f"缺少必要参数: {param}")
        
        # 获取石灰石浆液密度，如果未提供则默认为1200 kg/m³
        xstshsjy_MD = params.get('xstshsjy_MD', params.get('shsjy_MD', 1200))
        aptshsjy_MD = params.get('aptshsjy_MD', params.get('shsjy_MD', 1200))
        if xstshsjy_MD==0:
            xstshsjy_MD=1200
        if aptshsjy_MD==0:
            aptshsjy_MD=1200
        
        # 获取吸收塔和APT塔的钙硫比
        ca_s_xst = 1.0
        ca_s_apt = 1.0
        
        # 获取参数
        glfl = params['glfl']         # 机组送风量
        yyq_o2 = params['yyq_O2']     # 实际氧量
        c1 = params['yyq_SO2']        # 原烟气SO2浓度
        c2 = params['jyq_SO2']        # 净烟气SO2浓度
        c3 = params['llyd_SO2']       # 联络烟道SO2浓度
        
        # 计算烟气量 Q = glfl/2200 × 2200000 × (21-yyq_O2)/15
        Q = (glfl / 2200) * 2200000 * ((21 - yyq_o2) / 15)
        
        # 吸收塔基准供浆量计算 - 使用新公式: ((c1-c3)×Q×100/64×Ca/S)/((k×ρ1+C)×1000000×0.9×ρ1)
        xst_flow = ((c1 - 20) * Q * 100 / 64 * ca_s_xst) / \
            ((self.K * xstshsjy_MD + self.B) * 1000000 * 0.9 * xstshsjy_MD)
            
        # APT塔基准供浆量计算 - 使用新公式: ((c3-c2)×Q×100/64×Ca/S)/((k×ρ2+C)×1000000×0.9×ρ2)
        apt_flow = ((c3 - 16) * Q * 100 / 64 * ca_s_apt) / \
                ((self.K * aptshsjy_MD + self.B) * 1000000 * 0.9 * aptshsjy_MD)
        
        return {
            'xst_base_flow': float(xst_flow),  # 吸收塔基准供浆量
            'apt_base_flow': float(apt_flow)   # APT塔基准供浆量
        }
class YangHuaFengJi:
    """氧化风机计算类"""
    def __init__(self):
        # 全局参数设置
        self.a = 0.277  # 氧化标定系数a，默认0.277
        self.b = 2.14   # 氧化标定系数b，默认2.14
        self.c = 0      # 氧化标定系数c，默认0
        
        # 缓存设置 - 1小时的数据 (1s一条，共3600条)
        self.buffer_size = 3600
        self.xst_ph_buffer = deque(maxlen=self.buffer_size)
        self.apt_ph_buffer = deque(maxlen=self.buffer_size)
    
    def update_ph_buffer(self, xst_ph, apt_ph):
        """
        更新PH值缓存
        Args:
            xst_ph: float, 吸收塔PH值
            apt_ph: float, APT塔浆液PH
        """
        if xst_ph is not None:
            self.xst_ph_buffer.append(xst_ph)
        if apt_ph is not None:
            self.apt_ph_buffer.append(apt_ph)
            
    def get_ph_avg(self, buffer):
        """
        计算PH均值
        Args:
            buffer: deque, PH值缓存
        Returns:
            float: PH均值
        """
        if not buffer:
            return 6.0  # 如果缓存为空，默认返回6.0
        return sum(buffer) / len(buffer)
    
    def calculate_ph_correction(self, ph_avg):
        """
        计算PH值修正系数α
        Args:
            ph_avg: float, PH均值
        Returns:
            float: 修正系数α
        """
        alpha = 1 + (ph_avg - 4.5) / 12
        # 限制α的值在1.0到1.15之间
        return max(1.0, min(1.15, alpha))
    
    def calculate_sulfite_correction(self, sulfite_concentration):
        """
        计算亚硫酸盐浓度修正系数γ
        Args:
            sulfite_concentration: float, 亚硫酸盐浓度 (ppm)
        Returns:
            float: 修正系数γ
        """
        if sulfite_concentration is None:
            return 1.0
            
        if sulfite_concentration > 3000:
            return 1.2
        elif sulfite_concentration > 1000:
            return 1.1
        else:
            return 1.0
        
    # def calculate_so2_mass_flow(self, jzfh, yyq_SO2, llyd_SO2):
    #     """
    #     计算SO2质量流量 Q (t/h)
    #     Args:
    #         jzfh: float, 机组负荷 T (MW)
    #         yyq_SO2: float, 原烟气SO2浓度 c1 (mg/m³)
    #         llyd_SO2: float, 联络烟道SO2浓度 c3 (mg/m³)
    #     Returns:
    #         float: SO2质量流量 Q (t/h)
    #     """
    #     # 一级塔: Q=T/650*2250000*(c1-c3)/1000000000
    #     return jzfh / 650 * 2250000 * (yyq_SO2 - llyd_SO2) / 1000000000
    def calculate_so2_mass_flow(self, glfl, yyq_o2, yyq_SO2, llyd_SO2):
        """
        计算SO2质量流量 Q (t/h)
        Args:
            glfl: float, 机组送风量 (t/h)
            yyq_o2: float, 实际氧量 (%)
            yyq_SO2: float, 原烟气SO2浓度 c1 (mg/m³)
            llyd_SO2: float, 联络烟道SO2浓度 c3 (mg/m³)
        Returns:
            float: SO2质量流量 Q (t/h)
        """
        # 计算烟气量 Q = glfl/2200 × 2200000 × (21-yyq_O2)/15
        Q_gas = (glfl / 2200) * 2200000 * ((21 - yyq_o2) / 15)
        # 一级塔: Q=Q_gas*(c1-c3)/1000000000
        return Q_gas * (yyq_SO2 - llyd_SO2) / 1000000000
    # def calculate_so2_mass_flow_apt(self, jzfh, llyd_SO2, jyq_SO2):
    #     """
    #     计算APT塔SO2质量流量 Q (t/h)
    #     Args:
    #         jzfh: float, 机组负荷 T (MW)
    #         llyd_SO2: float, 联络烟道SO2浓度 c3 (mg/m³)
    #         jyq_SO2: float, 净烟气SO2浓度 c2 (mg/m³)
    #     Returns:
    #         float: SO2质量流量 Q (t/h)
    #     """
    #     # 二级塔: Q=T/650*2250000*(c3-c2)/1000000000
    #     return jzfh / 650 * 2250000 * (llyd_SO2 - jyq_SO2) / 1000000000
    def calculate_so2_mass_flow_apt(self, glfl, yyq_o2, llyd_SO2, jyq_SO2):
        """
        计算APT塔SO2质量流量 Q (t/h)
        Args:
            glfl: float, 机组送风量 (t/h)
            yyq_o2: float, 实际氧量 (%)
            llyd_SO2: float, 联络烟道SO2浓度 c3 (mg/m³)
            jyq_SO2: float, 净烟气SO2浓度 c2 (mg/m³)
        Returns:
            float: SO2质量流量 Q (t/h)
        """
        # 计算烟气量 Q = glfl/2200 × 2200000 × (21-yyq_O2)/15
        Q_gas = (glfl / 2200) * 2200000 * ((21 - yyq_o2) / 15)
        # 二级塔: Q=Q_gas*(c3-c2)/1000000000
        return Q_gas * (llyd_SO2 - jyq_SO2) / 1000000000        
    def calculate_fan_flow_mode1(self, Q, alpha, gamma):
        """
        计算模式1的氧化风机流量
        模式1：q2 = (742.5×Q) × (Q×0.4+1.473) × α × γ
        """
        return 742.5 * Q * (Q * 0.4 + 1.473) * alpha * gamma
        
    def calculate_fan_flow_mode2(self, Q, a, b, c, alpha, gamma):
        """
        计算模式2的氧化风机流量
        模式2：q2 = (742.5×Q) × (Q×Q×a+b×Q+c) × α × γ
        """
        return 742.5 * Q * (Q * Q * a + b * Q + c) * alpha * gamma
    
    def calculate_apt_fan_flow(self, Q, alpha):
        """
        计算二级塔的氧化风机流量
        q22 = (742.5×Q) × 3.5 × α
        """
        return 742.5 * Q * 3.5 * alpha
        
    def cal_fan_flow(self, params):
        """
        计算氧化风机流量（同时计算两种模式）
        Args:
            params: dict, 包含以下参数：
                - jzfh: float, 机组负荷
                - yyq_SO2: float, 原烟气SO2
                - jyq_SO2: float, 净烟气SO2
                - llyd_SO2: float, 联络烟道SO2
                - xstjy_PH: float, 吸收塔浆液PH
                - aptjy_PH: float, APT塔浆液PH
                - xstylsy_ND: float, 吸收塔亚硫酸盐浓度 (可选)
        Returns:
            dict: 包含两种模式下吸收塔和APT塔的氧化风机流量
        """
        # # 参数检查
        # required_params = ['jzfh', 'yyq_SO2', 'jyq_SO2', 'llyd_SO2', 'xstjy_PH', 'aptjy_PH','xstylsy_ND']
        # for param in required_params:
        #     if param not in params:
        #         raise ValueError(f"缺少必要参数: {param}")
        
        # # 获取参数
        # jzfh = params['jzfh']
        # yyq_SO2 = params['yyq_SO2']
        # jyq_SO2 = params['jyq_SO2']
        # llyd_SO2 = params['llyd_SO2']
        # xst_ph = params['xstjy_PH']
        # apt_ph = params['aptjy_PH']
        # xstylsy_ND = params.get('xstylsy_ND')  # 吸收塔亚硫酸盐浓度，可选参数
        
        # # 使用类中定义的全局参数
        # a = self.a
        # b = self.b
        # c = self.c
        
        # # 更新PH缓存
        # self.update_ph_buffer(xst_ph, apt_ph)
        
        # # 计算PH均值
        # xst_ph_avg = self.get_ph_avg(self.xst_ph_buffer)
        # apt_ph_avg = self.get_ph_avg(self.apt_ph_buffer)
        
        # # 计算PH修正系数
        # xst_alpha = self.calculate_ph_correction(xst_ph_avg)
        # apt_alpha = self.calculate_ph_correction(apt_ph_avg)
        
        # # 计算亚硫酸盐浓度修正系数
        # sulfite_gamma = self.calculate_sulfite_correction(xstylsy_ND)
        
        # # 计算SO2质量流量
        # xst_Q = self.calculate_so2_mass_flow(jzfh, yyq_SO2, llyd_SO2)
        # apt_Q = self.calculate_so2_mass_flow_apt(jzfh, llyd_SO2, jyq_SO2)
        # 参数检查
        required_params = ['glfl', 'yyq_O2', 'yyq_SO2', 'jyq_SO2', 'llyd_SO2', 'xstjy_PH', 'aptjy_PH','xstylsy_ND']
        for param in required_params:
            if param not in params:
                raise ValueError(f"缺少必要参数: {param}")
        
        # 获取参数
        glfl = params['glfl']         # 机组送风量
        yyq_o2 = params['yyq_O2']     # 实际氧量
        yyq_SO2 = params['yyq_SO2']
        jyq_SO2 = params['jyq_SO2']
        llyd_SO2 = params['llyd_SO2']
        xst_ph = params['xstjy_PH']
        apt_ph = params['aptjy_PH']
        xstylsy_ND = params.get('xstylsy_ND')  # 吸收塔亚硫酸盐浓度，可选参数
        
        # 使用类中定义的全局参数
        a = self.a
        b = self.b
        c = self.c
        
        # 更新PH缓存
        self.update_ph_buffer(xst_ph, apt_ph)
        
        # 计算PH均值
        xst_ph_avg = self.get_ph_avg(self.xst_ph_buffer)
        apt_ph_avg = self.get_ph_avg(self.apt_ph_buffer)
        
        # 计算PH修正系数
        xst_alpha = self.calculate_ph_correction(xst_ph_avg)
        apt_alpha = self.calculate_ph_correction(apt_ph_avg)
        
        # 计算亚硫酸盐浓度修正系数
        sulfite_gamma = self.calculate_sulfite_correction(xstylsy_ND)
        
        # 计算SO2质量流量
        xst_Q = self.calculate_so2_mass_flow(glfl, yyq_o2, yyq_SO2, llyd_SO2)
        apt_Q = self.calculate_so2_mass_flow_apt(glfl, yyq_o2, llyd_SO2, jyq_SO2)
        # 计算模式1的氧化风机流量 - 吸收塔
        xst_fan_flow_mode1 = self.calculate_fan_flow_mode1(xst_Q, xst_alpha, sulfite_gamma)
        
        # 计算模式2的氧化风机流量 - 吸收塔
        xst_fan_flow_mode2 = self.calculate_fan_flow_mode2(xst_Q, a, b, c, xst_alpha, sulfite_gamma)
        
        # 计算APT塔氧化风机流量 - 使用简化公式
        apt_fan_flow = self.calculate_apt_fan_flow(apt_Q, apt_alpha)
        
        return {
            'xst_fan_flow_mode1': float(xst_fan_flow_mode1),  # 模式1吸收塔氧化风机流量
            'apt_fan_flow_mode1': float(apt_fan_flow),        # APT塔氧化风机流量
            'xst_fan_flow_mode2': float(xst_fan_flow_mode2),  # 模式2吸收塔氧化风机流量
            'apt_fan_flow_mode2': float(apt_fan_flow)         # APT塔氧化风机流量（与模式1相同）
        }
    def get_buffer_status(self):
        """
        获取缓存状态
        Returns:
            dict: 缓存状态信息
        """
        return {
            'buffer_size': self.buffer_size,
            'xst_ph_buffer_count': len(self.xst_ph_buffer),
            'apt_ph_buffer_count': len(self.apt_ph_buffer)
        }


class ProcessControl:
    """脱硫过程控制系统"""
    def __init__(self):
        self.jzgj = JiZhunGongJiang()  # 基准供浆量计算实例
        self.yhfj = YangHuaFengJi()    # 氧化风机计算实例

    def process_data(self, data):
        """
        处理输入数据，计算基准供浆量和氧化风机流量
        即使某一个计算失败也不影响另一个的计算
        Args:
            data: dict, 包含所有输入参数的字典
        Returns:
            dict: 在原始数据基础上添加计算结果
        """
        # 初始化结果字典
        result = data.copy()
        
        # 设置默认值
        default_gjl = {
            'xst_base_flow': 0.0,
            'apt_base_flow': 0.0
        }
        
        default_fj = {
            'xst_fan_flow_mode1': 0.0,
            'apt_fan_flow_mode1': 0.0,
            'xst_fan_flow_mode2': 0.0,
            'apt_fan_flow_mode2': 0.0
        }

        # 1. 计算基准供浆量
        try:
            gjl_result = self.jzgj.cal_ban_flow(data)
            result.update(gjl_result)
        except Exception as e:
            print(f"基准供浆量计算失败: {str(e)}")
            result.update(default_gjl)

        # 2. 计算氧化风机流量
        # try:
        #     yhfj_params = {
        #         'jzfh': data['jzfh'],
        #         'yyq_SO2': data['yyq_SO2'],
        #         'jyq_SO2':data['jyq_SO2'],
        #         'xstjy_PH': data['xstjy_PH'],
        #         'aptjy_PH': data['aptjy_PH'],
        #         'llyd_SO2':data['llyd_SO2'],
        #         'xstylsy_ND':data['xstylsy_ND']
        #     }
        #     fj_result = self.yhfj.cal_fan_flow(yhfj_params)
        #     result.update(fj_result)
        # except Exception as e:
        #     print(f"氧化风机流量计算失败: {str(e)}")
        #     result.update(default_fj)
                # 2. 计算氧化风机流量
        try:
            yhfj_params = {
                'glfl': data['glfl'],           # 机组送风量
                'yyq_O2': data['yyq_O2'],       # 实际氧量
                'yyq_SO2': data['yyq_SO2'],
                'jyq_SO2':data['jyq_SO2'],
                'xstjy_PH': data['xstjy_PH'],
                'aptjy_PH': data['aptjy_PH'],
                'llyd_SO2':data['llyd_SO2'],
                'xstylsy_ND':data['xstylsy_ND']
            }
            fj_result = self.yhfj.cal_fan_flow(yhfj_params)
            result.update(fj_result)
        except Exception as e:
            print(f"氧化风机流量计算失败: {str(e)}")
            result.update(default_fj)
        return result
    def get_buffer_status(self):
        """获取PH值缓存状态"""
        return self.yhfj.get_buffer_status()

# 使用示例
if __name__ == "__main__":
    # 创建处理实例
    processor = ProcessControl()
    
    # 准备输入数据（只包含必要的过程参数）
    test_data = {
        # 'jzfh': 600,         # 机组负荷
        'glfl': 2000,       # 机组送风量
        'zml':200,
        'yyq_O2':5,
        'yyq_SO2': 2000,     # 原烟气SO2浓度
        'jyq_SO2': 200,      # 净烟气SO2浓度
        'xstshsjy_MD': 1200,    # 吸收塔石灰石浆液密度
        'aptshsjy_MD': 1200,    # APT塔石灰石浆液密度
        'xstjy_PH': 5.5,     # 吸收塔浆液PH
        'aptjy_PH': 5.8,      # APT塔浆液PH
        'llyd_SO2':220,
        'xstylsy_ND':1000
    }
    
    try:
        # 计算结果
        result = processor.process_data(test_data)
        
        # 输出结果
        print("输入数据和计算结果:")
        for key, value in result.items():
            print(f"{key}: {value}")
        
    except Exception as e:
        print(f"计算出错: {str(e)}")