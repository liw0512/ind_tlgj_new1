from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P4PC = ROOT / "system/model/Process4MapControl.py"
CONFIG = ROOT / "system/model/config/process4map_config.py"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def replace_block(source: str, start: str, end: str, replacement: str) -> str:
    a = source.index(start)
    b = source.index(end, a)
    return source[:a] + replacement + source[b:]


source = read(P4PC)

# Active-version-aligned training watermark + one canonical time-order normalization.
marker = "    @staticmethod\n    def _parse_activation_time(value):\n"
if "def _active_training_watermark" not in source:
    insert = '''    def _active_training_watermark(self):
        """读取当前已激活第二模块版本的数据水位。

        watermark 不单独维护一份可漂移状态，而是绑定 active_version.json 指向的
        slurry policy snapshot。只有完整训练并成功激活的版本才会成为下一轮增量起点。
        """
        active_version = self._read_active_version()
        summary_path = (
            Path(self._core_path("slurry_policy_output_root"))
            / "snapshots"
            / active_version
            / "training_summary.json"
        )
        if not summary_path.is_file():
            raise FileNotFoundError(
                "当前激活第二模块缺少 training_summary.json，无法确定增量 watermark: %s"
                % summary_path
            )
        with summary_path.open("r", encoding="utf-8") as stream:
            summary = json.load(stream)
        raw_timestamp = summary.get("last_data_timestamp")
        if raw_timestamp in (None, ""):
            raise RuntimeError(
                "当前激活第二模块 training_summary.json 缺少 last_data_timestamp；"
                "FAST V4 首次升级请重新执行一次完整初次训练。"
            )
        timestamp = pd.to_datetime(raw_timestamp, errors="coerce")
        if pd.isna(timestamp):
            raise RuntimeError(
                "当前激活第二模块 last_data_timestamp 无法解析: %r" % raw_timestamp
            )
        timestamp = pd.Timestamp(timestamp)
        if timestamp.tzinfo is not None:
            timestamp = timestamp.tz_convert(None)
        return timestamp, active_version

    @staticmethod
    def _normalize_training_dataframe(df, *, context="training"):
        """训练数据统一时间契约：date 可解析、稳定升序，再交给各模块/写 CSV。"""
        if df is None:
            return None
        result = df.copy()
        if "date" not in result.columns:
            raise RuntimeError(f"{context} 缺少必需时间字段 date")
        parsed = pd.to_datetime(result["date"], errors="coerce")
        invalid_count = int(parsed.isna().sum())
        if invalid_count:
            raise RuntimeError(
                f"{context} 存在 {invalid_count} 条无法解析的 date，拒绝进入训练"
            )
        result["date"] = parsed
        # 数据库跨月拼接、驱动返回顺序都不能作为训练时序事实源；这里统一稳定升序。
        result.sort_values("date", inplace=True, kind="mergesort")
        result.reset_index(drop=True, inplace=True)
        if not result["date"].is_monotonic_increasing:
            raise RuntimeError(f"{context} 按 date 排序后仍非单调递增")
        return result

'''
    source = source.replace(marker, insert + marker, 1)

source = replace_block(
    source,
    "    def _database_table_names(self, use_model_result_table=False):\n",
    "    def _database_table_exists(self, table_name):\n",
    '''    def _database_table_names(
        self,
        use_model_result_table=False,
        start_time=None,
        end_time=None,
    ):
        """返回训练可能涉及的月表。

        初次/无 watermark 时保持“本月+上月”兼容行为；增量有 watermark 时按
        watermark 月份一直枚举到当前月份，避免跨月甚至长时间停机后漏数据。
        """
        prefix = (
            self.process_config.persistence.model_result_table_prefix
            if use_model_result_table
            else self.process_config.persistence.filter_table_prefix
        )
        if start_time is None:
            now = datetime.datetime.now()
            current = f"{prefix}{now.year}_{now.month}"
            if now.month == 1:
                previous = f"{prefix}{now.year - 1}_12"
            else:
                previous = f"{prefix}{now.year}_{now.month - 1}"
            return [current, previous]

        start = pd.Timestamp(start_time).to_period("M")
        end = pd.Timestamp(end_time or datetime.datetime.now()).to_period("M")
        if end < start:
            return []
        periods = pd.period_range(start=start, end=end, freq="M")
        return [f"{prefix}{period.year}_{period.month}" for period in periods]

''',
)

