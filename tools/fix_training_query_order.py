from pathlib import Path

path = Path(__file__).resolve().parents[1] / "system/model/Process4MapControl.py"
source = path.read_text(encoding="utf-8-sig")
old = '''                    # ORDER BY 只是数据库侧优化；P4PC 返回前仍会再次强制排序。\n                    sql += " ORDER BY date ASC"\n                    if remaining is not None:\n'''
new = '''                    # 初次训练要先从数据库取“最新 N 条”，因此无 watermark 时 DESC；\n                    # 增量则从 watermark 向后读取。无论哪种情况，P4PC 返回/落 CSV 前\n                    # 都会再次统一按 date ASC 排序，数据库返回顺序不作为训练事实源。\n                    sql += (\n                        " ORDER BY date DESC"\n                        if since_time is None\n                        else " ORDER BY date ASC"\n                    )\n                    if remaining is not None:\n'''
if old not in source:
    raise SystemExit("target query-order block not found")
path.write_text(source.replace(old, new, 1), encoding="utf-8")
