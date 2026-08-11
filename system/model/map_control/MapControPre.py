import datetime
import json
import os
import socket
import threading
import traceback
import logging
from concurrent.futures.thread import ThreadPoolExecutor
from sqlalchemy import create_engine

from system.base.LogUntil import setup_log
from system.base.config.SysConfig import config
# from system.model.map_control.MapControlFeiXian import MapControl  # 包含预测PH和泵推荐，泡沫层高度
# from system.model.map_control.Main_control import MainControl  # 计算基准工匠和氧化风机
from system.model.map_control.cluster.online_cluster import RealtimeProcessor  # 进行工况分类，泵组合推荐，PH预测
# logging = logging.getLogger("slave")
logging = setup_log("map_control_pre")


class MapControPre:

    def __init__(self):

        self.engine = create_engine(config["dbconnetion"])
        self.contro_table_name = "t_model_result_" + str(datetime.datetime.now().year) + "_" + str(
            datetime.datetime.now().month)

        #self.map_control = MapControl(stack_len=5, deque_max_len=300000, comm_T=1, samp_N=2)

        #self.frist_tower_main_control = MainControl()
        #self.second_tower_main_control = MainControl()
        self.realtime_processor = RealtimeProcessor()  # 进行工况分类，泵组合推荐，PH预测
        self.pre_time = None
        self.pumplist = None
        self.s_target = None
        self.get_new_table_name()


    # def get_new_table_name(self):
    #     """
    #     lixing: 获取最新的表名
    #     :return:
    #     """
    #     result = self.engine.execute("select tablename from pg_tables where schemaname ='public'").fetchall()
    #     djh = {
    #         "t_model_result": [],
    #     }
    #     for i in result:
    #         if str(i[0]).startswith("t_model_result"):
    #             djh["t_model_result"].append(i[0])
    #
    #     for r in djh.keys():
    #         djh[r] = (sorted(djh[r], reverse=True))
    #
    #     self.contro_table_name = djh["t_model_result"][0]
    def get_new_table_name(self):
        """
        lixing: 获取最新的表名，按年月数字排序
        :return:
        """
        result = self.engine.execute("select tablename from pg_tables where schemaname ='public'").fetchall()

        # 收集所有以t_model_result开头的表名
        model_result_tables = []
        for i in result:
            if str(i[0]).startswith("t_model_result"):
                model_result_tables.append(i[0])

        # 自定义排序函数，按年份和月份的数字大小排序
        def custom_sort_key(table_name):
            try:
                parts = table_name.split('_')
                if len(parts) >= 5:
                    year = int(parts[3])
                    month = int(parts[4])
                    return (year, month)
            except (ValueError, IndexError):
                pass
            # 格式不符合预期时返回最小值
            return (0, 0)

        # 按自定义排序规则降序排列
        sorted_tables = sorted(model_result_tables, key=custom_sort_key, reverse=True)

        # 如果存在表，则取最新的一个
        if sorted_tables:
            self.contro_table_name = sorted_tables[0]
        else:
            # 如果没有找到任何表，可以设置一个默认值或抛出异常
            self.contro_table_name = None  # 或者使用默认表名
    # def load_min_max_settings(self):

    #     try:

    #         _config_path = f'{config["base_path"]}/settings-defaults.json'
    #         if os.path.exists(_config_path):
    #             f = open(_config_path, "r",encoding='utf-8')
    #             content = f.read()
    #             f.close()
    #         else:
    #             f = open(f'{config["base_path"]}/settings-defaults.json', "r",encoding='utf-8')
    #             content = f.read()
    #             f.close()

    #         min_max_settings: dict = json.loads(content)
    #         min_max_settings = {k: float(y) for k, y in min_max_settings.items()}

    #         self.load_min = min_max_settings["load_min"]
    #         self.load_max = min_max_settings["load_max"]

    #         self.s_in_cur_min = min_max_settings["s_in_cur_min"]
    #         self.s_in_cur_max = min_max_settings["s_in_cur_max"]

    #         self.s_out_cur_min = min_max_settings["s_out_cur_min"]
    #         self.s_out_cur_max = min_max_settings["s_out_cur_max"]

    #         self.P1_yxt_min = min_max_settings["P1_yxt_min"]
    #         self.P1_yxt_max = min_max_settings["P1_yxt_max"]

    #         self.P2_yxt_min = min_max_settings["P2_yxt_min"]
    #         self.P2_yxt_max = min_max_settings["P2_yxt_max"]

    #         self.yxtjymd_min = min_max_settings["yxtjymd_min"]
    #         self.yxtjymd_max = min_max_settings["yxtjymd_max"]

    #         self.yhfl_yxt_min = min_max_settings["yhfl_yxt_min"]
    #         self.yhfl_yxt_max = min_max_settings["yhfl_yxt_max"]

    #         self.P1_xst_min = min_max_settings["P1_xst_min"]
    #         self.P1_xst_max = min_max_settings["P1_xst_max"]

    #         self.P2_xst_min = min_max_settings["P2_xst_min"]
    #         self.P2_xst_max = min_max_settings["P2_xst_max"]

    #         self.xstjymd_min = min_max_settings["xstjymd_min"]
    #         self.xstjymd_max = min_max_settings["xstjymd_max"]

    #         self.yhfl_xst_min = min_max_settings["yhfl_xst_min"]
    #         self.yhfl_xst_max = min_max_settings["yhfl_xst_max"]

    #         self.ph_min = min_max_settings["ph_min"]
    #         self.ph_max = min_max_settings["ph_max"]

    #     except Exception as e:
    #         logging.error(f"解析最大最小值配置时出现错误：{e}")
    #         traceback.print_exc()
    
    # def map_console(self, row):
    #     """
    #     lixing：执行 mapcontrol 模型
    #     :param s_for_d:
    #     :param p_for_d:
    #     :param f_for_d:
    #     :param row:
    #     :param pump:
    #     :param target_so2:
    #     :return:
    #     """
    #     try:
    #         # load settings
    #         self.load_min_max_settings()

    #         if self.map_control:
    #             pump_status_optim = [0,0,0,0,0,0,0]
    #             pump_status_advice = [0,0,0,0,0,0,0]
    #             ph_frist_pre = 0
    #             ph_second_pre = 0
    #             so2_out_pre = 0
    #             load_msg = 0
    #             yyq_msg = 0
    #             ph_yxt_msg = 0
    #             ph_xst_msg = 0
    #             H_yxt = 0
    #             H_xst = 0

    #             try:
    #                 logging.info(f"""\n
    #                 map_control/input:\n
    #                 load={load}, s_in_cur={s_in_cur}, pump_status_cur={pump_cur_status}, p_cur_1_yxt={p_cur_1_yxt},
    #                 p_cur_2_yxt={p_cur_2_yxt}, p_cur_1_xst={p_cur_1_xst}, p_cur_2_xst={p_cur_2_xst},
    #                 s_out_cur={s_out_cur}, P1_yxt={P1_yxt}, P2_yxt={P2_yxt}, yxtjymd={yxtjymd},
    #                 yhfl_yxt={yhfl_yxt}, P1_xst={P1_xst}, P2_xst={P2_xst}, xstjymd={xstjymd}, yhfl_xst={yhfl_xst},
                    
    #                 load_min={self.load_min}, load_max={self.load_max}, s_in_cur_min={self.s_in_cur_min}, s_in_cur_max={self.s_in_cur_max},
    #                 s_out_cur_min={self.s_out_cur_min}, s_out_cur_max={self.s_out_cur_max}, P1_yxt_min={self.P1_yxt_min}, P1_yxt_max={self.P1_yxt_max},
    #                 P2_yxt_min={self.P2_yxt_min}, P2_yxt_max={self.P2_yxt_max}, yxtjymd_min={self.yxtjymd_min}, yxtjymd_max={self.yxtjymd_max},
    #                 yhfl_yxt_min={self.yhfl_yxt_min}, yhfl_yxt_max={self.yhfl_yxt_max}, P1_xst_min={self.P1_xst_min}, P1_xst_max={self.P1_xst_max},
    #                 P2_xst_min={self.P2_xst_min}, P2_xst_max={self.P2_xst_max}, xstjymd_min={self.xstjymd_min}, xstjymd_max={self.xstjymd_max},
    #                 yhfl_xst_min={self.yhfl_xst_min}, yhfl_xst_max={self.yhfl_xst_max}""")

    #                 pump_status_optim, pump_status_advice, ph_frist_pre, ph_second_pre, so2_out_pre, load_msg, yyq_msg, ph_yxt_msg, ph_xst_msg, H_yxt, H_xst = self.map_control.main_control(
    #                     load=load, s_in_cur=s_in_cur, pump_status_cur=pump_cur_status, p_cur_1_yxt=p_cur_1_yxt,
    #                     p_cur_2_yxt=p_cur_2_yxt, p_cur_1_xst=p_cur_1_xst, p_cur_2_xst=p_cur_2_xst,
    #                     s_out_cur=s_out_cur, P1_yxt=P1_yxt, P2_yxt=P2_yxt, yxtjymd=yxtjymd,
    #                     yhfl_yxt=yhfl_yxt, P1_xst=P1_xst, P2_xst=P2_xst, xstjymd=xstjymd, yhfl_xst=yhfl_xst,

    #                     load_min=self.load_min, load_max=self.load_max, s_in_cur_min=self.s_in_cur_min,
    #                     s_in_cur_max=self.s_in_cur_max,
    #                     s_out_cur_min=self.s_out_cur_min, s_out_cur_max=self.s_out_cur_max, P1_yxt_min=self.P1_yxt_min,
    #                     P1_yxt_max=self.P1_yxt_max,
    #                     P2_yxt_min=self.P2_yxt_min, P2_yxt_max=self.P2_yxt_max, yxtjymd_min=self.yxtjymd_min,
    #                     yxtjymd_max=self.yxtjymd_max,
    #                     yhfl_yxt_min=self.yhfl_yxt_min, yhfl_yxt_max=self.yhfl_yxt_max, P1_xst_min=self.P1_xst_min,
    #                     P1_xst_max=self.P1_xst_max,
    #                     P2_xst_min=self.P2_xst_min, P2_xst_max=self.P2_xst_max, xstjymd_min=self.xstjymd_min,
    #                     xstjymd_max=self.xstjymd_max,
    #                     yhfl_xst_min=self.yhfl_xst_min, yhfl_xst_max=self.yhfl_xst_max
    #                 )

    #                 logging.info(f"""\n
    #                     map_control/result:\n
    #                     pump_status_optim={pump_status_optim}, pump_status_advice={pump_status_advice}, ph_frist_pre={ph_frist_pre}, ph_second_pre={ph_second_pre}, 
    #                     so2_out_pre={so2_out_pre}, load_msg={load_msg}, yyq_msg={yyq_msg}, ph_yxt_msg={ph_yxt_msg}, ph_xst_msg={ph_xst_msg}, H_yxt={H_yxt}, H_xst={H_xst}
    #                 """)

    #             except Exception as ex:
    #                 traceback.print_exc()
    #                 logging.error(f"error: {ex}")

    #             flow_bs_1 = 0
    #             flow_bs_msg_1 = 0
    #             q_bs_1 = 0
    #             q_bs_msg_1 = 0

    #             flow_bs_2 = 0
    #             flow_bs_msg_2 = 0
    #             q_bs_2 = 0
    #             q_bs_msg_2 = 0

    #             try:

    #                 K = 0.0011
    #                 B = -1.0059

    #                 a = 0.677
    #                 b = 1.6
    #                 c = 0
    #                 mode_flag = "1"

    #                 flow_bs_1, flow_bs_msg_1, q_bs_1, q_bs_msg_1 = self.frist_tower_main_control.main(
    #                      load=load,
    #                      s_in_cur=s_in_cur,
    #                      s_out_cur=s_out_cur,
    #                      p_cur_1=p_cur_1_yxt,
    #                      p_cur_2=p_cur_2_yxt,
    #                      shsjymd=yxtjymd,
    #                      load_min=self.load_min,
    #                      load_max=self.load_max,
    #                      s_in_cur_min=self.s_in_cur_min,
    #                      s_in_cur_max=self.s_in_cur_max,
    #                      s_out_cur_min=self.s_out_cur_min,
    #                      s_out_cur_max=self.s_out_cur_max,
    #                      shsjymd_min=self.yxtjymd_min,
    #                      shsjymd_max=self.yxtjymd_max,
    #                      ph_min=self.ph_min,
    #                      ph_max=self.ph_max,
    #                      K=K,
    #                      B=B, a=a, b=b, c=c,
    #                      mode_flag=mode_flag)

    #                 logging.info(f"""一级塔公式计算的基准供浆值为={flow_bs_1}\n,
    #                 一级塔基准供浆输入参数的告警信息={flow_bs_msg_1}\n,
    #                 一级塔理论氧化风机量={q_bs_1}\n,
    #                 一级塔理论氧化风机输入参数的告警信息={q_bs_msg_1}\n""")

    #                 flow_bs_2, flow_bs_msg_2, q_bs_2, q_bs_msg_2 = self.second_tower_main_control.main(load=load,
    #                       s_in_cur=s_in_cur,
    #                       s_out_cur=s_out_cur,
    #                       p_cur_1=p_cur_1_xst,
    #                       p_cur_2=p_cur_2_xst,
    #                       shsjymd=xstjymd,
    #                       load_min=self.load_min,
    #                       load_max=self.load_max,
    #                       s_in_cur_min=self.s_in_cur_min,
    #                       s_in_cur_max=self.s_in_cur_max,
    #                       s_out_cur_min=self.s_out_cur_min,
    #                       s_out_cur_max=self.s_out_cur_max,
    #                       shsjymd_min=self.xstjymd_min,
    #                       shsjymd_max=self.xstjymd_max,
    #                       ph_min=self.ph_min,
    #                       ph_max=self.ph_max,
    #                       K=K,
    #                       B=B, a=a, b=b, c=c,
    #                       mode_flag=mode_flag)

    #                 logging.info(f"""二级塔公式计算的基准供浆值为={flow_bs_2}\n,
    #                 二级塔基准供浆输入参数的告警信息={flow_bs_msg_2}\n,
    #                 二级塔理论氧化风机量={q_bs_2}\n,
    #                 二级塔理论氧化风机输入参数的告警信息={q_bs_msg_2}\n""")

    #             except Exception as ex:
    #                 traceback.print_exc()
    #                 logging.error(f"error:{ex}")

    #             self.pumplist = {
    #                 "pump_status_optim": pump_status_optim,
    #                 "pump_status_advice": pump_status_advice,
    #                 "ph_frist_pre": ph_frist_pre,
    #                 "ph_second_pre": ph_second_pre,
    #                 "so2_out_pre": so2_out_pre,
    #                 "load_msg": load_msg,
    #                 "yyq_msg": yyq_msg,
    #                 "ph_yxt_msg": ph_yxt_msg,
    #                 "ph_xst_msg": ph_xst_msg,
    #                 "H_yxt": H_yxt,
    #                 "H_xst": H_xst,

    #                 "mean_ph_yxt": round(self.map_control.mean_ph_yxt, 2),
    #                 "mean_ph_yxt_4h": round(self.map_control.mean_ph_yxt_4h,2),
    #                 "mean_ph_yxt_8h": round(self.map_control.mean_ph_yxt_8h,2),
    #                 "mean_ph_yxt_1d": round(self.map_control.mean_ph_yxt_1d,2),
    #                 "mean_ph_yxt_7d": round(self.map_control.mean_ph_yxt_7d,2),
    #                 "std_ph_yxt_4h": round(self.map_control.std_ph_yxt_4h,2),
    #                 "std_ph_yxt_8h": round(self.map_control.std_ph_yxt_8h,2),
    #                 "std_ph_yxt_1d": round(self.map_control.std_ph_yxt_1d,2),
    #                 "std_ph_yxt_7d": round(self.map_control.std_ph_yxt_7d,2),

    #                 "mean_ph_xst": round(self.map_control.mean_ph_xst, 2),
    #                 "mean_ph_xst_4h": round(self.map_control.mean_ph_xst_4h,2),
    #                 "mean_ph_xst_8h": round(self.map_control.mean_ph_xst_8h,2),
    #                 "mean_ph_xst_1d": round(self.map_control.mean_ph_xst_1d,2),
    #                 "mean_ph_xst_7d": round(self.map_control.mean_ph_xst_7d,2),
    #                 "std_ph_xst_4h": round(self.map_control.std_ph_xst_4h,2),
    #                 "std_ph_xst_8h": round(self.map_control.std_ph_xst_8h,2),
    #                 "std_ph_xst_1d": round(self.map_control.std_ph_xst_1d,2),
    #                 "std_ph_xst_7d": round(self.map_control.std_ph_xst_7d,2),

    #                 "mean_s_out": round(self.map_control.mean_s_out, 2),
    #                 "mean_s_out_4h": round(self.map_control.mean_s_out_4h,2),
    #                 "mean_s_out_8h": round(self.map_control.mean_s_out_8h,2),
    #                 "mean_s_out_1d": round(self.map_control.mean_s_out_1d,2),
    #                 "mean_s_out_7d": round(self.map_control.mean_s_out_7d,2),
    #                 "std_s_out_4h": round(self.map_control.std_s_out_4h,2),
    #                 "std_s_out_8h": round(self.map_control.std_s_out_8h,2),
    #                 "std_s_out_1d": round(self.map_control.std_s_out_1d,2),
    #                 "std_s_out_7d": round(self.map_control.std_s_out_7d,2),

    #                 "tower_1": {
    #                     "flow_bs_1": flow_bs_1,
    #                     "flow_bs_msg_1":flow_bs_msg_1,
    #                     "q_bs_1":q_bs_1,
    #                     "q_bs_msg_1":q_bs_msg_1
    #                 },

    #                 "tower_2": {
    #                     "flow_bs_2": flow_bs_2,
    #                     "flow_bs_msg_2": flow_bs_msg_2,
    #                     "q_bs_2": q_bs_2,
    #                     "q_bs_msg_2": q_bs_msg_2
    #                 },

    #                 "data": {
    #                     "load": float(load[0]),
    #                     "s_in_cur": float(s_in_cur[0]),
    #                     "pump_status_cur": pump_cur_status,
    #                     "p_cur_1_yxt": float(p_cur_1_yxt[0]),
    #                     "p_cur_2_yxt": float(p_cur_2_yxt[0]),
    #                     "p_cur_1_xst": float(p_cur_1_xst[0]),
    #                     "p_cur_2_xst": float(p_cur_2_xst[0]),
    #                     "s_out_cur": float(s_out_cur[0]),
    #                     "P1_yxt": float(P1_yxt[0]),
    #                     "P2_yxt": float(P2_yxt[0]),
    #                     "yxtjymd": float(yxtjymd[0]),
    #                     "yhfl_yxt": float(yhfl_yxt[0]),
    #                     "P1_xst": float(P1_xst[0]),
    #                     "P2_xst": float(P2_xst[0]),
    #                     "xstjymd": float(xstjymd[0]),
    #                     "yhfl_xst": float(yhfl_xst[0])
    #                 }
    #             }

    #         return self.pumplist
    #     except Exception as e:
    #         traceback.print_exc()
    #         logging.error("map_console error -----> %s", str(e))
    def map_console(self, row):
        """
        直接调用RealtimeProcessor，返回其所有结果，并记录输入输出日志
        """
        try:
            # 记录输入数据
            #logging.info(f"\n[map_console] 输入数据 row: {json.dumps(row, ensure_ascii=False, default=str)}")

            # 1. 直接用RealtimeProcessor处理
            result = self.realtime_processor.process_realtime_data(row)
            if result is None:
                logging.error("[map_console] RealtimeProcessor返回结果为空")
                return None

            # 记录输出结果
            logging.info(f"\n[map_console] 输出结果 result: {json.dumps(result, ensure_ascii=False, default=str)}")

            # 2. 直接返回所有内容（推荐）
            return result

        except Exception as e:
            logging.error(f"map_console error: {str(e)}")
            
            traceback.print_exc()
            return None
