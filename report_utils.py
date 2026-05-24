from __future__ import annotations
import os
import shutil
import subprocess
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from shioaji_loader import ShioajiSafeLoader

# =========================================================
# AI 評分與市場狀態判定核心邏輯
# =========================================================

def _calculate_ai_score(df_day: pd.DataFrame, df30: pd.DataFrame, relative_vol: float) -> int:
    """根據 AI 條件樹計算分數"""
    score = 0
    # 日K 條件
    if _slope(df_day["MA20"], 3) > 0: score += 2
    if float(df_day.iloc[-1]["close"]) > float(df_day.iloc[-1]["MA60"]): score += 2
    
    # 30分K 條件
    if _高低結構(df30, 8) == "高點墊高": score += 2
    if _量價_check(df_day, df30) == "量縮": score += 2
    
    # 攻擊與量能
    if _價格突破平台(df30): score += 5
    if relative_vol > 2: score += 3
    
    return score

def _determine_market_state(日方向: str, 三十分方向: str) -> str:
    """AI 四種劇本判讀"""
    if 日方向 == "多" and 三十分方向 == "多": return "強勢主升"
    if 日方向 == "多" and 三十分方向 == "空": return "短線轉弱"
    if 日方向 == "空" and 三十分方向 == "多": return "反彈"
    return "主跌"

# =========================================================
# 輔助計算函數 (補完區)
# =========================================================

def _resample_30(df5: pd.DataFrame) -> pd.DataFrame:
    """排除午休的真實30分K"""
    df = df5.between_time("09:00", "13:30").copy()
    return df.resample("30min", closed="left", label="left").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
    }).dropna()

def _resample_day(df5: pd.DataFrame) -> pd.DataFrame:
    """合成日K"""
    return df5.resample("D", closed="left", label="left").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
    }).dropna()

def _add_ma(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["MA20"] = df["close"].rolling(20, min_periods=1).mean()
    df["MA60"] = df["close"].rolling(60, min_periods=1).mean()
    return df

def _slope(series: pd.Series, lookback: int) -> float:
    return float(series.iloc[-1] - series.iloc[-1-lookback]) if len(series) > lookback else 0

def _方向(df: pd.DataFrame) -> str:
    return "多" if float(df.iloc[-1]["close"]) > float(df.iloc[-1]["MA20"]) else "空"

def _高低結構(df: pd.DataFrame, n: int) -> str:
    recent = df.iloc[-n:]
    return "高點墊高" if recent["high"].iloc[-1] >= recent["high"].max() else "整理"

def _量價_check(df_day: pd.DataFrame, df30: pd.DataFrame) -> str:
    return "量縮" if df30["volume"].iloc[-1] < df30["volume"].rolling(20).mean().iloc[-1] else "放量"

def _價格突破平台(df30: pd.DataFrame) -> bool:
    return float(df30.iloc[-1]["close"]) > float(df30["close"].iloc[-8:-1].max())

# =========================================================
# 主分析流程
# =========================================================

def _analyze(symbol: str, loader: ShioajiSafeLoader):
    # 1. 資料處理
    df5 = loader.fetch_kbars(symbol)
    df30 = _resample_30(df5)
    df_day = _resample_day(df5)
    
    df_day = _add_ma(df_day)
    df30 = _add_ma(df30)
    
    # 2. 狀態與分數計算
    日方向 = _方向(df_day)
    三十分方向 = _方向(df30)
    market_state = _determine_market_state(日方向, 三十分方向)
    score = _calculate_ai_score(df_day, df30, 1.5) # RV 需根據實際邏輯計算
    
    return {
        "股票": symbol,
        "狀態": market_state,
        "AI分數": score,
        "日K方向": 日方向,
        "30分K方向": 三十分方向
    }
# (其餘報表生成與 Git 同步邏輯維持不變)
