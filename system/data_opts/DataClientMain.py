import datetime
import traceback
import uuid
from sqlalchemy import create_engine
import time
import random
from system.data_opts.AvgData import AvgData
import logging
from system.base.config.SysConfig import config
from system.model.Process4MapControl import ProcessForMapConsole
from system.base.LogUntil import setup_log
from concurrent.futures import ThreadPoolExecutor
import http.client
import pandas as pd
logging = setup_log("data_client_main")

class DataClientMain():

    def __init__(self,GLOBAL_DATA):

        self.GLOBAL_DATA = GLOBAL_DATA
        self.engine = create_engine(config["dbconnetion"])
        self.data = []

        self.process_for_mapconsole = ProcessForMapConsole(self.GLOBAL_DATA)
        self.map_console_result = []

        self.pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix='slave_thread')
        self.pool4mapconsole = ThreadPoolExecutor(max_workers=3, thread_name_prefix='pool4mapconsole_thread')

        self.direct = []
        self.fill_result()
        self.count = 0  # 计数器
        self.copy_data = None
        self.rt_table_name = "t_data1_rt_" + str(datetime.datetime.now().year) + "_" + str(
            datetime.datetime.now().month)
        self.avgData = AvgData()
        self.hour = 0
        self.mouth = 0
        self.year = 0
        self.next_heart_val = 0

        self.prvi_dcs_ts = None
        self.is_report_dcs_ts = False

        self.getNewDataTableName()

    def fill_result(self):
        for i in range(config["send_master_redirect_data_sum"]):
            self.direct.append(0)

    def getNewDataTableName(self):
        result = self.engine.execute("select tablename from pg_tables where schemaname ='public'").fetchall()
        djh = {
            "t_data1_rt": [],
        }
        for i in result:
            if str(i[0]).startswith("t_data1_rt"):
                djh["t_data1_rt"].append(i[0])

        for r in djh.keys():
            djh[r] = (sorted(djh[r], reverse=True))

        self.rt_table_name = djh["t_data1_rt"][0]

    # def start(self):
    #
    #     while True:
    #
    #         try:
    #
    #             all_data: list = self.GLOBAL_DATA.get("data")
    #             if len(all_data) > 0:
    #                 values = all_data.pop()
    #                 self.reduction_and_pre_data(values)
    #         except Exception as e:
    #             traceback.print_exc()
    #             logging.error(f"error: {e}")
    #         finally:
    #             time.sleep(1)
    def start(self):
        while True:
            try:
                all_data = self.GLOBAL_DATA.get("data")
                if len(all_data) > 0:
                    # 不再使用pop()，而是使用索引访问并保留原始数据
                    values = all_data[-1].copy()  # 创建最新数据的副本
                    # self.reduction_and_pre_data(values)


            except Exception as e:
                traceback.print_exc()
                logging.error(f"error: {e}")
            finally:
                time.sleep(1)


    def send_cnn_to_dcs(self):

        while True:
            try:

                logging.info(f"send data to dcs...")

                map_control_result = self.GLOBAL_DATA.get('map_control')

                # todo: to dcs code here...

            except Exception as e:
                traceback.print_exc()
                logging.error(str(e))
            finally:
                time.sleep(20)

    def insert_data(self, data):
        try:
            if data:
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
                    "jzfh" float8, 
                    "zml" float8,
                    "xstshsjy_MD" float8,
                    "aptshsjy_MD" float8,
                    "yyq_SO2" float8,
                    "jyq_SO2" float8,
                    "yyq_FC" float8,
                    "jyq_FC" float8,
                    "yyq_O2" float8,
                    "yyq_LL" float8,
                    "jyq_LL" float8,
                    "yyq_WD" float8,
                    "xstjy_PH1" float8,
                    "xstjy_PH2" float8,
                    "xst_YW" float8,
                    "xstshsjy_LL" float8,
                    "xstjyxhb_ADL" float8,   
                    "xstjyxhb_BDL" float8,
                    "xstjyxhb_CDL" float8,
                    "xstjyxhb_DDL" float8,
                    "xstyhfj_ADL" float8,
                    "xstyhfj_BDL" float8,
                    "xstgjb_ADL" float8,
                    "aptjy_PH1" float8,
                    "aptjy_PH2" float8,
                    "apt_YW" float8,
                    "aptshsjy_LL" float8,
                    "aptjyxhb_ADL" float8,
                    "aptjyxhb_BDL" float8,
                    "aptjyxhb_CDL" float8,
                    "aptyhfj_ADL" float8,
                    "aptyhfj_BDL" float8,
                    "aptgjb_ADL" float8,
                    "xstjy_PH" float8,
                    "aptjy_PH" float8,
                    "xst_ADL_status" int,
                    "xst_BDL_status" int,
                    "xst_CDL_status" int,
                    "xst_DDL_status" int,
                    "xst_pump_status" varchar(20),
                    "apt_ADL_status" int,
                    "apt_BDL_status" int,
                    "apt_CDL_status" int,
                    "apt_pump_status" varchar(20),
                    "combined_pump_status" varchar(20),
                    "liquid_gas_ratio" float8,
                    "desulfurization_efficiency" float8,
                    "M0" float8,
                    "M1_daily" float8,
                    "M1_monthly" float8,
                    "xst_base_flow" float8,
                    "apt_base_flow" float8,
                    "xst_fan_flow_mode1" float8,
                    "apt_fan_flow_mode1" float8,
                    "xst_fan_flow_mode2" float8,
                    "apt_fan_flow_mode2" float8,
                    "xstjyxhb_zdh" float8,
                    "xstyhfj_zdh" float8,
                    "xstjbq_zdh" float8,
                    "xstsgpcb_zdh" float8,
                    "xstgjb_zdh" float8,
                    "xstsmj_zdh" float8,
                    "xst_zdh" float8,
                    "aptjyxhb_zdh" float8,
                    "aptyhfj_zdh" float8,
                    "aptjbq_zdh" float8,
                    "aptsgpcb_zdh" float8,
                    "aptgjb_zdh" float8,
                    "aptsmj_zdh" float8,
                    "apt_zdh" float8,
                    "M_elec_hour" float8,
                    "M_stone_primary" float8,
                    "M_stone_secondary" float8,
                    "M_pollute" float8,
                    "M_gypsum" float8,
                    "total_cost" float8
                    );
                    """)
                    self.engine.execute('ALTER TABLE "' + self.rt_table_name + '" ADD PRIMARY KEY ("id")')
                    # self.engine.execute(
                    #     'CREATE INDEX "%s" ON "public".' + self.rt_table_name + ' USING btree ("timestamp" "pg_catalog"."timestamp_ops" ASC NULLS LAST);',
                    #     ("index_" + self.rt_table_name))
                    self.engine.execute("insert into t_table_name(id,table_name,table_alias) values (%s,%s,%s)", (
                        uuid.uuid4(), self.rt_table_name,
                        "实时数据表_" + str(datetime.datetime.now().year) + "_" + str(datetime.datetime.now().month)))
                # logging.info("target_so2 为   %s", str(data[20]))
                # self.engine.execute("update t_sys_conf set value=%s where name='model.so2_val'",(str(data[20])))
                uuid1 = uuid.uuid4()
                # logging.info("data 中的数据为%s", str(data))
                self.engine.execute(

                    f"""
                    insert into {self.rt_table_name} (
                        "id", "date","xstshsjy_MD","yyq_SO2","jyq_SO2" ,
                        "yyq_O2","yyq_LL","jyq_LL",
                        "xst_YW", 
                        "xstjyxhb_ADL", "xstjyxhb_BDL", "xstjyxhb_CDL", "xstjyxhb_DDL","xstjyxhb_EDL",
                        "xstyhfj_ADL",
                        "xstjy_PH", 
                        "xst_ADL_status", "xst_BDL_status", "xst_CDL_status", "xst_DDL_status","xst_EDL_status", "xst_pump_status", 
                        "combined_pump_status", "liquid_gas_ratio", "desulfurization_efficiency"
                    ) values
                    (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                    uuid.uuid4(),
                    data.get("date", pd.Timestamp.now()),
                    data.get("xstshsjy_MD", 0), #一级塔石灰石浆液密度
                    data.get("yyq_SO2", 0), #原烟气SO2
                    data.get("jyq_SO2", 0), #净烟气SO2
                    data.get("yyq_O2", 0), #原烟气O2
                    data.get("yyq_LL", 0), #原烟气流量
                    data.get("jyq_LL", 0), #净烟气流量
                    data.get("xst_YW", 0), #一级塔液位
                    data.get("xstjyxhb_ADL", 0),    #一级塔浆液循环泵电流
                    data.get("xstjyxhb_BDL", 0),
                    data.get("xstjyxhb_CDL", 0),
                    data.get("xstjyxhb_DDL", 0),
                    data.get("xstjyxhb_EDL", 0),
                    data.get("xstyhfj_ADL", 0), #一级塔氧化风机电流
                    data.get("xstjy_PH", 0), #一级塔浆液PH（平均）
                    data.get("xst_ADL_status", 0),  #一级塔泵状态
                    data.get("xst_BDL_status", 0), 
                    data.get("xst_CDL_status", 0),
                    data.get("xst_DDL_status", 0),
                    data.get("xst_EDL_status", 0),
                    data.get("xst_pump_status", ""), #一级塔泵状态（组合）
                    data.get("combined_pump_status", ""), #泵状态（组合）
                    data.get("liquid_gas_ratio", 0), #液气比
                    data.get("desulfurization_efficiency", 0) #脱硫效率
                    )
                    )
            time.sleep(0.02)
        except Exception as e:
            traceback.print_exc()
            logging.error("slave 中的insert_data 出现了异常 异常为==>%s", str(e))

    def reduction_and_pre_data(self, values: dict):

        self.pool.submit(self.insert_data, values)

        # call map console model
        p = []
        p.append(values.copy())
        future0 = self.pool4mapconsole.submit(self.process_for_mapconsole.clean_data, [i for i in p])
        future0.add_done_callback(self.get_map_console_result)

    def get_map_console_result(self, future):
        if future._result:
            self.map_console_result = future._result

    def get_direct(self):
        self.direct.clear()
        self.direct.append(self.hour)
        self.direct.append(self.mouth)
        self.direct.append(self.year)
        self.direct.append(self.data[1])