source = replace_block(
    source,
    "    def _count_recent_database_records(self, settings):\n",
    "    def get_recent_days_data(\n",
    '''    def _count_recent_database_records(
        self,
        settings,
        *,
        since_time=None,
        until_time=None,
    ):
        target_count = self._database_target_count(settings)
        available = 0
        until = pd.Timestamp(until_time or datetime.datetime.now())
        tables = self._database_table_names(
            settings["use_model_result_table"],
            start_time=since_time,
            end_time=until,
        )
        for table_name in tables:
            try:
                if not self._database_table_exists(table_name):
                    continue
                if since_time is None:
                    row = self.engine.execute(
                        f"SELECT COUNT(*) FROM {table_name}"
                    ).fetchone()
                else:
                    row = self.engine.execute(
                        f"SELECT COUNT(*) FROM {table_name} WHERE date > %s AND date <= %s",
                        (pd.Timestamp(since_time).to_pydatetime(), until.to_pydatetime()),
                    ).fetchone()
                available += int(row[0]) if row else 0
                # readiness 只需要知道是否达到一个训练周期的数据量，不必继续全表计数。
                if available >= target_count:
                    break
            except Exception as exc:
                logging.warning("统计训练表 %s 失败: %s", table_name, exc)
        return min(available, target_count), target_count

''',
)

source = replace_block(
    source,
    "    def get_recent_days_data(\n",
    "    def _load_training_data(self, mode):\n",
    '''    def get_recent_days_data(
        self,
        day,
        use_model_result_table=False,
        record_limit=None,
        minimum_ratio=None,
        since_time=None,
        until_time=None,
    ):
        """读取数据库训练数据；有 watermark 时读取其后的全部新增数据。

        无论数据库自身返回顺序如何，最终都统一按 date 升序稳定排序后返回。
        """
        try:
            settings = {
                "days": int(day),
                "database_record_limit": int(record_limit or 0),
                "use_model_result_table": bool(use_model_result_table),
            }
            target_count = self._database_target_count(settings)
            ratio = (
                float(self.process_config.training.database_minimum_data_ratio)
                if minimum_ratio is None
                else float(minimum_ratio)
            )
            minimum_required = max(1, int(target_count * ratio))
            frames = []
            until = pd.Timestamp(until_time or datetime.datetime.now())
            tables = self._database_table_names(
                use_model_result_table,
                start_time=since_time,
                end_time=until,
            )

            # 无 watermark 的初次训练仍按目标条数截取；有 watermark 的增量必须把
            # watermark 后的所有新增数据读全，不能因“最近3天条数”截断追赶数据。
            remaining = None if since_time is not None else target_count
            for table_name in tables:
                if remaining is not None and remaining <= 0:
                    break
                try:
                    if not self._database_table_exists(table_name):
                        logging.warning("训练数据表不存在: %s", table_name)
                        continue
                    params = []
                    clauses = []
                    if since_time is not None:
                        clauses.append("date > %s")
                        params.append(pd.Timestamp(since_time).to_pydatetime())
                    clauses.append("date <= %s")
                    params.append(until.to_pydatetime())
                    sql = f"SELECT * FROM {table_name} WHERE " + " AND ".join(clauses)
                    # ORDER BY 只是数据库侧优化；P4PC 返回前仍会再次强制排序。
                    sql += " ORDER BY date ASC"
                    if remaining is not None:
                        sql += f" LIMIT {int(remaining)}"
                    result = self.engine.execute(sql, tuple(params))
                    rows = result.fetchall()
                    if rows:
                        frame = pd.DataFrame(rows, columns=result.keys())
                        frames.append(frame)
                        if remaining is not None:
                            remaining -= len(frame)
                        logging.info("从 %s 读取训练数据 %s 条", table_name, len(frame))
                except Exception as exc:
                    logging.warning("读取训练数据表 %s 失败: %s", table_name, exc)

            if not frames:
                logging.warning("数据库未取得训练数据")
                return None

            df = pd.concat(frames, ignore_index=True, sort=False)
            df = self._normalize_training_dataframe(df, context="database training data")
            if since_time is None and len(df) > target_count:
                df = df.tail(target_count).reset_index(drop=True)
                df = self._normalize_training_dataframe(df, context="database training tail")
            logging.info(
                "数据库训练取数完成: watermark=%s, requested_cycle_records=%s, actual=%s, minimum_by_ratio=%s, first=%s, last=%s",
                since_time,
                target_count,
                len(df),
                minimum_required,
                df["date"].iloc[0] if not df.empty else None,
                df["date"].iloc[-1] if not df.empty else None,
            )
            if len(df) < minimum_required:
                logging.warning(
                    "数据库训练数据完整率不足: actual=%s, required=%s",
                    len(df),
                    minimum_required,
                )
            return df
        except Exception as exc:
            logging.error("获取训练数据时发生错误: %s", exc)
            traceback.print_exc()
            return None

''',
)

