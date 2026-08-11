# -*- coding: utf-8 -*-
################################################################################
"""
@date: 2025-03-20
@author: Kang
@title: Main_control.py
@description:
    主控函数（费县）
@version: 0.0.1（费县）
"""
################################################################################
import numpy as np

from system.model.map_control.yhfj_and_jzgj_model import ProcessControl


class MainControl(object):

    def __init__(self):
        self.process_control = ProcessControl()

    ##### 主控函数
    def main(self, **kwargs):
        '''
        input parms:
            load: 机组负荷
            s_in_cur：入口SO2浓度（原烟气）（yyqSO2）
            s_out_cur：出口SO2浓度当前值（净烟气）（jyqSO2）
            shsjymd:石灰石浆液密度（shsjymd）
            p_cur: 吸收塔的ph当前值(xstjyPH1和xstjyPH2取平均值)
            K: 基础供浆用参数，含固率与石灰石的拟合曲线的斜率
            B: 基础供浆用参数，含固率与石灰石的拟合曲线的截距
            a：氧化风机用参数，氧化标定系数a
            b：氧化风机用参数，氧化标定系数b
            c：氧化风机用参数，氧化标定系数c
            mode_flag：氧化风机用参数，选择工作模式“1”或“2”

        :return parms:
            flow_bs：基准供浆值
            flow_bs_msg：基准供浆输入参数取值是否在正常范围提示信息
            q_bs：氧化风机量
            q_bs_msg：氧化风机量输入参数取值是否在正常范围提示信息
        '''
        ###1、输入参数预处理
        ## (1) 必须输入的参数  load, s_in_cur, s_out_cur, shsjymd, p_cur, K, B, a, b, c, mode_flag
        load, s_in_cur, s_out_cur, shsjymd, p_cur_1, K, B, a, b, c, mode_flag = \
            kwargs['load'], kwargs['s_in_cur'], kwargs['s_out_cur'], kwargs['shsjymd'], kwargs['p_cur_1'], kwargs['K'], kwargs['B'],kwargs['a'],kwargs['b'],kwargs['c'], kwargs['mode_flag']
        ## (2) 可选输入的参数
        if 'p_cur_2' in kwargs.keys():
            p_cur_2 = kwargs['p_cur_2']
        else:
            p_cur_2 = p_cur_1
        ## (3) 部分变量（除pump_status_cur以外的变量）转为numpy.array格式
        load, s_in_cur, s_out_cur, shsjymd, p_cur_1, p_cur_2 = map(np.array,[load, s_in_cur, s_out_cur, shsjymd, p_cur_1,p_cur_2])
        # 处理吸收塔中的PH1和PH2（取均值）
        p_cur = np.round((p_cur_1+p_cur_2)/2,4)
        ##（4）可选输入的参数（参数的正常范围）
        # 机组负荷的范围
        load_min = kwargs['load_min'] if 'load_min' in kwargs.keys() else 150
        load_max = kwargs['load_max'] if 'load_max' in kwargs.keys() else 750
        # 入口SO2浓度的范围
        s_in_cur_min = kwargs['s_in_cur_min'] if 's_in_cur_min' in kwargs.keys() else 200
        s_in_cur_max = kwargs['s_in_cur_max'] if 's_in_cur_max' in kwargs.keys() else 6000
        # 出口SO2浓度的范围
        s_out_cur_min = kwargs['s_out_cur_min'] if 's_out_cur_min' in kwargs.keys() else 0
        s_out_cur_max = kwargs['s_out_cur_max'] if 's_out_cur_max' in kwargs.keys() else 200
        # 石灰石浆液密度的范围
        shsjymd_min = kwargs['shsjymd_min'] if 'shsjymd_min' in kwargs.keys() else 1000
        shsjymd_max = kwargs['shsjymd_max'] if 'shsjymd_max' in kwargs.keys() else 1500
        # PH值的范围
        ph_min = kwargs['ph_min'] if 'ph_min' in kwargs.keys() else 4
        ph_max = kwargs['ph_max'] if 'ph_max' in kwargs.keys() else 8

        ###2、基准供浆计算
        data = {
            "jzfh": float(load.mean()),
            "glfl": float(np.array(kwargs.get("glfl", load)).mean()),
            "yyq_O2": float(np.array(kwargs.get("yyq_O2", 6.0)).mean()),
            "yyq_SO2": float(s_in_cur.mean()),
            "jyq_SO2": float(s_out_cur.mean()),
            "xstshsjy_MD": float(shsjymd.mean()),
            "aptshsjy_MD": float(np.array(kwargs.get("aptshsjy_MD", shsjymd)).mean()),
            "xstjy_PH": float(p_cur.mean()),
            "aptjy_PH": float(np.array(kwargs.get("aptjy_PH", p_cur)).mean()),
            "llyd_SO2": float(np.array(kwargs.get("llyd_SO2", s_out_cur)).mean()),
            "xstylsy_ND": float(np.array(kwargs.get("xstylsy_ND", 0.0)).mean()),
        }
        result = self.process_control.process_data(data)
        flow_bs = np.array([result.get("xst_base_flow", 0.0)])
        flow_bs_msg = ""

        ###3、氧化风机计算
        q_bs = np.array([result.get("xst_fan_flow_mode1", 0.0)])
        q_bs_msg = ""

        return flow_bs, flow_bs_msg, q_bs, q_bs_msg

