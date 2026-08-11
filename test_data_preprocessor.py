# -*- coding: utf-8 -*-
"""
使用 data_preprocessor1.DataPreprocessor 对离线 CSV 数据进行测试

========== 可配置全局变量（直接修改此处即可） ==========
INPUT_CSV     : 要处理的 CSV 文件路径
OUTPUT_CSV    : 处理结果输出路径
PROCESS_MODE  : 处理模式
                 "filter"   - 仅数据滤波（限幅 + 滤波）
                 "features" - 仅特征生成（泵状态 / PH合并 / 液气比 / 脱硫效率）
                 "both"     - 数据滤波 + 特征生成
MAX_ROWS      : 最多处理多少行（None 表示全部；先用小数值测试，如 100/1000）
PRINT_EVERY   : 每处理多少行打印一次进度
=====================================================
"""

import os
import sys
import time

# ===================== 可配置全局变量 =====================
INPUT_CSV = r"F:\xiregangchang\ind_optim_serv\files\selected_30s_filtered.csv"
OUTPUT_CSV = r"F:\xiregangchang\ind_optim_serv\files\selected_30s_processed.csv"
PROCESS_MODE = "both"  # "filter" / "features" / "both"
MAX_ROWS = None        # None=全部；测试可设 100/1000 先跑通
PRINT_EVERY = 1000     # 进度打印间隔（行数）
# ========================================================

import pandas as pd

# 抑制 joblib 找不到物理核的告警刷屏
os.environ.setdefault("LOKY_MAX_CPU_COUNT", "4")

# 确保能导入 system 包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from system.model.map_control.data_preprocessor1 import DataPreprocessor

VALID_MODES = ("filter", "features", "both")


def process_one_row(preprocessor, data_dict, mode):
    """对单行数据按模式处理"""
    if mode in ("filter", "both"):
        data_dict = preprocessor.filter_realtime_data(data_dict)
    if mode in ("features", "both"):
        data_dict = preprocessor.generate_features(data_dict)
    return data_dict


def main():
    if PROCESS_MODE not in VALID_MODES:
        raise ValueError(f"PROCESS_MODE 不合法: {PROCESS_MODE}，可选值: {VALID_MODES}")

    if not os.path.exists(INPUT_CSV):
        raise FileNotFoundError(f"输入文件不存在: {INPUT_CSV}")

    out_dir = os.path.dirname(os.path.abspath(OUTPUT_CSV))
    os.makedirs(out_dir, exist_ok=True)

    print(f"加载数据: {INPUT_CSV}")
    df = pd.read_csv(INPUT_CSV)
    if MAX_ROWS is not None:
        df = df.head(MAX_ROWS)
    print(f"原始数据: {len(df)} 行, {len(df.columns)} 列")
    print(f"列名: {df.columns.tolist()}")
    print(f"处理模式: {PROCESS_MODE}")

    preprocessor = DataPreprocessor()

    start = time.time()
    last_print = time.time()
    results = []
    total = len(df)
    for idx, row in df.iterrows():
        data_dict = row.to_dict()
        try:
            processed = process_one_row(preprocessor, data_dict, PROCESS_MODE)
            results.append(processed)
        except Exception as e:
            print(f"第 {idx} 行处理失败，跳过: {e}")

        if (idx + 1) % PRINT_EVERY == 0:
            now = time.time()
            print(f"已处理 {idx + 1}/{total} 行, "
                  f"耗时 {now - start:.1f}s, 本次{PRINT_EVERY}行用时 {now - last_print:.1f}s")
            last_print = now

    elapsed = time.time() - start
    print(f"处理完成，用时 {elapsed:.2f} 秒，成功 {len(results)} / {total} 行")

    if not results:
        print("无处理结果，退出")
        return

    result_df = pd.DataFrame(results)
    result_df.to_csv(OUTPUT_CSV, index=False)
    print(f"结果已保存: {OUTPUT_CSV}")
    print(f"结果数据: {len(result_df)} 行, {len(result_df.columns)} 列")
    print(f"新增列: {[c for c in result_df.columns if c not in df.columns]}")


if __name__ == "__main__":
    main()