source = replace_block(
    source,
    "    def _load_training_data(self, mode):\n",
    "    def _save_training_work_csv(self, df, settings):\n",
    '''    def _load_training_data(self, mode):
        settings = self._training_mode_settings(mode)
        source = settings["source"]
        if source not in {"database", "csv"}:
            raise ValueError(
                f"{mode} data_source={source!r} 无效，仅支持 'database' 或 'csv'"
            )

        watermark_time = None
        watermark_version = None
        if str(mode).strip().lower() == "incremental":
            watermark_time, watermark_version = self._active_training_watermark()
            logging.info(
                "增量训练 watermark: version=%s, last_data_timestamp=%s",
                watermark_version,
                watermark_time,
            )

        if source == "csv":
            source_path = self._resolve_training_path(settings["source_csv"])
            if not source_path:
                raise ValueError(f"{mode} 训练已选择 csv，但 source_csv 未配置")
            if not os.path.isfile(source_path):
                raise FileNotFoundError(f"{mode} 训练 CSV 不存在: {source_path}")
            df = pd.read_csv(source_path)
            df = self._normalize_training_dataframe(df, context=f"{mode} source CSV")
            if watermark_time is not None:
                df = df[df["date"] > watermark_time].copy().reset_index(drop=True)
            logging.info(
                "%s 训练使用指定 CSV: %s, watermark=%s, records=%s",
                mode,
                source_path,
                watermark_time,
                len(df),
            )
            required = settings["minimum_records"]
        else:
            target_count = self._database_target_count(settings)
            df = self.get_recent_days_data(
                day=settings["days"],
                use_model_result_table=settings["use_model_result_table"],
                record_limit=(None if watermark_time is not None else target_count),
                since_time=watermark_time,
                until_time=datetime.datetime.now(),
            )
            ratio_required = int(
                target_count * float(self.process_config.training.database_minimum_data_ratio)
            )
            required = max(settings["minimum_records"], ratio_required)

        if df is None or len(df) < required:
            actual = 0 if df is None else len(df)
            raise RuntimeError(
                f"{mode} 训练数据不足: actual={actual}, required={required}, source={source}, watermark={watermark_time}"
            )
        df = self._normalize_training_dataframe(df, context=f"{mode} final training frame")
        if watermark_time is not None and not df.empty:
            first = pd.Timestamp(df["date"].iloc[0])
            if first <= watermark_time:
                raise RuntimeError(
                    "增量训练数据边界错误：第一条必须严格晚于 active watermark；"
                    f"first={first}, watermark={watermark_time}"
                )
        return df, settings

''',
)