if __name__ == '__main__':
    # 机组负荷;(jzfh)
    load = [[600], [500], [400], [450], [350], [380], [560], [350], [380], [560]]
    #入口SO2（原烟气）浓度;(yyqSO2)
    # s_in_cur = [[2100],[2740],[2119],[1977],[2200],[2238],[2277]]
    # s_in_cur = [[2100], [-1], [2119], [3], [2200], [2238], [2277], [2200], [2238], [2277]]
    # s_in_cur = [[2100], [-1], [2119], [3], [2200], [0], [0], [0], [-10], [2277]]
    s_in_cur = [[-2], [2230], [2119], [0], [0], [2238], [2277], [2200], [2238], [2277]]
    # 出口SO2（净烟气）浓度预测值;(jyqSO2)
    s_out_cur = [[24], [11], [12], [3], [6], [30], [36], [6], [30], [36]]
    # 吸收塔的当前PH值PH1(xstjyPH1)
    p_cur_1 = [[5.5], [5.7], [5.8], [5.6], [5.6], [5.1],[5.1], [5.6], [5.1],[5.1]]
    # 吸收塔当前PH值PH2(xstjyPH2)
    p_cur_2 = [[5.4], [5.6], [5.7], [5.5], [5.6], [5.2], [5.2], [5.6], [5.2], [5.2]]
    # 石灰石浆液密度;(shsjymd)
    shsjymd = [[1067], [1104], [1144], [1187], [1233], [1283], [2040], [1233], [1283], [2040]]

    K = 0.0011
    B = -1.0059

    a = 0.677
    b = 1.6
    c = 0
    mode_flag = "1"

    ###参数范围参考值
    #机组负荷
    load_min = 150
    load_max = 750
    #入口SO2浓度（原烟气浓度）
    s_in_cur_min = 300
    s_in_cur_max = 5000
    #出口SO2浓度（净烟气浓度）
    s_out_cur_min = 0
    s_out_cur_max = 200
    #石灰石浆液密度
    shsjymd_min = 1000
    shsjymd_max = 1500
    #吸收塔的ph当前值
    ph_min = 4
    ph_max = 8

    ######第一级塔
    frist_tower_main_control =MainControl()
    ######第二级塔
    second_tower_main_control = MainControl()

    for i in range(len(load)):
        print('%' * 50)
        print("第" + str(i + 1) + "个时刻：")
        flow_bs_1, flow_bs_msg_1, q_bs_1, q_bs_msg_1 = frist_tower_main_control.main(load=load[i], s_in_cur=s_in_cur[i], s_out_cur=s_out_cur[i], p_cur_1=p_cur_1[i], p_cur_2=p_cur_2[i], shsjymd=shsjymd[i],
                                                                 load_min=load_min,load_max=load_max,s_in_cur_min=s_in_cur_min,s_in_cur_max=s_in_cur_max,s_out_cur_min=s_out_cur_min,s_out_cur_max=s_out_cur_max,shsjymd_min=shsjymd_min,shsjymd_max=shsjymd_max,ph_min=ph_min,ph_max=ph_max,K=K,B=B,a=a,b=b,c=c,mode_flag=mode_flag)
        print("一级塔公式计算的基准供浆值为：", flow_bs_1)
        print("一级塔基准供浆输入参数的告警信息：", flow_bs_msg_1)
        print("一级塔理论氧化风机量：", q_bs_1)
        print("一级塔理论氧化风机输入参数的告警信息：", q_bs_msg_1)
        print('+' * 25)
        flow_bs_2, flow_bs_msg_2, q_bs_2, q_bs_msg_2 = second_tower_main_control.main(load=load[i], s_in_cur=s_in_cur[i], s_out_cur=s_out_cur[i], p_cur_1=p_cur_1[i], p_cur_2=p_cur_2[i], shsjymd=shsjymd[i],
                                                                 load_min=load_min,load_max=load_max,s_in_cur_min=s_in_cur_min,s_in_cur_max=s_in_cur_max,s_out_cur_min=s_out_cur_min,s_out_cur_max=s_out_cur_max,shsjymd_min=shsjymd_min,shsjymd_max=shsjymd_max,ph_min=ph_min,ph_max=ph_max,K=K,B=B,a=a,b=b,c=c,mode_flag=mode_flag)
        print("二级塔公式计算的基准供浆值为：", flow_bs_1)
        print("二级塔基准供浆输入参数的告警信息：", flow_bs_msg_1)
        print("二级塔理论氧化风机量：", q_bs_1)
        print("二级塔理论氧化风机输入参数的告警信息：", q_bs_msg_1)
