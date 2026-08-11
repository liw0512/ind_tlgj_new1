import datetime
import json
import threading
import time
import traceback
import uuid
from sqlalchemy import create_engine
from system.base.LogUntil import setup_log
from system.base.config.SysConfig import config

logging = setup_log("wc")

class DataHandler():

    def __init__(self,GLOBAL_DATA):
        self.GLOBAL_DATA = GLOBAL_DATA
        self.engine = create_engine(config["dbconnetion"])
        self.lock = threading.Lock()

        self.rt_table_name = "t_data1_rt_" + str(datetime.datetime.now().year) + "_" + str(
            datetime.datetime.now().month)
        self.contro_table_name = "t_model_result_" + str(datetime.datetime.now().year) + "_" + str(
            datetime.datetime.now().month)

        self.init_data_struct()  # 初始化map结构
        self.getNewDataTableName()  # 获取数据库中最新的表名
        self.table_update()  # 数据库表的更新
        self.tj_pre_time = None
        self.real_time = None

        self.change_befor_time = {}  # 回到初始时间
        self.rt_diff_time = None  # 上一次的endtime和这一次的endtime之差
        self.diff_time = 0
        self.mark = {
            "start_time": (datetime.timedelta(hours=-config["search_time"]) + datetime.datetime.now()).strftime(
                "%Y-%m-%d %H:%M:%S"),
            "end_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "args": ["jyq_so2", "pre_jyq_so2", "jzfh"],
            "is_send": False,
            "update_end_time": 1,  # 默认更新结束时间
        }

    def table_update(self):
        if not self.rt_table_name.endswith(
                str(datetime.datetime.now().year) + "_" + str(datetime.datetime.now().month)):
            self.rt_table_name = "t_data1_rt_" + str(datetime.datetime.now().year) + "_" + str(
                datetime.datetime.now().month)
            self.engine.execute(
                f"""
                DROP TABLE IF EXISTS "public".{self.rt_table_name}; 
                CREATE TABLE "public".{self.rt_table_name} 
                ("id" uuid NOT NULL, 
                    "date" timestamp(6) NOT NULL, 
                    "xstshsjy_MD" float8,
                    "yyq_SO2" float8,
                    "jyq_SO2" float8,
                    "yyq_O2" float8,
                    "yyq_LL" float8,
                    "jyq_LL" float8,
                    "xst_YW" float8,
                    "xstjyxhb_ADL" float8,   
                    "xstjyxhb_BDL" float8,
                    "xstjyxhb_CDL" float8,
                    "xstjyxhb_DDL" float8,
                    "xstjyxhb_EDL" float8,
                    "xstyhfj_ADL" float8,
                    "xstjy_PH" float8,
                    "xst_ADL_status" int,
                    "xst_BDL_status" int,
                    "xst_CDL_status" int,
                    "xst_DDL_status" int,
                    "xst_EDL_status" int,
                    "xst_pump_status" varchar(20),
                    "combined_pump_status" varchar(20),
                    "liquid_gas_ratio" float8,
                    "desulfurization_efficiency" float8
                    )
                """
            )
            self.engine.execute('ALTER TABLE "' + self.rt_table_name + '" ADD PRIMARY KEY ("id")')
            # self.engine.execute(
            #     'CREATE INDEX "%s" ON "public".' + self.rt_table_name + ' USING btree ("date" "pg_catalog"."timestamp_ops" ASC NULLS LAST);',
            #     ("index_" + self.rt_table_name))
            self.engine.execute("insert into t_table_name(id,table_name,table_alias) values (%s,%s,%s)", (
                uuid.uuid4(), self.rt_table_name,
                "实时数据表_" + str(datetime.datetime.now().year) + "_" + str(datetime.datetime.now().month)))
        if not self.contro_table_name.endswith(
                str(datetime.datetime.now().year) + "_" + str(datetime.datetime.now().month)):
            self.contro_table_name = "t_model_result_" + str(datetime.datetime.now().year) + "_" + str(
                datetime.datetime.now().month)

            self.engine.execute(
                f'''
                DROP TABLE IF EXISTS "public".{self.contro_table_name}; 
                CREATE TABLE "public".{self.contro_table_name} ( 
                    "id" uuid NOT NULL, 
                    "date" timestamp(6) NOT NULL, 
                    "xstshsjy_MD" float8,
                    "yyq_SO2" float8,
                    "jyq_SO2" float8,
                    "yyq_O2" float8,
                    "yyq_LL" float8,
                    "jyq_LL" float8,
                    "xst_YW" float8,
                    "xstjyxhb_ADL" float8,   
                    "xstjyxhb_BDL" float8,
                    "xstjyxhb_CDL" float8,
                    "xstjyxhb_DDL" float8,
                    "xstjyxhb_EDL" float8,
                    "xstyhfj_ADL" float8,
                    "xstgjb_ADL" float8,
                    "xstjy_PH" float8,
                    "xst_ADL_status" int,
                    "xst_BDL_status" int,
                    "xst_CDL_status" int,
                    "xst_DDL_status" int,
                    "xst_EDL_status" int,
                    "xst_pump_status" varchar(20),
                    "combined_pump_status" varchar(20),
                    "liquid_gas_ratio" float8,
                    "desulfurization_efficiency" float8,
                    "cluster_label" int,
                    "timestamp" timestamp(6),
                    "confidence" float8,
                    "recommended_pump" varchar(20),
                    "drop_flag" varchar(20),
                    "suggested_xst_ph" float8,
                    "event_type" varchar(80),
                    "is_stable" varchar(20),
                    "cache_size" int,
                    "final_condition" int
                )''')

            self.engine.execute(
                'ALTER TABLE "public".' + self.contro_table_name + ' ADD CONSTRAINT "%s" PRIMARY KEY ("id");',
                ("primary_" + self.contro_table_name))

            # self.engine.execute(
            #     'CREATE INDEX "%s" ON "public".' + self.contro_table_name + '  USING btree ("timestamp" "pg_catalog"."timestamp_ops" ASC NULLS LAST);',
            #     ("index_" + self.contro_table_name))

            self.engine.execute("insert into t_table_name(id,table_name,table_alias) values (%s,%s,%s)", (
                uuid.uuid4(), self.contro_table_name,
                "推荐结果表_" + str(datetime.datetime.now().year) + "_" + str(datetime.datetime.now().month)))

    def init_data_struct(self):

        self.columns = ["zml", "jyq_so2", "yyq_o2", "jzfh", "yyq_wd", "yyq_ll", "jyq_so2", "jyq_ll", "sgjy_md",
                        "sgjy_ph1",
                        "sgjy_ph2",
                        "xst_yw", "sgjy_ll", "jytjf_wzfk", "jyxhb_dl1", "jyxhb_dl2", "jyxhb_dl3", "jyxhb_dl4"]
        self.send_obj = {"chart": [],
                         "data": {}
                         }
        self.zml_map = {}
        self.yyq_so2_map = {}
        self.yyq_o2_map = {}
        self.jzfh_map = {}
        self.yyq_wd_map = {}
        self.yyq_ll_map = {}
        self.jyq_so2_map = {}
        self.jyq_ll_map = {}
        self.sgjy_md_map = {}
        self.sgjy_ph1_map = {}
        self.sgjy_ph2_map = {}
        self.xst_yw_map = {}
        self.sgjy_ll_map = {}
        self.jytjf_wzfk_map = {}
        self.jyxhb_dl1_map = {}
        self.jyxhb_dl2_map = {}
        self.jyxhb_dl3_map = {}
        self.jyxhb_dl4_map = {}
        self.pre_jyq_so2_map = {}
        self.pre_gjll_map = {}
        self.pre_ph_map = {}
        self.tjph_map = {}
        self.tjgjll_map = {}
        self.advice_map = {}

        self.folw_base_tmp_map = {}

    def getNewDataTableName(self):
        result = self.engine.execute("select tablename from pg_tables where schemaname ='public'").fetchall()
        djh = {
            "t_data1_filter_rt": [],
            "t_data1_rt": [],
            "t_model_result": [],
        }

        # 收集表名
        for i in result:
            table_name = str(i[0])
            if table_name.startswith("t_data1_filter_rt"):
                djh["t_data1_filter_rt"].append(table_name)
            if table_name.startswith("t_data1_rt"):
                djh["t_data1_rt"].append(table_name)
            if table_name.startswith("t_model_result"):
                djh["t_model_result"].append(table_name)

        # 自定义排序函数
        def custom_sort_key(table_name):
            try:
                # 提取表名末尾的数字部分
                parts = table_name.split('_')
                # 假设最后几个部分是年份和月份
                if len(parts) >= 2:
                    # 尝试将最后两个部分解析为年份和月份
                    year_part = parts[-2] if len(parts) >= 2 else "0"
                    month_part = parts[-1] if len(parts) >= 1 else "0"

                    # 尝试将它们转换为整数
                    year = int(year_part) if year_part.isdigit() else 0
                    month = int(month_part) if month_part.isdigit() else 0

                    return (year, month)
            except (ValueError, IndexError):
                pass
            return (0, 0)

        # 对每种表名应用自定义排序
        for r in djh.keys():
            djh[r] = sorted(djh[r], key=custom_sort_key, reverse=True)

        # 确保有表名可用
        if djh["t_data1_rt"] and djh["t_model_result"]:
            self.rt_table_name = djh["t_data1_rt"][0]
            self.contro_table_name = djh["t_model_result"][0]
        else:
            # 处理没有表的情况
            if not djh["t_data1_rt"]:
                self.rt_table_name = None  # 或设置默认值
            if not djh["t_model_result"]:
                self.contro_table_name = None  # 或设置默认值
    # def getNewDataTableName(self):
    #     result = self.engine.execute("select tablename from pg_tables where schemaname ='public'").fetchall()
    #     djh = {
    #         "t_data1_filter_rt": [],
    #         "t_data1_rt": [],
    #         "t_model_result": [],
    #     }
    #     for i in result:
    #         if str(i[0]).startswith("t_data1_filter_rt"):
    #             djh["t_data1_filter_rt"].append(i[0])
    #         if str(i[0]).startswith("t_data1_rt"):
    #             djh["t_data1_rt"].append(i[0])
    #         if str(i[0]).startswith("t_model_result"):
    #             djh["t_model_result"].append(i[0])
    #     for r in djh.keys():
    #         djh[r] = (sorted(djh[r], reverse=True))
    #
    #     self.rt_table_name = djh["t_data1_rt"][0]
    #     self.contro_table_name = djh["t_model_result"][0]

    def timing_clean_data(self):
        logging.info("定时清除数据已经启动")
        clean_time = (datetime.timedelta(days=-config["save_time"]) + datetime.datetime.now()).timestamp()
        self.lock.acquire()
        try:

            if self.zml_map:
                List = []
                for i in self.zml_map.keys():
                    if i <= clean_time:
                        List.append(i)
                for i in List:
                    self.zml_map.pop(i)
            if self.yyq_so2_map:

                List = []
                for i in self.yyq_so2_map.keys():
                    if i <= clean_time:
                        List.append(i)
                for i in List:
                    self.yyq_so2_map.pop(i)
            if self.yyq_o2_map:

                List = []
                for i in self.yyq_o2_map.keys():
                    if i <= clean_time:
                        List.append(i)
                for i in List:
                    self.yyq_o2_map.pop(i)
            if self.jzfh_map:

                List = []
                for i in self.jzfh_map.keys():
                    if i <= clean_time:
                        List.append(i)
                for i in List:
                    self.jzfh_map.pop(i)
            if self.yyq_wd_map:

                List = []
                for i in self.yyq_wd_map.keys():
                    if i <= clean_time:
                        List.append(i)
                for i in List:
                    self.yyq_wd_map.pop(i)
            if self.yyq_ll_map:

                List = []
                for i in self.yyq_ll_map.keys():
                    if i <= clean_time:
                        List.append(i)
                for i in List:
                    self.yyq_ll_map.pop(i)
            if self.jyq_so2_map:

                List = []
                for i in self.jyq_so2_map.keys():
                    if i <= clean_time:
                        List.append(i)
                for i in List:
                    self.jyq_so2_map.pop(i)
            if self.jyq_ll_map:

                List = []
                for i in self.jyq_ll_map.keys():
                    if i <= clean_time:
                        List.append(i)
                for i in List:
                    self.jyq_ll_map.pop(i)
            if self.sgjy_md_map:

                List = []
                for i in self.sgjy_md_map.keys():
                    if i <= clean_time:
                        List.append(i)
                for i in List:
                    self.sgjy_md_map.pop(i)
            if self.sgjy_ph1_map:

                List = []
                for i in self.sgjy_ph1_map.keys():
                    if i <= clean_time:
                        List.append(i)
                for i in List:
                    self.sgjy_ph1_map.pop(i)
            if self.sgjy_ph2_map:

                List = []
                for i in self.sgjy_ph2_map.keys():
                    if i <= clean_time:
                        List.append(i)
                for i in List:
                    self.sgjy_ph2_map.pop(i)
            if self.xst_yw_map:

                List = []
                for i in self.xst_yw_map.keys():
                    if i <= clean_time:
                        List.append(i)
                for i in List:
                    self.xst_yw_map.pop(i)
            if self.sgjy_ll_map:

                List = []
                for i in self.sgjy_ll_map.keys():
                    if i <= clean_time:
                        List.append(i)
                for i in List:
                    self.sgjy_ll_map.pop(i)
            if self.jytjf_wzfk_map:

                List = []
                for i in self.jytjf_wzfk_map.keys():
                    if i <= clean_time:
                        List.append(i)
                for i in List:
                    self.jytjf_wzfk_map.pop(i)
            if self.jyxhb_dl1_map:

                List = []
                for i in self.jyxhb_dl1_map.keys():
                    if i <= clean_time:
                        List.append(i)
                for i in List:
                    self.jyxhb_dl1_map.pop(i)
            if self.jyxhb_dl2_map:

                List = []
                for i in self.jyxhb_dl2_map.keys():
                    if i <= clean_time:
                        List.append(i)
                for i in List:
                    self.jyxhb_dl2_map.pop(i)
            if self.jyxhb_dl3_map:

                List = []
                for i in self.jyxhb_dl3_map.keys():
                    if i <= clean_time:
                        List.append(i)
                for i in List:
                    self.jyxhb_dl3_map.pop(i)
            if self.jyxhb_dl4_map:

                List = []
                for i in self.jyxhb_dl4_map.keys():
                    if i <= clean_time:
                        List.append(i)
                for i in List:
                    self.jyxhb_dl4_map.pop(i)
            if self.pre_jyq_so2_map:

                List = []
                for i in self.pre_jyq_so2_map.keys():
                    if i <= clean_time:
                        List.append(i)
                for i in List:
                    self.pre_jyq_so2_map.pop(i)
            if self.pre_ph_map:
                List = []
                for i in self.pre_ph_map.keys():
                    if i <= clean_time:
                        List.append(i)
                for i in List:
                    self.pre_ph_map.pop(i)
            if self.pre_gjll_map:
                List = []
                for i in self.pre_gjll_map.keys():
                    if i <= clean_time:
                        List.append(i)
                for i in List:
                    self.pre_gjll_map.pop(i)
            if self.tjgjll_map:
                List = []
                for i in self.tjgjll_map.keys():
                    if i <= clean_time:
                        List.append(i)
                for i in List:
                    self.tjgjll_map.pop(i)
            if self.tjph_map:
                List = []
                for i in self.tjph_map.keys():
                    if i <= clean_time:
                        List.append(i)
                for i in List:
                    self.tjph_map.pop(i)
        except Exception as e:
            traceback.print_exc()
            logging.error("timing_clean_data 出现问题了===》%s", str(e))
        finally:
            self.lock.release()
        logging.info("定时清除数据已结束")
        threading.Timer(config["save_time"] * 60 * 60 * 24, self.timing_clean_data).start()

    def fill_data(self, befor_time, data, now_time):
        try:
            if befor_time is None and len(data) > 0:
                sorted_list = sorted(data.keys())
                befor_time = max(sorted_list)
            if len(data) == 0:
                return data
            count = now_time - befor_time
            value = data[now_time]
            if count > config["rdstep"] + 3:
                for r in range(1, count):
                    data[befor_time + r] = ""
            else:
                for i in range(1, count):
                    data[befor_time + i] = value

            return data
        except Exception as e:
            traceback.print_exc()
            logging.error("fill_data 的异常信息为==》%s", str(e))

    def fill_pre_data(self, befor_time, data, now_time):
        try:
            if befor_time is None and len(data) > 0:
                sorted_list = sorted(data.keys())
                befor_time = max(sorted_list)
            if len(data) == 0:
                return data
            count = now_time - befor_time
            value = data[now_time]
            if count > config["ptstep"] + 3:
                for r in range(1, count):
                    data[befor_time + r] = ""

            else:
                for i in range(1, count):
                    data[befor_time + i] = value

            return data
        except Exception as e:
            traceback.print_exc()
            logging.error("fill_pre_dict 的异常信息为==》%s", str(e))

    def fill_none(self, begin, end, dict):
        try:
            if end > begin:
                new_dict = dict.copy()
                for i in range(begin, end):
                    new_dict[i] = ''
                return new_dict
            else:
                return dict
        except Exception as e:
            traceback.print_exc()
            logging.error("fill_none 的异常信息为==》%s", str(e))

    def fill_dict(self, dict):
        try:
            if len(dict) == 0:
                return dict
            sorted_list = sorted(dict.keys())
            new_dict = {}
            for i, item in enumerate(sorted_list):
                if i <= len(sorted_list) - 2:
                    count = sorted_list[i + 1] - item
                    if count > 1 and count <= config["rdstep"] + 3:
                        new_dict[item] = dict[sorted_list[i]]
                        value = dict[sorted_list[i + 1]]
                        for r in range(1, count):
                            new_dict[(item + r)] = value
                    elif count > config["rdstep"] + 3:
                        for r in range(count):
                            new_dict[(item + r)] = ""
                    elif count == 1:
                        new_dict[item] = dict[item]
            if sorted_list:
                new_dict[max(sorted_list)] = dict[max(sorted_list)]
            return new_dict
        except Exception as e:
            traceback.print_exc()
            logging.error("fill_dict 的异常信息为==》%s", str(e))

    def fill_pre_dict(self, dict):
        try:
            if len(dict) == 0:
                return dict
            sorted_list = sorted(dict.keys())
            min_sort = min(sorted_list)
            new_dict = {}
            for i, item in enumerate(sorted_list):
                if i == 0:
                    for r in range(1, config["ptstep"]):
                        new_dict[min_sort - r] = dict[min_sort]
                if i <= len(sorted_list) - 2:
                    count = sorted_list[i + 1] - item
                    if count > 1 and count <= config["ptstep"] + 3:
                        value = dict[sorted_list[i + 1]]
                        for r in range(count):
                            new_dict[(item + r)] = value
                    elif count > config["ptstep"] + 3:
                        for r in range(count):
                            new_dict[(item + r)] = ""
                        for j in range(1, config["ptstep"]):
                            new_dict[sorted_list[i + 1] - j] = dict[sorted_list[i + 1]]
                    elif count == 1:
                        new_dict[item] = dict[item]
            if sorted_list:
                new_dict[max(sorted_list)] = dict[max(sorted_list)]
            return new_dict
        except Exception as e:
            traceback.print_exc()
            logging.error("fill_pre_dict 的异常信息为==》%s", str(e))

    def miniotor(self):
        while True:

            # 以前没有 sleep，是因为 udp recv 会阻塞
            time.sleep(1)

            if self.GLOBAL_DATA.get("_data") is None:
                continue

            msg = json.dumps(self.GLOBAL_DATA["_data"])
            if msg:
                pre_data = msg.replace("NaN", "0")
                pre_data = pre_data.replace("None", "0")
                obj = json.loads(pre_data)
                self.lock.acquire()

                try:
                    if obj.get("pre_so2"):

                        # 预测so2
                        self.pre_jyq_so2_map[next_time] = obj["pre_so2"]
                        self.pre_jyq_so2_map = self.fill_pre_data(self.tj_pre_time, self.pre_jyq_so2_map, next_time)
                        logging.info("预测so2: %s", obj["pre_so2"])

                        # 预测供浆流量
                        self.pre_gjll_map[next_time] = obj["pre_gjll"]
                        self.pre_gjll_map = self.fill_pre_data(self.tj_pre_time, self.pre_gjll_map, next_time)
                        # 预测ph
                        self.pre_ph_map[next_time] = obj["pre_ph"]
                        self.pre_ph_map = self.fill_pre_data(self.tj_pre_time, self.pre_ph_map, next_time)

                        self.send_obj["data"]["pre_so2"] = obj["pre_so2"]
                        self.send_obj["data"]["pre_ph"] = obj["pre_ph"]
                        self.send_obj["data"]["pre_gjll"] = obj["pre_gjll"]

                    elif obj.get("advice1"):
                        timestamp = int(datetime.datetime.strptime(obj["date"], "%Y-%m-%d %H:%M:%S").timestamp())
                        next_time = timestamp + config["mod_pre_step"]

                        self.send_obj["data"]["advice1"] = obj["advice1"]
                        self.send_obj["data"]["advice2"] = obj["advice2"]
                        self.send_obj["data"]["advice3"] = obj["advice3"]
                        self.send_obj["data"]["advice4"] = obj["advice4"]

                        self.tj_pre_time = next_time
                    elif obj.get("start_time"):
                        logging.info("改变时间的信号： %s", str(msg))
                        self.change_befor_time[int(datetime.datetime.now().timestamp())] = self.mark["start_time"]

                        self.mark["start_time"] = obj["start_time"]
                        self.mark["end_time"] = obj["end_time"]
                        self.mark["update_end_time"] = 0
                    elif obj.get("update_end_time"):
                        if len(self.change_befor_time) > 0:
                            self.mark["start_time"] = self.change_befor_time[min(self.change_befor_time.keys())]
                            self.change_befor_time.clear()
                            self.mark["update_end_time"] = 1
                    elif obj.get("args"):
                        logging.info("页面改变args： %s", str(msg))
                        self.mark["args"] = obj["args"]
                    elif obj.get("zml"):
                        # logging.info("实时数据： %s", str(msg))
                        timestamp = int(datetime.datetime.strptime(obj["date"], "%Y-%m-%d %H:%M:%S").timestamp())
                        next_time = timestamp
                        r = obj
                        self.zml_map[next_time] = r["zml"]
                        self.zml_map = self.fill_data(self.real_time, self.zml_map, next_time)

                        self.yyq_so2_map[next_time] = r["yyq_so2"]
                        self.yyq_so2_map = self.fill_data(self.real_time, self.yyq_so2_map, next_time)

                        self.yyq_o2_map[next_time] = r["yyq_o2"]
                        self.yyq_o2_map = self.fill_data(self.real_time, self.yyq_o2_map, next_time)

                        self.jzfh_map[next_time] = r["jzfh"]
                        self.jzfh_map = self.fill_data(self.real_time, self.jzfh_map, next_time)

                        self.yyq_wd_map[next_time] = r["yyq_wd"]
                        self.yyq_wd_map = self.fill_data(self.real_time, self.yyq_wd_map, next_time)

                        self.yyq_ll_map[next_time] = r["yyq_ll"]
                        self.yyq_ll_map = self.fill_data(self.real_time, self.yyq_ll_map, next_time)

                        self.jyq_so2_map[next_time] = r["jyq_so2"]
                        self.jyq_so2_map = self.fill_data(self.real_time, self.jyq_so2_map, next_time)
                        # logging.info("实时jyq_so2： %s", r["jyq_so2"])

                        self.jyq_ll_map[next_time] = r["jyq_ll"]
                        self.jyq_ll_map = self.fill_data(self.real_time, self.jyq_ll_map, next_time)

                        self.sgjy_md_map[next_time] = r["sgjy_md"]
                        self.sgjy_md_map = self.fill_data(self.real_time, self.sgjy_md_map, next_time)

                        self.sgjy_ph1_map[next_time] = r["sgjy_ph1"]
                        self.sgjy_ph1_map = self.fill_data(self.real_time, self.sgjy_ph1_map, next_time)

                        self.sgjy_ph2_map[next_time] = r["sgjy_ph2"]
                        self.sgjy_ph2_map = self.fill_data(self.real_time, self.sgjy_ph2_map, next_time)

                        self.xst_yw_map[next_time] = r["xst_yw"]
                        self.xst_yw_map = self.fill_data(self.real_time, self.xst_yw_map, next_time)

                        self.sgjy_ll_map[next_time] = r["sgjy_ll"]
                        self.sgjy_ll_map = self.fill_data(self.real_time, self.sgjy_ll_map, next_time)

                        self.jytjf_wzfk_map[next_time] = r["jytjf_wzfk"]
                        self.jytjf_wzfk_map = self.fill_data(self.real_time, self.jytjf_wzfk_map, next_time)

                        self.jyxhb_dl1_map[next_time] = r["jyxhb_dl1"]
                        self.jyxhb_dl1_map = self.fill_data(self.real_time, self.jyxhb_dl1_map, next_time)

                        self.jyxhb_dl2_map[next_time] = r["jyxhb_dl2"]
                        self.jyxhb_dl2_map = self.fill_data(self.real_time, self.jyxhb_dl2_map, next_time)

                        self.jyxhb_dl3_map[next_time] = r["jyxhb_dl3"]
                        self.jyxhb_dl3_map = self.fill_data(self.real_time, self.jyxhb_dl3_map, next_time)

                        self.jyxhb_dl4_map[next_time] = r["jyxhb_dl4"]
                        self.jyxhb_dl4_map = self.fill_data(self.real_time, self.jyxhb_dl4_map, next_time)

                        self.send_obj["data"]["zml"] = r["zml"]
                        self.send_obj["data"]["yyq_so2"] = r["yyq_so2"]
                        self.send_obj["data"]["yyq_o2"] = r["yyq_o2"]

                        self.send_obj["data"]["jzfh"] = r["jzfh"]
                        self.send_obj["data"]["yyq_wd"] = r["yyq_wd"]
                        self.send_obj["data"]["yyq_ll"] = r["yyq_ll"]
                        self.send_obj["data"]["jyq_so2"] = r["jyq_so2"]
                        self.send_obj["data"]["jyq_ll"] = r["jyq_ll"]

                        self.send_obj["data"]["sgjy_md"] = r["sgjy_md"]
                        self.send_obj["data"]["sgjy_ph1"] = r["sgjy_ph1"]
                        self.send_obj["data"]["sgjy_ph2"] = r["sgjy_ph2"]
                        self.send_obj["data"]["xst_yw"] = r["xst_yw"]
                        self.send_obj["data"]["sgjy_ll"] = r["sgjy_ll"]

                        self.send_obj["data"]["jytjf_wzfk"] = r["jytjf_wzfk"]

                        self.send_obj["data"]["jyxhb_dl1"] = r["jyxhb_dl1"]
                        self.send_obj["data"]["jyxhb_dl2"] = r["jyxhb_dl2"]
                        self.send_obj["data"]["jyxhb_dl3"] = r["jyxhb_dl3"]
                        self.send_obj["data"]["jyxhb_dl4"] = r["jyxhb_dl4"]

                        self.send_obj["data"]["target_so2"] = r["target_so2"]

                        self.mark["is_send"] = True
                        if self.mark["update_end_time"]:
                            if self.rt_diff_time is None:
                                self.rt_diff_time = obj["date"]
                            else:
                                self.diff_time = int(datetime.datetime.strptime(obj["date"],
                                                                                "%Y-%m-%d %H:%M:%S").timestamp() - datetime.datetime.strptime(
                                    self.rt_diff_time,
                                    "%Y-%m-%d %H:%M:%S").timestamp())
                                self.rt_diff_time = obj["date"]
                            self.mark["end_time"] = obj["date"]
                        self.real_time = next_time
                except Exception as e:
                    traceback.print_exc()
                    logging.error("miniotor 中的异常为 %s", str(e))
                finally:
                    self.lock.release()

    def get_send_data(self):

        self.table_update()  # 数据表更新
        self.lock.acquire()
        str_start_time = self.mark["start_time"]
        str_end_time = self.mark["end_time"]
        start_time = int(datetime.datetime.strptime(str_start_time, "%Y-%m-%d %H:%M:%S").timestamp())
        end_time = int(datetime.datetime.strptime(str_end_time, "%Y-%m-%d %H:%M:%S").timestamp())
        send_data = []

        try:
            # self.GLOBAL_DATA["so2_chart_lines"]
            for r in self.GLOBAL_DATA["so2_chart_lines"]:
                # 实时值
                if r == "zml":
                    obj = {}
                    obj["name"] = "zml"
                    obj["title"] = "总煤量"
                    obj["step"] = config["rtstep"]
                    data = []
                    sort_keys = sorted(self.zml_map.keys())
                    little = min(sort_keys)
                    big = max(sort_keys)
                    end_time1 = end_time
                    if end_time1 >= big:
                        end_time1 = big
                    if start_time >= little and end_time1 <= big:
                        for i in sort_keys:
                            if i >= start_time and i <= end_time1:
                                data.append(self.zml_map[i])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    elif (start_time < little and end_time1 <= big and end_time1 >= little) or end_time1 < little:
                        # 查找数据库
                        str_end_time1 = datetime.datetime.fromtimestamp(little).strftime("%Y-%m-%d %H:%M:%S")
                        result = self.engine.execute(
                            "select timestamp,zml from " + self.rt_table_name + " where timestamp between %s and %s order by  timestamp ",
                            (str_start_time, str_end_time1)).fetchall()
                        result = [dict(i) for i in result]
                        for i in result:
                            timestamp = int(i["timestamp"].timestamp())
                            self.zml_map[timestamp] = i["zml"]
                        self.zml_map = self.fill_dict(self.zml_map)
                        self.zml_map = self.fill_none(start_time, min(self.zml_map.keys()),
                                                      self.zml_map)
                        sort_keys = sorted(self.zml_map.keys())
                        for m in sort_keys:
                            if m >= start_time and m <= end_time1:
                                data.append(self.zml_map[m])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    send_data.append(obj)
                if r == "jyq_so2":
                    obj = {}
                    obj["name"] = "jyq_so2"
                    obj["title"] = "净烟气SO2含量"
                    obj["step"] = config["rtstep"]
                    data = []
                    sort_keys = sorted(self.jyq_so2_map.keys())
                    little = min(sort_keys)
                    big = max(sort_keys)
                    end_time1 = end_time
                    if end_time1 >= big:
                        end_time1 = big
                    if start_time >= little and end_time1 <= big:
                        for i in sort_keys:
                            if i >= start_time and i <= end_time1:
                                data.append(self.jyq_so2_map[i])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    elif (start_time < little and end_time1 <= big and end_time1 >= little) or end_time1 < little:
                        # 查找数据库
                        str_end_time1 = datetime.datetime.fromtimestamp(little).strftime("%Y-%m-%d %H:%M:%S")
                        result = self.engine.execute(
                            "select timestamp,jyq_so2 from " + self.rt_table_name + " where timestamp between %s and %s order by  timestamp ",
                            (str_start_time, str_end_time1)).fetchall()
                        result = [dict(i) for i in result]
                        for i in result:
                            timestamp = int(i["timestamp"].timestamp())
                            self.jyq_so2_map[timestamp] = i["jyq_so2"]
                        self.jyq_so2_map = self.fill_dict(self.jyq_so2_map)
                        self.jyq_so2_map = self.fill_none(start_time, min(self.jyq_so2_map.keys()), self.jyq_so2_map)
                        sort_keys = sorted(self.jyq_so2_map.keys())
                        for m in sort_keys:
                            if m >= start_time and m <= end_time1:
                                data.append(self.jyq_so2_map[m])
                        obj["data"] = data
                        # for r in sort_keys:
                        #     if r >= start_time and r <= end_time1:
                        #         obj["start_time"] = r
                        #         break

                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1

                    send_data.append(obj)
                if r == "yyq_so2":
                    obj = {}
                    obj["name"] = "yyq_so2"
                    obj["title"] = "原烟气so2含量"
                    obj["step"] = config["rtstep"]
                    data = []
                    sort_keys = sorted(self.yyq_so2_map.keys())
                    little = min(sort_keys)
                    big = max(sort_keys)
                    end_time1 = end_time
                    if end_time1 >= big:
                        end_time1 = big
                    if start_time >= little and end_time1 <= big:
                        for i in sort_keys:
                            if i >= start_time and i <= end_time1:
                                data.append(self.yyq_so2_map[i])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    elif (start_time < little and end_time1 <= big and end_time1 >= little) or end_time1 < little:
                        # 查找数据库
                        str_end_time1 = datetime.datetime.fromtimestamp(little).strftime("%Y-%m-%d %H:%M:%S")

                        result = self.engine.execute(
                            "select timestamp,yyq_so2 from " + self.rt_table_name + " where timestamp between %s and %s order by  timestamp ",
                            (str_start_time, str_end_time1)).fetchall()
                        result = [dict(i) for i in result]
                        for i in result:
                            timestamp = int(i["timestamp"].timestamp())
                            self.yyq_so2_map[timestamp] = i["yyq_so2"]
                        self.yyq_so2_map = self.fill_dict(self.yyq_so2_map)
                        self.yyq_so2_map = self.fill_none(start_time, min(self.yyq_so2_map.keys()),
                                                          self.yyq_so2_map)
                        sort_keys = sorted(self.yyq_so2_map.keys())
                        for m in sort_keys:
                            if m >= start_time and m <= end_time1:
                                data.append(self.yyq_so2_map[m])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    send_data.append(obj)
                if r == "yyq_o2":
                    obj = {}
                    obj["name"] = "yyq_o2"
                    obj["title"] = "原烟气o2含量"
                    obj["step"] = config["rtstep"]
                    data = []
                    sort_keys = sorted(self.yyq_o2_map.keys())
                    little = min(sort_keys)
                    big = max(sort_keys)
                    end_time1 = end_time
                    if end_time1 >= big:
                        end_time1 = big
                    if start_time >= little and end_time1 <= big:
                        for i in sort_keys:
                            if i >= start_time and i <= end_time1:
                                data.append(self.yyq_o2_map[i])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    elif (start_time < little and end_time1 <= big and end_time1 >= little) or end_time1 < little:
                        # 查找数据库
                        str_end_time1 = datetime.datetime.fromtimestamp(little).strftime("%Y-%m-%d %H:%M:%S")

                        result = self.engine.execute(
                            "select timestamp,yyq_o2 from " + self.rt_table_name + " where timestamp between %s and %s order by  timestamp ",
                            (str_start_time, str_end_time1)).fetchall()
                        result = [dict(i) for i in result]
                        for i in result:
                            timestamp = int(i["timestamp"].timestamp())
                            self.yyq_o2_map[timestamp] = i["yyq_o2"]
                        self.yyq_o2_map = self.fill_dict(self.yyq_o2_map)
                        self.yyq_o2_map = self.fill_none(start_time, min(self.yyq_o2_map.keys()),
                                                         self.yyq_o2_map)
                        sort_keys = sorted(self.yyq_o2_map.keys())
                        for m in sort_keys:
                            if m >= start_time and m <= end_time1:
                                data.append(self.yyq_o2_map[m])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    send_data.append(obj)
                if r == "jzfh":
                    obj = {}
                    obj["name"] = "jzfh"
                    obj["title"] = "机组负荷"
                    obj["step"] = config["rtstep"]
                    data = []
                    sort_keys = sorted(self.jzfh_map.keys())
                    little = min(sort_keys)
                    big = max(sort_keys)
                    end_time1 = end_time
                    if end_time1 >= big:
                        end_time1 = big
                    if start_time >= little and end_time1 <= big:
                        for i in sort_keys:
                            if i >= start_time and i <= end_time1:
                                data.append(self.jzfh_map[i])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    elif (start_time < little and end_time1 <= big and end_time1 >= little) or end_time1 < little:
                        # 查找数据库
                        str_end_time1 = datetime.datetime.fromtimestamp(little).strftime("%Y-%m-%d %H:%M:%S")

                        result = self.engine.execute(
                            "select timestamp,jzfh from " + self.rt_table_name + " where timestamp between %s and %s order by  timestamp ",
                            (str_start_time, str_end_time1)).fetchall()
                        result = [dict(i) for i in result]
                        for i in result:
                            timestamp = int(i["timestamp"].timestamp())
                            self.jzfh_map[timestamp] = i["jzfh"]
                        self.jzfh_map = self.fill_dict(self.jzfh_map)
                        self.jzfh_map = self.fill_none(start_time, min(self.jzfh_map.keys()),
                                                       self.jzfh_map)
                        sort_keys = sorted(self.jzfh_map.keys())
                        for m in sort_keys:
                            if m >= start_time and m <= end_time1:
                                data.append(self.jzfh_map[m])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    send_data.append(obj)
                if r == "yyq_wd":
                    obj = {}
                    obj["name"] = "yyq_wd"
                    obj["title"] = "原烟气烟气温度"
                    obj["step"] = config["rtstep"]
                    data = []
                    sort_keys = sorted(self.yyq_wd_map.keys())
                    little = min(sort_keys)
                    big = max(sort_keys)
                    end_time1 = end_time
                    if end_time1 >= big:
                        end_time1 = big
                    if start_time >= little and end_time1 <= big:
                        for i in sort_keys:
                            if i >= start_time and i <= end_time1:
                                data.append(self.yyq_wd_map[i])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    elif (start_time < little and end_time1 <= big and end_time1 >= little) or end_time1 < little:
                        # 查找数据库
                        str_end_time1 = datetime.datetime.fromtimestamp(little).strftime("%Y-%m-%d %H:%M:%S")

                        result = self.engine.execute(
                            "select timestamp,yyq_wd from " + self.rt_table_name + " where timestamp between %s and %s order by  timestamp ",
                            (str_start_time, str_end_time1)).fetchall()
                        result = [dict(i) for i in result]
                        for i in result:
                            timestamp = int(i["timestamp"].timestamp())
                            self.yyq_wd_map[timestamp] = i["yyq_wd"]
                            data.append(i["yyq_wd"])
                        self.yyq_wd_map = self.fill_dict(self.yyq_wd_map)
                        self.yyq_wd_map = self.fill_none(start_time, min(self.yyq_wd_map.keys()),
                                                         self.yyq_wd_map)
                        sort_keys = sorted(self.yyq_wd_map.keys())
                        for m in sort_keys:
                            if m >= start_time and m <= end_time1:
                                data.append(self.yyq_wd_map[m])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    send_data.append(obj)
                if r == "yyq_ll":
                    obj = {}
                    obj["name"] = "yyq_ll"
                    obj["title"] = "原烟气烟气流量"
                    obj["step"] = config["rtstep"]
                    data = []
                    sort_keys = sorted(self.yyq_ll_map.keys())
                    little = min(sort_keys)
                    big = max(sort_keys)
                    end_time1 = end_time
                    if end_time1 >= big:
                        end_time1 = big
                    if start_time >= little and end_time1 <= big:
                        for i in sort_keys:
                            if i >= start_time and i <= end_time1:
                                data.append(self.yyq_ll_map[i])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    elif (start_time < little and end_time1 <= big and end_time1 >= little) or end_time1 < little:
                        # 查找数据库
                        str_end_time1 = datetime.datetime.fromtimestamp(little).strftime("%Y-%m-%d %H:%M:%S")

                        result = self.engine.execute(
                            "select timestamp,yyq_ll from " + self.rt_table_name + " where timestamp between %s and %s order by  timestamp ",
                            (str_start_time, str_end_time1)).fetchall()
                        result = [dict(i) for i in result]
                        for i in result:
                            timestamp = int(i["timestamp"].timestamp())
                            self.yyq_ll_map[timestamp] = i["yyq_ll"]
                        self.yyq_ll_map = self.fill_dict(self.yyq_ll_map)
                        self.yyq_ll_map = self.fill_none(start_time, min(self.yyq_ll_map.keys()),
                                                         self.yyq_ll_map)
                        sort_keys = sorted(self.yyq_ll_map.keys())
                        for m in sort_keys:
                            if m >= start_time and m <= end_time1:
                                data.append(self.yyq_ll_map[m])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    send_data.append(obj)
                if r == "jyq_ll":
                    obj = {}
                    obj["name"] = "jyq_ll"
                    obj["title"] = "净烟气烟气流量"
                    obj["step"] = config["rtstep"]
                    data = []
                    sort_keys = sorted(self.jyq_ll_map.keys())
                    little = min(sort_keys)
                    big = max(sort_keys)
                    end_time1 = end_time
                    if end_time1 >= big:
                        end_time1 = big
                    if start_time >= little and end_time1 <= big:
                        for i in sort_keys:
                            if i >= start_time and i <= end_time1:
                                data.append(self.jyq_ll_map[i])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    elif (start_time < little and end_time1 <= big and end_time1 >= little) or end_time1 < little:
                        # 查找数据库
                        str_end_time1 = datetime.datetime.fromtimestamp(little).strftime("%Y-%m-%d %H:%M:%S")

                        result = self.engine.execute(
                            "select timestamp,jyq_ll from " + self.rt_table_name + " where timestamp between %s and %s order by  timestamp ",
                            (str_start_time, str_end_time1)).fetchall()
                        result = [dict(i) for i in result]
                        for i in result:
                            timestamp = int(i["timestamp"].timestamp())
                            self.jyq_ll_map[timestamp] = i["jyq_ll"]
                        self.jyq_ll_map = self.fill_dict(self.jyq_ll_map)
                        self.jyq_ll_map = self.fill_none(start_time, min(self.jyq_ll_map.keys()),
                                                         self.jyq_ll_map)
                        sort_keys = sorted(self.jyq_ll_map.keys())
                        for m in sort_keys:
                            if m >= start_time and m <= end_time1:
                                data.append(self.jyq_ll_map[m])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    send_data.append(obj)
                if r == "sgjy_md":
                    obj = {}
                    obj["name"] = "sgjy_md"
                    obj["title"] = "石灰石浆液密度"
                    obj["step"] = config["rtstep"]
                    data = []
                    sort_keys = sorted(self.sgjy_md_map.keys())
                    little = min(sort_keys)
                    big = max(sort_keys)
                    end_time1 = end_time
                    if end_time1 >= big:
                        end_time1 = big
                    if start_time >= little and end_time1 <= big:
                        for i in sort_keys:
                            if i >= start_time and i <= end_time1:
                                data.append(self.sgjy_md_map[i])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    elif (start_time < little and end_time1 <= big and end_time1 >= little) or end_time1 < little:
                        # 查找数据库
                        str_end_time1 = datetime.datetime.fromtimestamp(little).strftime("%Y-%m-%d %H:%M:%S")

                        result = self.engine.execute(
                            "select timestamp,sgjy_md from " + self.rt_table_name + " where timestamp between %s and %s order by  timestamp ",
                            (str_start_time, str_end_time1)).fetchall()
                        result = [dict(i) for i in result]
                        for i in result:
                            timestamp = int(i["timestamp"].timestamp())
                            self.sgjy_md_map[timestamp] = i["sgjy_md"]
                        self.sgjy_md_map = self.fill_dict(self.sgjy_md_map)
                        self.sgjy_md_map = self.fill_none(start_time, min(self.sgjy_md_map.keys()),
                                                          self.sgjy_md_map)
                        sort_keys = sorted(self.sgjy_md_map.keys())
                        for m in sort_keys:
                            if m >= start_time and m <= end_time1:
                                data.append(self.sgjy_md_map[m])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    send_data.append(obj)
                if r == "sgjy_ph1":
                    obj = {}
                    obj["name"] = "sgjy_ph1"
                    obj["title"] = "石膏浆液PH值1"
                    obj["step"] = config["rtstep"]
                    data = []
                    sort_keys = sorted(self.sgjy_ph1_map.keys())
                    little = min(sort_keys)
                    big = max(sort_keys)
                    end_time1 = end_time
                    if end_time1 >= big:
                        end_time1 = big
                    if start_time >= little and end_time1 <= big:
                        for i in sort_keys:
                            if i >= start_time and i <= end_time1:
                                data.append(self.sgjy_ph1_map[i])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    elif (start_time < little and end_time1 <= big and end_time1 >= little) or end_time1 < little:
                        # 查找数据库
                        str_end_time1 = datetime.datetime.fromtimestamp(little).strftime("%Y-%m-%d %H:%M:%S")

                        result = self.engine.execute(
                            "select timestamp,sgjy_ph1 from " + self.rt_table_name + " where timestamp between %s and %s order by  timestamp ",
                            (str_start_time, str_end_time1)).fetchall()
                        result = [dict(i) for i in result]
                        for i in result:
                            timestamp = int(i["timestamp"].timestamp())
                            self.sgjy_ph1_map[timestamp] = i["sgjy_ph1"]
                        self.sgjy_ph1_map = self.fill_dict(self.sgjy_ph1_map)
                        self.sgjy_ph1_map = self.fill_none(start_time, min(self.sgjy_ph1_map.keys()),
                                                           self.sgjy_ph1_map)
                        sort_keys = sorted(self.sgjy_ph1_map.keys())
                        for m in sort_keys:
                            if m >= start_time and m <= end_time1:
                                data.append(self.sgjy_ph1_map[m])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    send_data.append(obj)
                if r == "sgjy_ph2":
                    obj = {}
                    obj["name"] = "sgjy_ph2"
                    obj["title"] = "石膏浆液PH值2"
                    obj["step"] = config["rtstep"]
                    data = []
                    sort_keys = sorted(self.sgjy_ph2_map.keys())
                    little = min(sort_keys)
                    big = max(sort_keys)
                    end_time1 = end_time
                    if end_time1 >= big:
                        end_time1 = big
                    if start_time >= little and end_time1 <= big:
                        for i in sort_keys:
                            if i >= start_time and i <= end_time1:
                                data.append(self.sgjy_ph2_map[i])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    elif (start_time < little and end_time1 <= big and end_time1 >= little) or end_time1 < little:
                        # 查找数据库
                        str_end_time1 = datetime.datetime.fromtimestamp(little).strftime("%Y-%m-%d %H:%M:%S")

                        result = self.engine.execute(
                            "select timestamp,sgjy_ph2 from " + self.rt_table_name + " where timestamp between %s and %s order by  timestamp ",
                            (str_start_time, str_end_time1)).fetchall()
                        result = [dict(i) for i in result]
                        for i in result:
                            timestamp = int(i["timestamp"].timestamp())
                            self.sgjy_ph2_map[timestamp] = i["sgjy_ph2"]
                        self.sgjy_ph2_map = self.fill_dict(self.sgjy_ph2_map)
                        self.sgjy_ph2_map = self.fill_none(start_time, min(self.sgjy_ph2_map.keys()),
                                                           self.sgjy_ph2_map)
                        sort_keys = sorted(self.sgjy_ph2_map.keys())
                        for m in sort_keys:
                            if m >= start_time and m <= end_time1:
                                data.append(self.sgjy_ph2_map[m])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    send_data.append(obj)
                if r == "jytjf_wzfk":
                    obj = {}
                    obj["name"] = "jytjf_wzfk"
                    obj["title"] = "浆液调节阀位置反馈"
                    obj["step"] = config["rtstep"]
                    data = []
                    sort_keys = sorted(self.jytjf_wzfk_map.keys())
                    little = min(sort_keys)
                    big = max(sort_keys)
                    end_time1 = end_time
                    if end_time1 >= big:
                        end_time1 = big
                    if start_time >= little and end_time1 <= big:
                        for i in sort_keys:
                            if i >= start_time and i <= end_time1:
                                data.append(self.jytjf_wzfk_map[i])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    elif (start_time < little and end_time1 <= big and end_time1 >= little) or end_time1 < little:
                        # 查找数据库
                        str_end_time1 = datetime.datetime.fromtimestamp(little).strftime("%Y-%m-%d %H:%M:%S")

                        result = self.engine.execute(
                            "select timestamp,jytjf_wzfk from " + self.rt_table_name + " where timestamp between %s and %s order by  timestamp ",
                            (str_start_time, str_end_time1)).fetchall()
                        result = [dict(i) for i in result]
                        for i in result:
                            timestamp = int(i["timestamp"].timestamp())
                            self.jytjf_wzfk_map[timestamp] = i["jytjf_wzfk"]
                        self.jytjf_wzfk_map = self.fill_dict(self.jytjf_wzfk_map)
                        self.jytjf_wzfk_map = self.fill_none(start_time, min(self.jytjf_wzfk_map.keys()),
                                                             self.jytjf_wzfk_map)
                        sort_keys = sorted(self.jytjf_wzfk_map.keys())
                        for m in sort_keys:
                            if m >= start_time and m <= end_time1:
                                data.append(self.jytjf_wzfk_map[m])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    send_data.append(obj)
                if r == "jyxhb_dl1":
                    obj = {}
                    obj["name"] = "jyxhb_dl1"
                    obj["title"] = "浆液循环泵电流1"
                    obj["step"] = config["rtstep"]
                    data = []
                    sort_keys = sorted(self.jyxhb_dl1_map.keys())
                    little = min(sort_keys)
                    big = max(sort_keys)
                    end_time1 = end_time
                    if end_time1 >= big:
                        end_time1 = big
                    if start_time >= little and end_time1 <= big:
                        for i in sort_keys:
                            if i >= start_time and i <= end_time1:
                                data.append(self.jyxhb_dl1_map[i])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    elif (start_time < little and end_time1 <= big and end_time1 >= little) or end_time1 < little:
                        # 查找数据库
                        str_end_time1 = datetime.datetime.fromtimestamp(little).strftime("%Y-%m-%d %H:%M:%S")

                        result = self.engine.execute(
                            "select timestamp,jyxhb_dl1 from " + self.rt_table_name + " where timestamp between %s and %s order by  timestamp ",
                            (str_start_time, str_end_time1)).fetchall()
                        result = [dict(i) for i in result]
                        for i in result:
                            timestamp = int(i["timestamp"].timestamp())
                            self.jyxhb_dl1_map[timestamp] = i["jyxhb_dl1"]
                        self.jyxhb_dl1_map = self.fill_dict(self.jyxhb_dl1_map)
                        self.jyxhb_dl1_map = self.fill_none(start_time, min(self.jyxhb_dl1_map.keys()),
                                                            self.jyxhb_dl1_map)
                        sort_keys = sorted(self.jyxhb_dl1_map.keys())
                        for m in sort_keys:
                            if m >= start_time and m <= end_time1:
                                data.append(self.jyxhb_dl1_map[m])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    send_data.append(obj)
                if r == "xst_yw":
                    obj = {}
                    obj["name"] = "xst_yw"
                    obj["title"] = "吸收塔液位"
                    obj["step"] = config["rtstep"]
                    data = []
                    sort_keys = sorted(self.xst_yw_map.keys())
                    little = min(sort_keys)
                    big = max(sort_keys)
                    end_time1 = end_time
                    if end_time1 >= big:
                        end_time1 = big
                    if start_time >= little and end_time1 <= big:
                        for i in sort_keys:
                            if i >= start_time and i <= end_time1:
                                data.append(self.xst_yw_map[i])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    elif (start_time < little and end_time1 <= big and end_time1 >= little) or end_time1 < little:
                        # 查找数据库
                        str_end_time1 = datetime.datetime.fromtimestamp(little).strftime("%Y-%m-%d %H:%M:%S")

                        result = self.engine.execute(
                            "select timestamp,xst_yw from " + self.rt_table_name + " where timestamp between %s and %s order by  timestamp ",
                            (str_start_time, str_end_time1)).fetchall()
                        result = [dict(i) for i in result]
                        for i in result:
                            timestamp = int(i["timestamp"].timestamp())
                            self.xst_yw_map[timestamp] = i["xst_yw"]
                        self.xst_yw_map = self.fill_dict(self.xst_yw_map)
                        self.xst_yw_map = self.fill_none(start_time, min(self.xst_yw_map.keys()),
                                                         self.xst_yw_map)
                        sort_keys = sorted(self.xst_yw_map.keys())
                        for m in sort_keys:
                            if m >= start_time and m <= end_time1:
                                data.append(self.xst_yw_map[m])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    send_data.append(obj)
                if r == "sgjy_LL":
                    obj = {}
                    obj["name"] = "sgjy_LL"
                    obj["title"] = "石灰石浆液流量"
                    obj["step"] = config["rtstep"]
                    data = []
                    sort_keys = sorted(self.sgjy_ll_map.keys())
                    little = min(sort_keys)
                    big = max(sort_keys)
                    end_time1 = end_time
                    if end_time1 >= big:
                        end_time1 = big
                    if start_time >= little and end_time1 <= big:
                        for i in sort_keys:
                            if i >= start_time and i <= end_time1:
                                data.append(self.sgjy_ll_map[i])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    elif (start_time < little and end_time1 <= big and end_time1 >= little) or end_time1 < little:
                        # 查找数据库
                        str_end_time1 = datetime.datetime.fromtimestamp(little).strftime("%Y-%m-%d %H:%M:%S")

                        result = self.engine.execute(
                            "select timestamp,sgjy_ll from " + self.rt_table_name + " where timestamp between %s and %s order by  timestamp ",
                            (str_start_time, str_end_time1)).fetchall()
                        result = [dict(i) for i in result]
                        for i in result:
                            timestamp = int(i["timestamp"].timestamp())
                            self.sgjy_ll_map[timestamp] = i["sgjy_ll"]
                        self.sgjy_ll_map = self.fill_dict(self.sgjy_ll_map)
                        self.sgjy_ll_map = self.fill_none(start_time, min(self.sgjy_ll_map.keys()),
                                                          self.sgjy_ll_map)
                        sort_keys = sorted(self.sgjy_ll_map.keys())
                        for m in sort_keys:
                            if m >= start_time and m <= end_time1:
                                data.append(self.sgjy_ll_map[m])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    send_data.append(obj)
                if r == "jyxhb_dl2":
                    obj = {}
                    obj["name"] = "jyxhb_dl2"
                    obj["title"] = "浆液循环泵电流2"
                    obj["step"] = config["rtstep"]
                    data = []
                    sort_keys = sorted(self.jyxhb_dl2_map.keys())
                    little = min(sort_keys)
                    big = max(sort_keys)
                    end_time1 = end_time + config["rdstep"]
                    if end_time1 >= big:
                        end_time1 = big
                    if start_time >= little and end_time1 <= big:
                        for i in sort_keys:
                            if i >= start_time and i <= end_time1:
                                data.append(self.jyxhb_dl2_map[i])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    elif (start_time < little and end_time1 <= big and end_time1 >= little) or end_time1 < little:
                        # 查找数据库
                        str_end_time1 = datetime.datetime.fromtimestamp(little).strftime("%Y-%m-%d %H:%M:%S")

                        result = self.engine.execute(
                            "select timestamp,jyxhb_dl2 from " + self.rt_table_name + " where timestamp between %s and %s order by  timestamp ",
                            (str_start_time, str_end_time1)).fetchall()
                        result = [dict(i) for i in result]
                        for i in result:
                            timestamp = int(i["timestamp"].timestamp())
                            self.jyxhb_dl2_map[timestamp] = i["jyxhb_dl2"]
                            data.append(i["jyxhb_dl2"])
                        self.jyxhb_dl2_map = self.fill_dict(self.jyxhb_dl2_map)
                        self.jyxhb_dl2_map = self.fill_none(start_time, min(self.jyxhb_dl2_map.keys()),
                                                            self.jyxhb_dl2_map)
                        sort_keys = sorted(self.jyxhb_dl2_map.keys())
                        for m in sort_keys:
                            if m >= start_time and m <= end_time1:
                                data.append(self.jyxhb_dl2_map[m])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    send_data.append(obj)
                if r == "jyxhb_dl3":
                    obj = {}
                    obj["name"] = "jyxhb_dl3"
                    obj["title"] = "浆液循环泵电流3"
                    obj["step"] = config["rtstep"]
                    data = []
                    sort_keys = sorted(self.jyxhb_dl3_map.keys())
                    little = min(sort_keys)
                    big = max(sort_keys)
                    end_time1 = end_time
                    if end_time1 >= big:
                        end_time1 = big
                    if start_time >= little and end_time1 <= big:
                        for i in sort_keys:
                            if i >= start_time and i <= end_time1:
                                data.append(self.jyxhb_dl3_map[i])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    elif (start_time < little and end_time1 <= big and end_time1 >= little) or end_time1 < little:
                        # 查找数据库
                        str_end_time1 = datetime.datetime.fromtimestamp(little).strftime("%Y-%m-%d %H:%M:%S")
                        result = self.engine.execute(
                            "select timestamp,jyxhb_dl3 from " + self.rt_table_name + " where timestamp between %s and %s order by  timestamp ",
                            (str_start_time, str_end_time1)).fetchall()
                        result = [dict(i) for i in result]
                        for i in result:
                            timestamp = int(i["timestamp"].timestamp())
                            self.jyxhb_dl3_map[timestamp] = i["jyxhb_dl3"]
                        self.jyxhb_dl3_map = self.fill_dict(self.jyxhb_dl3_map)
                        self.jyxhb_dl3_map = self.fill_none(start_time, min(self.jyxhb_dl3_map.keys()),
                                                            self.jyxhb_dl3_map)
                        sort_keys = sorted(self.jyxhb_dl3_map.keys())
                        for m in sort_keys:
                            if m >= start_time and m <= end_time1:
                                data.append(self.jyxhb_dl3_map[m])
                        obj["data"] = data

                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    send_data.append(obj)
                if r == "jyxhb_dl4":
                    obj = {}
                    obj["name"] = "jyxhb_dl4"
                    obj["title"] = "浆液循环泵电流4"
                    obj["step"] = config["rtstep"]
                    data = []
                    sort_keys = sorted(self.jyxhb_dl4_map.keys())
                    little = min(sort_keys)
                    big = max(sort_keys)
                    end_time1 = end_time
                    if end_time1 >= big:
                        end_time1 = big
                    if start_time >= little and end_time1 <= big:
                        for i in sort_keys:
                            if i >= start_time and i <= end_time1:
                                data.append(self.jyxhb_dl4_map[i])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    elif (start_time < little and end_time1 <= big and end_time1 >= little) or end_time1 < little:
                        # 查找数据库
                        str_end_time1 = datetime.datetime.fromtimestamp(little).strftime("%Y-%m-%d %H:%M:%S")

                        result = self.engine.execute(
                            "select timestamp,jyxhb_dl4 from " + self.rt_table_name + " where timestamp between %s and %s order by  timestamp ",
                            (str_start_time, str_end_time1)).fetchall()
                        result = [dict(i) for i in result]
                        for i in result:
                            timestamp = int(i["timestamp"].timestamp())
                            self.jyxhb_dl4_map[timestamp] = i["jyxhb_dl4"]
                        self.jyxhb_dl4_map = self.fill_dict(self.jyxhb_dl4_map)
                        self.jyxhb_dl4_map = self.fill_none(start_time, min(self.jyxhb_dl4_map.keys()),
                                                            self.jyxhb_dl4_map)
                        sort_keys = sorted(self.jyxhb_dl4_map.keys())
                        for m in sort_keys:
                            if m >= start_time and m <= end_time1:
                                data.append(self.jyxhb_dl4_map[m])
                        obj["data"] = data
                        # for r in sort_keys:
                        #     if r >= start_time and r <= end_time1:
                        #         obj["start_time"] = r
                        #         break
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    send_data.append(obj)

                # 预测值
                # 寻优系统不需要 pre_jyq_so2 此值
                # if r == "pre_jyq_so2":
                #     obj = {}
                #     obj["name"] = "pre_jyq_so2"
                #     obj["title"] = "预测净烟气so2"
                #     obj["step"] = config["rtstep"]
                #     data = []
                #     if len(self.pre_jyq_so2_map) == 0:
                #         obj["data"] = data
                #         obj["start_time"] = start_time
                #         obj["end_time"] = end_time1
                #         break
                #
                #     sort_keys = sorted(self.pre_jyq_so2_map.keys())
                #     little = min(sort_keys)
                #     big = max(sort_keys)
                #     end_time1 = end_time + config["mod_pre_step"]
                #     if end_time1 >= big:
                #         end_time1 = big
                #         if start_time >= little and end_time1 <= big:
                #             for i in sort_keys:
                #                 if i >= start_time and i <= end_time1:
                #                     data.append(self.pre_jyq_so2_map[i])
                #                     obj["data"] = data
                #                     obj["start_time"] = start_time
                #                     obj["end_time"] = end_time1
                #         elif (start_time < little and end_time1 <= big and end_time1 >= little) or end_time1 < little:
                #         # 查找数据库
                #             str_end_time = datetime.datetime.fromtimestamp(little).strftime("%Y-%m-%d %H:%M:%S")
                #
                #             pre_jyq_result = self.engine.execute(
                #                 "select time,so2 from "+self.mod_pre_table_name+" where  time between %s and %s order by  time ",
                #                 (str_start_time, str_end_time)).fetchall()
                #             for i in pre_jyq_result:
                #                 i = dict(i)
                #
                #                 self.pre_jyq_so2_map[int(i["time"].timestamp())] = i["so2"]
                #             self.pre_jyq_so2_map = self.fill_pre_dict(self.pre_jyq_so2_map)
                #             self.pre_jyq_so2_map = self.fill_none(start_time, min(self.pre_jyq_so2_map.keys()),
                #                                               self.pre_jyq_so2_map)
                #             sort_keys = sorted(self.pre_jyq_so2_map.keys())
                #             for m in sort_keys:
                #                 if m >= start_time and m <= end_time1:
                #                     data.append(self.pre_jyq_so2_map[m])
                #             obj["data"] = data
                #             # for r in sort_keys:
                #             #     if r >= start_time and r <= end_time1:
                #             #         obj["start_time"] = r
                #             #         break
                #             obj["start_time"] = start_time
                #             obj["end_time"] = end_time1
                #     send_data.append(obj)

                # 推荐值
                if r == "tjgjll":
                    obj = {}
                    obj["name"] = "tjgjll"
                    obj["title"] = "推荐供浆量"
                    obj["step"] = config["rtstep"]
                    data = []
                    if len(self.tjgjll_map) == 0:
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                        break
                    sort_keys = sorted(self.tjgjll_map.keys())
                    little = min(sort_keys)
                    big = max(sort_keys)
                    end_time1 = end_time + config["mod_pre_step"]
                    if end_time1 >= big:
                        end_time1 = big
                    if start_time >= little and end_time1 <= big:
                        for i in sort_keys:
                            if i >= start_time and i <= end_time1:
                                data.append(self.tjgjll_map[i])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    elif (start_time < little and end_time1 <= big and end_time1 >= little) or end_time1 < little:
                        # 查找数据库
                        str_end_time1 = datetime.datetime.fromtimestamp(little).strftime("%Y-%m-%d %H:%M:%S")
                        result = self.engine.execute(
                            "select timestamp,slurry_supply from " + self.contro_table_name + " where timestamp between %s and %s   order by  timestamp ",
                            (str_start_time, str_end_time1)).fetchall()
                        for i in result:
                            i = dict(i)
                            self.tjgjll_map[int(i["timestamp"].timestamp())] = i["slurry_supply"]
                        self.tjgjll_map = self.fill_pre_dict(self.tjgjll_map)
                        self.tjgjll_map = self.fill_none(start_time, min(self.tjgjll_map.keys()),
                                                         self.tjgjll_map)
                        sort_keys = sorted(self.tjgjll_map.keys())
                        for m in sort_keys:
                            if m >= start_time and m <= end_time1:
                                data.append(self.tjgjll_map[m])
                        obj["data"] = data
                        # for r in sort_keys:
                        #     if r >= start_time and r <= end_time1:
                        #         obj["start_time"] = r
                        #         break
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1

                    send_data.append(obj)
                if r == "tjph":
                    obj = {}
                    obj["name"] = "tjph"
                    obj["title"] = "推荐ph值"
                    obj["step"] = config["rtstep"]
                    data = []
                    if len(self.tjph_map) == 0:
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                        break
                    sort_keys = sorted(self.tjph_map.keys())
                    little = min(sort_keys)
                    big = max(sort_keys)
                    end_time1 = end_time + config["mod_pre_step"]
                    if end_time1 >= big:
                        end_time1 = big
                    if start_time >= little and end_time1 <= big:
                        for i in sort_keys:
                            if i >= start_time and i <= end_time1:
                                data.append(self.tjph_map[i])
                        obj["data"] = data
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1
                    elif (start_time < little and end_time1 <= big and end_time1 >= little) or end_time1 < little:
                        # 查找数据库
                        str_end_time1 = datetime.datetime.fromtimestamp(little).strftime("%Y-%m-%d %H:%M:%S")

                        result = self.engine.execute(
                            "select timestamp,rec_ph_value from " + self.contro_table_name + " where timestamp between %s and %s  order by  timestamp ",
                            (str_start_time, str_end_time1)).fetchall()
                        for i in result:
                            i = dict(i)
                            self.tjph_map[int(i["timestamp"].timestamp())] = i["rec_ph_value"]
                        self.tjph_map = self.fill_pre_dict(self.tjph_map)
                        self.tjph_map = self.fill_none(start_time, min(self.tjph_map.keys()),
                                                       self.tjph_map)
                        sort_keys = sorted(self.tjph_map.keys())
                        for m in sort_keys:
                            if m >= start_time and m <= end_time1:
                                data.append(self.tjph_map[m])
                        obj["data"] = data
                        # for r in sort_keys:
                        #     if r >= start_time and r <= end_time1:
                        #         obj["start_time"] = r
                        #         break
                        obj["start_time"] = start_time
                        obj["end_time"] = end_time1

                    send_data.append(obj)

            self.send_obj["chart"] = send_data

            if self.mark["update_end_time"]:
                self.mark["start_time"] = (
                        datetime.timedelta(seconds=self.diff_time) + datetime.datetime.strptime(self.mark["start_time"],
                                                                                                "%Y-%m-%d %H:%M:%S")).strftime(
                    "%Y-%m-%d %H:%M:%S")

        except Exception as e:
            traceback.print_exc()
            logging.error("get_send_data 方法出现了异常 为 %s", str(e))
        finally:
            self.lock.release()

    def start(self):
        logging.info("start datahandler")
        threading.Thread(target=self.miniotor).start()
        self.timing_clean_data()
        while True:
            try:
                self.lock.acquire()
                if self.mark["is_send"]:
                    self.mark["is_send"] = False
                    self.lock.release()
                    self.get_send_data()

                    if self.send_obj.get("data"):
                        self.GLOBAL_DATA["data"] = self.send_obj["data"]
                    if self.send_obj.get("chart"):
                        self.GLOBAL_DATA["chart"] = self.send_obj["chart"]

                    # logging.info(f"接收到数据：{self.send_obj}")
                else:
                    self.lock.release()
                time.sleep(0.5)
            except Exception as e:
                traceback.print_exc()
                logging.error("start_ws 方法出现了异常 %s", str(e))