source = replace_block(
    source,
    "    def _save_training_work_csv(self, df, settings):\n",
    "    def check_data_accumulation(self, mode='initial'):\n",
    '''    def _save_training_work_csv(self, df, settings):
        """训练工作 CSV 的唯一落盘入口；落盘前再次强制按 date 升序。"""
        df = self._normalize_training_dataframe(
            df,
            context=f"{settings['mode']} work CSV",
        )
        output_path = self._resolve_training_path(settings["work_csv"])
        if not output_path:
            raise ValueError(f"{settings['mode']} work_csv 未配置")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        df.to_csv(
            output_path,
            index=False,
            date_format="%Y-%m-%d %H:%M:%S",
        )
        logging.info(
            "%s 训练工作 CSV 已按 date 升序保存: %s, records=%s, first=%s, last=%s",
            settings["mode"],
            output_path,
            len(df),
            df["date"].iloc[0] if not df.empty else None,
            df["date"].iloc[-1] if not df.empty else None,
        )
        return output_path

''',
)

source = replace_block(
    source,
    "    def check_data_accumulation(self, mode='initial'):\n",
    "    def insert_data(self, data):\n",
    '''    def check_data_accumulation(self, mode="initial"):
        """按训练模式检查数据量；增量只统计 active watermark 之后的新数据。"""
        try:
            settings = self._training_mode_settings(mode)
            watermark_time = None
            if str(mode).strip().lower() == "incremental":
                watermark_time, watermark_version = self._active_training_watermark()
                logging.info(
                    "检查增量数据积累: active=%s, watermark=%s",
                    watermark_version,
                    watermark_time,
                )

            if settings["source"] == "csv":
                source_path = self._resolve_training_path(settings["source_csv"])
                if not source_path or not os.path.isfile(source_path):
                    logging.info("%s 训练 CSV 未就绪: %s", mode, source_path)
                    return False
                frame = pd.read_csv(source_path)
                frame = self._normalize_training_dataframe(
                    frame, context=f"{mode} accumulation CSV"
                )
                if watermark_time is not None:
                    frame = frame[frame["date"] > watermark_time]
                count = len(frame)
                required = settings["minimum_records"]
            elif settings["source"] == "database":
                count, target = self._count_recent_database_records(
                    settings,
                    since_time=watermark_time,
                    until_time=datetime.datetime.now(),
                )
                required = max(
                    settings["minimum_records"],
                    int(target * float(self.process_config.training.database_minimum_data_ratio)),
                )
            else:
                logging.error("%s 训练数据源无效: %s", mode, settings["source"])
                return False
            logging.info(
                "%s 训练数据检查: source=%s, watermark=%s, actual=%s, required=%s, cycle_days=%s",
                mode,
                settings["source"],
                watermark_time,
                count,
                required,
                settings["days"],
            )
            return count >= required
        except Exception as exc:
            logging.error("检查 %s 训练数据时发生错误: %s", mode, exc)
            return False

''',
)

write(P4PC, source)

cfg = read(CONFIG)
cfg = cfg.replace(
    "    incremental_training_days: int = 3  # 数据库模式下每次增量训练回看的天数。",
    "    incremental_training_days: int = 3  # 增量周期期望数据量对应天数；正式增量实际从 active watermark 之后读取全部新数据。",
)
cfg = cfg.replace(
    "    incremental_database_record_limit: int = 0  # 数据库最多读取条数；0 表示按 incremental_training_days 自动计算。",
    "    incremental_database_record_limit: int = 0  # 无 watermark/兼容取数时的条数上限；正式增量有 watermark 时不会截断未学习的新数据。",
)
write(CONFIG, cfg)
