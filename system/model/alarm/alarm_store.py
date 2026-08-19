"""低频报警事件 PostgreSQL 持久化。

报警事件与 30 秒过程月表不同：一次报警从触发到恢复只对应一条 ``t_alarm_event``
记录，活动期间仅低频刷新 current/extreme/duration，不按实时周期刷表。
"""
from __future__ import annotations

import json
import queue
import threading
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional

from sqlalchemy import create_engine

from system.base.config.SysConfig import config


ALARM_EVENT_TABLE = "t_alarm_event"


def _quote(name: str) -> str:
    return '"%s"' % str(name).replace('"', '""')


def _to_json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, separators=(",", ":"), default=str)


class AlarmEventStore:
    """报警事件表的唯一读写入口。"""

    def __init__(self) -> None:
        self.engine = create_engine(config["dbconnetion"], pool_pre_ping=True)

    def close(self) -> None:
        try:
            self.engine.dispose()
        except Exception:
            pass

    def ensure_table(self) -> None:
        self.engine.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_quote(ALARM_EVENT_TABLE)} (
                id varchar(36) PRIMARY KEY,
                alarm_key varchar(160) NOT NULL,
                start_time timestamp(6) NOT NULL,
                end_time timestamp(6),
                last_time timestamp(6) NOT NULL,
                level varchar(24) NOT NULL,
                category varchar(32) NOT NULL,
                object_name varchar(160) NOT NULL,
                message text NOT NULL,
                state varchar(24) NOT NULL,
                current_value float8,
                extreme_value float8,
                threshold_text varchar(256),
                unit varchar(32),
                reason_code varchar(160),
                recovery_message text,
                duration_seconds float8,
                detail_json jsonb
            )
            """
        )
        self.engine.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{ALARM_EVENT_TABLE}_start_time "
            f"ON {_quote(ALARM_EVENT_TABLE)} ({_quote('start_time')})"
        )
        self.engine.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{ALARM_EVENT_TABLE}_state "
            f"ON {_quote(ALARM_EVENT_TABLE)} ({_quote('state')})"
        )
        self.engine.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{ALARM_EVENT_TABLE}_alarm_key "
            f"ON {_quote(ALARM_EVENT_TABLE)} ({_quote('alarm_key')})"
        )

        # 目录表只做兼容注册；不存在时不影响报警表本身。
        try:
            self.engine.execute(
                """
                INSERT INTO t_table_name(id, table_name, table_alias)
                SELECT gen_random_uuid(), %s, %s
                WHERE NOT EXISTS (
                    SELECT 1 FROM t_table_name WHERE table_name=%s
                )
                """,
                (ALARM_EVENT_TABLE, "报警事件表", ALARM_EVENT_TABLE),
            )
        except Exception:
            pass

    def interrupt_open_events(self, when: Optional[datetime] = None) -> None:
        """报警服务重启时结束旧进程遗留的 ACTIVE 事件，避免历史永久挂起。"""
        when = when or datetime.now()
        self.engine.execute(
            f"""
            UPDATE {_quote(ALARM_EVENT_TABLE)}
            SET state='INTERRUPTED',
                end_time=%s,
                last_time=%s,
                recovery_message='报警服务重新启动，上一活动事件结束；若条件仍成立将重新触发',
                duration_seconds=EXTRACT(EPOCH FROM (%s - start_time))
            WHERE state='ACTIVE'
            """,
            (when, when, when),
        )

    def insert_event(self, event: Mapping[str, Any]) -> None:
        self.engine.execute(
            f"""
            INSERT INTO {_quote(ALARM_EVENT_TABLE)} (
                id, alarm_key, start_time, end_time, last_time, level, category,
                object_name, message, state, current_value, extreme_value,
                threshold_text, unit, reason_code, recovery_message,
                duration_seconds, detail_json
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb
            )
            ON CONFLICT (id) DO NOTHING
            """,
            (
                str(event.get("id")),
                str(event.get("alarm_key") or ""),
                event.get("start_time"),
                event.get("end_time"),
                event.get("last_time") or event.get("start_time"),
                str(event.get("level") or "ALARM"),
                str(event.get("category") or "SYSTEM"),
                str(event.get("object_name") or "--"),
                str(event.get("message") or "报警"),
                str(event.get("state") or "ACTIVE"),
                event.get("current_value"),
                event.get("extreme_value"),
                str(event.get("threshold_text") or ""),
                str(event.get("unit") or ""),
                str(event.get("reason_code") or ""),
                str(event.get("recovery_message") or ""),
                float(event.get("duration_seconds") or 0.0),
                _to_json(event.get("detail")),
            ),
        )

    def update_active(self, event: Mapping[str, Any]) -> None:
        self.engine.execute(
            f"""
            UPDATE {_quote(ALARM_EVENT_TABLE)}
            SET last_time=%s,
                level=%s,
                category=%s,
                object_name=%s,
                message=%s,
                current_value=%s,
                extreme_value=%s,
                threshold_text=%s,
                unit=%s,
                reason_code=%s,
                duration_seconds=%s,
                detail_json=%s::jsonb
            WHERE id=%s AND state='ACTIVE'
            """,
            (
                event.get("last_time"),
                str(event.get("level") or "ALARM"),
                str(event.get("category") or "SYSTEM"),
                str(event.get("object_name") or "--"),
                str(event.get("message") or "报警"),
                event.get("current_value"),
                event.get("extreme_value"),
                str(event.get("threshold_text") or ""),
                str(event.get("unit") or ""),
                str(event.get("reason_code") or ""),
                float(event.get("duration_seconds") or 0.0),
                _to_json(event.get("detail")),
                str(event.get("id")),
            ),
        )

    def recover_event(self, event: Mapping[str, Any]) -> None:
        self.engine.execute(
            f"""
            UPDATE {_quote(ALARM_EVENT_TABLE)}
            SET end_time=%s,
                last_time=%s,
                state=%s,
                current_value=%s,
                extreme_value=%s,
                recovery_message=%s,
                duration_seconds=%s,
                detail_json=%s::jsonb
            WHERE id=%s
            """,
            (
                event.get("end_time"),
                event.get("last_time"),
                str(event.get("state") or "RECOVERED"),
                event.get("current_value"),
                event.get("extreme_value"),
                str(event.get("recovery_message") or "报警条件已恢复"),
                float(event.get("duration_seconds") or 0.0),
                _to_json(event.get("detail")),
                str(event.get("id")),
            ),
        )

    def query_events(
        self,
        start: datetime,
        end: datetime,
        *,
        level: str = "ALL",
        category: str = "ALL",
        state: str = "ALL",
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        where = ["start_time <= %s", "COALESCE(end_time, last_time, start_time) >= %s"]
        params: List[Any] = [end, start]
        if level and level != "ALL":
            where.append("level=%s")
            params.append(level)
        if category and category != "ALL":
            where.append("category=%s")
            params.append(category)
        if state and state != "ALL":
            if state == "ACTIVE":
                where.append("state='ACTIVE'")
            elif state == "CLOSED":
                where.append("state<>'ACTIVE'")
            else:
                where.append("state=%s")
                params.append(state)

        params.append(max(1, int(limit)))
        result = self.engine.execute(
            f"""
            SELECT id, alarm_key, start_time, end_time, last_time, level, category,
                   object_name, message, state, current_value, extreme_value,
                   threshold_text, unit, reason_code, recovery_message,
                   duration_seconds, detail_json
            FROM {_quote(ALARM_EVENT_TABLE)}
            WHERE {' AND '.join(where)}
            ORDER BY start_time DESC
            LIMIT %s
            """,
            tuple(params),
        )
        keys = list(result.keys())
        rows: List[Dict[str, Any]] = []
        for row in result.fetchall():
            item = dict(zip(keys, row))
            detail = item.get("detail_json")
            if isinstance(detail, str):
                try:
                    detail = json.loads(detail)
                except Exception:
                    detail = {}
            item["detail"] = detail if isinstance(detail, dict) else {}
            rows.append(item)
        return rows


class AlarmPersistenceWorker:
    """后台串行写报警事件，避免 PostgreSQL I/O 阻塞 PyQt 主线程。"""

    def __init__(self, *, max_queue: int = 256) -> None:
        self._queue: "queue.Queue[tuple]" = queue.Queue(maxsize=max(16, int(max_queue)))
        self._thread = threading.Thread(target=self._run, name="alarm-persistence", daemon=True)
        self._started = False
        self._last_error: Optional[str] = None

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._thread.start()

    def submit(self, action: str, event: Mapping[str, Any]) -> None:
        if not self._started:
            self.start()
        item = (str(action), dict(event))
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            # 报警事件量极低；若数据库长时间不可用，优先保留最新事件而不是阻塞前端。
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(item)
            except queue.Full:
                pass

    def _run(self) -> None:
        store: Optional[AlarmEventStore] = None
        try:
            store = AlarmEventStore()
            store.ensure_table()
            store.interrupt_open_events()
            self._last_error = None
        except Exception as exc:
            self._last_error = str(exc)

        while True:
            action, event = self._queue.get()
            try:
                if store is None:
                    store = AlarmEventStore()
                    store.ensure_table()
                if action == "start":
                    store.insert_event(event)
                elif action == "update":
                    store.update_active(event)
                elif action == "recover":
                    store.recover_event(event)
                self._last_error = None
            except Exception as exc:
                self._last_error = str(exc)
                try:
                    if store is not None:
                        store.close()
                except Exception:
                    pass
                store = None
            finally:
                self._queue.task_done()
