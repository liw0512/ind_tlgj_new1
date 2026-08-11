# -*- coding: utf-8 -*-
"""DataClientMain variant that instantiates the slurry-enabled p4pc adapter.

All inherited database/DCS helper methods remain unchanged.  Only construction
of ``process_for_mapconsole`` is replaced, so Application.py can switch the
core without rewriting the copied industrial data client.
"""
from __future__ import annotations

import datetime
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import create_engine

from system.base.config.SysConfig import config
from system.data_opts.AvgData import AvgData
from system.data_opts.DataClientMain import DataClientMain as LegacyDataClientMain
from system.model.Process4SlurryMapControl import ProcessForMapConsole


class DataClientMain(LegacyDataClientMain):
    def __init__(self, GLOBAL_DATA):
        self.GLOBAL_DATA = GLOBAL_DATA
        self.engine = create_engine(config["dbconnetion"])
        self.data = []

        # Only integration change: instantiate the new p4pc adapter.  Its base
        # class still owns the original data/state/training/DB worker threads.
        self.process_for_mapconsole = ProcessForMapConsole(self.GLOBAL_DATA)
        self.map_console_result = []

        self.pool = ThreadPoolExecutor(
            max_workers=3, thread_name_prefix="slave_thread"
        )
        self.pool4mapconsole = ThreadPoolExecutor(
            max_workers=3, thread_name_prefix="pool4mapconsole_thread"
        )

        self.direct = []
        self.fill_result()
        self.count = 0
        self.copy_data = None
        self.rt_table_name = (
            "t_data1_rt_"
            + str(datetime.datetime.now().year)
            + "_"
            + str(datetime.datetime.now().month)
        )
        self.avgData = AvgData()
        self.hour = 0
        self.mouth = 0
        self.year = 0
        self.next_heart_val = 0

        self.prvi_dcs_ts = None
        self.is_report_dcs_ts = False

        self.getNewDataTableName()
