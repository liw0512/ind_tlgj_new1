from __future__ import annotations

from typing import Any, Dict, List, Optional

from system.model.config.plant_config import PLANT_CONFIG, enabled_towers

from .history_data_service import HistoryDataService


class ResponsiveHistoryDataService(HistoryDataService):
    """在原历史查询服务上补充 GUI 所需的循环泵历史字段。

    原 HistoryDataService 继续负责数据库、跨月查询、缺口识别和降采样；这里仅把
    plant_config 中启用塔的 circulation_pumps 动态加入历史查询字段，避免 GUI 写死
    xstjyxhb_ADL/.../EDL。
    """

    def __init__(self, db_url: Optional[str] = None) -> None:
        super().__init__(db_url=db_url)

        meta: Dict[str, Any] = dict(self.process_meta)
        columns: List[str] = list(meta.get("columns", ()) or ())
        circulation_series: List[Dict[str, str]] = []

        for tower in enabled_towers(PLANT_CONFIG):
            tower_name = str(tower.get("display_name") or tower.get("tower_id") or "吸收塔")
            for pump in tower.get("circulation_pumps", []) or []:
                column = str(pump.get("value_column") or "").strip()
                if not column:
                    continue
                columns.append(column)
                circulation_series.append(
                    {
                        "column": column,
                        "name": str(pump.get("display_name") or column),
                        "unit": str(pump.get("unit") or ""),
                        "tower": tower_name,
                        "side": "left",
                    }
                )

        meta["columns"] = tuple(dict.fromkeys(columns))
        meta["circulation_series"] = circulation_series
        self.process_meta = meta
