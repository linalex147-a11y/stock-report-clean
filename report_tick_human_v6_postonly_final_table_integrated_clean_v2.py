from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
# 🟢 修正關鍵：補齊 typing 模組導入，解決 NameError 崩潰
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests
from report_utils import *
# 引入安全加載器與您的 config.py 金鑰憑證檔
from shioaji_loader import ShioajiSafeLoader
from config import Config
from market_cache import MarketCache
os.makedirs("cache/tick", exist_ok=True)
CACHE = MarketCache(
    root_dir="cache/tick",
    overlap_days=1,
)

# =========================================================
# 策略設定載入 (介接您資料夾中的 report_config_tick_v1.py)
# =========================================================

try:
    from report_config_tick_v1 import 報表設定  # type: ignore
except ImportError:
    try:
        from report_config import 報表設定  # type: ignore
    except ImportError:
        @dataclass
        class _Fallback報表設定:
            報表標的: Dict[str, str] = field(default_factory=lambda: {"2330": "台積電"})
            回看天數: int = 90
            前高前低天數: int = 3
            平台回看根數: int = 10
            大量區回看根數: int = 15
            大量K量能均線根數: int = 10
            大量K量能倍率: float = 1.4
            大量K實體最小比例: float = 0.006
            報表輸出目錄: str = "report_out"
            發送HTML到TG: bool = False
            分類設定: Dict[str, List[str]] = field(default_factory=lambda: {"自選": ["2330"]})
        報表設定 = _Fallback報表設定()

print("🔥 AI 多週期盤後結構分析器 (Shioaji 版) 啟動")

# =========================================================
# 基礎設定讀取與輔助工具函數
# =========================================================

def _cfg(name: str, default=None):
    return getattr(報表設定, name, default)

def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")

def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")

def _round(v):
    try:
        return round(float(v), 2)
    except Exception:
        return None

def _price(v) -> str:
    if v is None:
        return "-"
    try:
        fv = float(v)
        if fv.is_integer():
            return str(int(fv))
        return str(_round(fv))
    except Exception:
        return "-"

def _default_封測標的() -> Dict[str, str]:
    return {
        "2449": "京元電子",
        "3711": "日月光投控",
        "6239": "力成",
        "8150": "南茂",
        "6271": "同欣電",
    }

def _default_封測分類() -> List[str]:
    return ["2449", "3711", "6239", "8150", "6271"]

def _股票池() -> dict:
    pool = dict(_cfg("報表標的", {}))
    # 確保基本封測股能自動補齊至股票池
    for symbol, name in _default_封測標的().items():
        pool.setdefault(symbol, name)
    return pool

def _分類設定() -> dict:
    cats = dict(_cfg("分類設定", {}))
    defaults = _default_封測分類()
    current = list(cats.get("封測", []))
    for symbol in defaults:
        if symbol not in current:
            current.append(symbol)
    cats["封測"] = current
    return cats

def _unique_keep_order(seq):
    out = []
    for x in seq:
        if x is None:
            continue
        if x not in out:
            out.append(x)
    return out

def _row_cats(symbol: str) -> str:
    matched = []
    for cat, symbols in _分類設定().items():
        if symbol in symbols:
            matched.append(cat)
    if not matched:
        matched = ["其他"]
    return ",".join(matched)


def _resample_30(df5: pd.DataFrame) -> pd.DataFrame:
    """將5分K合成為真實 30 分 K（避開午休假K）"""
    morning = df5.between_time("09:00", "11:30").copy()
    afternoon = df5.between_time("13:00", "13:30").copy()
    df = pd.concat([morning, afternoon]).sort_index()

    return df.resample("30min", closed="left", label="left").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()

def _resample_day(df5: pd.DataFrame) -> pd.DataFrame:
    """將5分K合成為日K"""
    return df5.resample("D", closed="left", label="left").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()

# =========================================================
# 技術指標與趨勢判讀 (全向量化一次計算，避開重複計算)
# =========================================================

def _add_ma(df: pd.DataFrame) -> pd.DataFrame:
    """在載入時一次性計算所有移動平均線與量能均線"""
    df = df.copy()
    df["MA5"] = df["close"].rolling(5, min_periods=1).mean()
    df["MA10"] = df["close"].rolling(10, min_periods=1).mean()
    df["MA20"] = df["close"].rolling(20, min_periods=1).mean()
    df["MA60"] = df["close"].rolling(60, min_periods=1).mean()
    df["VMA10"] = df["volume"].rolling(10, min_periods=1).mean()
    return df

def _slope(series: pd.Series, lookback: int = 3) -> float:
    s = series.dropna()
    if len(s) <= lookback:
        return 0.0
    return float(s.iloc[-1] - s.iloc[-1 - lookback])

def _方向(df: pd.DataFrame) -> str:
    """判斷趨勢多空方向 (需預先跑過 _add_ma)"""
    if len(df) < 3:
        return "空"

    last = df.iloc[-1]
    prev = df.iloc[-2]

    close = float(last["close"])
    ma20_now = float(last["MA20"])
    ma20_prev = float(prev["MA20"])

    if close >= ma20_now and ma20_now > ma20_prev:
        return "多"
    return "空"

def _趨勢強度(df: pd.DataFrame) -> str:
    """評估多空趨勢強度 (需預先跑過 _add_ma)"""
    if len(df) < 3:
        return "盤整"

    last = df.iloc[-1]
    close = float(last["close"])
    ma5 = float(last["MA5"])
    ma10 = float(last["MA10"])
    ma20 = float(last["MA20"])
    ma60 = float(last["MA60"])

    score = 0
    if close > ma5:
        score += 1
    if close > ma10:
        score += 1
    if close > ma20:
        score += 1
    if close > ma60:
        score += 1
    if ma5 > ma10:
        score += 1
    if ma10 > ma20:
        score += 1
    if ma20 > ma60:
        score += 1
    if _slope(df["MA20"], 3) > 0:
        score += 1

    if score >= 6:
        return "強多"
    if score >= 4:
        return "偏多"
    if score <= 1:
        return "強空"
    if score <= 3:
        return "偏空"
    return "盤整"

# =========================================================
# 撐壓結構與關鍵價判讀
# =========================================================

def _前高前低(df_day: pd.DataFrame) -> Tuple[Optional[float], Optional[float]]:
    n = int(_cfg("前高前低天數", 3))
    recent = df_day.iloc[-min(n, len(df_day)):]
    return _round(recent["close"].max()), _round(recent["close"].min())

def _平台(df30: pd.DataFrame) -> Tuple[Optional[float], Optional[float]]:
    n = int(_cfg("平台回看根數", 10))
    recent = df30.iloc[-min(n, len(df30)):]
    return _round(recent["close"].max()), _round(recent["close"].min())

def _ma(df_day: pd.DataFrame) -> Tuple[Optional[float], Optional[float]]:
    """獲取 MA 關鍵價 (需預先跑過 _add_ma)"""
    return _round(df_day.iloc[-1]["MA20"]), _round(df_day.iloc[-1]["MA60"])

def _高低結構(df: pd.DataFrame, lookback: int = 8) -> str:
    if len(df) < 4:
        return "資料不足"

    recent = df.iloc[-min(lookback, len(df)):]
    highs = recent["high"].tolist()
    lows = recent["low"].tolist()

    if len(highs) >= 3 and len(lows) >= 3:
        high_up = highs[-1] > highs[-2] > highs[-3]
        low_up = lows[-1] > lows[-2] > lows[-3]
        high_down = highs[-1] < highs[-2] < highs[-3]
        low_down = lows[-1] < lows[-2] < lows[-3]

        if high_up and low_up:
            return "高低走強"
        if high_down and low_down:
            return "高低走弱"

    if highs[-1] >= max(highs[:-1]):
        return "高點墊高"
    if lows[-1] <= min(lows[:-1]):
        return "低點墊低"
    return "區間整理"

def _收斂程度(df: pd.DataFrame, lookback: int = 12) -> float:
    if len(df) < 4:
        return 1.0
    recent = df.iloc[-min(lookback, len(df)):]
    rng = float(recent["high"].max() - recent["low"].min())
    close = float(recent["close"].iloc[-1])
    if close <= 0:
        return 1.0
    return max(0.0, min(1.0, rng / (close * 0.12)))

def _大量K_high_low(df5: pd.DataFrame, 方向: str) -> Tuple[Optional[float], Optional[float]]:
    """依據趨勢方向篩選最有關鍵意義的大量K線之高低點"""
    if len(df5) < 15:
        return None, None

    window_n = int(_cfg("大量區回看根數", 15))
    vma_n = int(_cfg("大量K量能均線根數", 10))
    mul = float(_cfg("大量K量能倍率", 1.4))
    min_body = float(_cfg("大量K實體最小比例", 0.006))

    window = df5.iloc[-min(window_n, len(df5)):]
    if len(window) < vma_n + 1:
        return None, None

    best_high = None
    best_low = None
    best_score = -1.0

    for i in range(vma_n, len(window)):
        row = window.iloc[i]
        vma = float(window["volume"].iloc[i - vma_n:i].mean())
        if vma <= 0:
            continue

        o = float(row["open"])
        c = float(row["close"])
        v = float(row["volume"])

        if o <= 0 or c <= 0 or v < vma * mul:
            continue

        if 方向 == "多" and c <= o:
            continue
        if 方向 == "空" and c >= o:
            continue

        body = abs(c - o) / o
        if body < min_body:
            continue

        score = (v / vma) + body
        if score > best_score:
            best_score = score
            best_high = float(row["high"])
            best_low = float(row["low"])

    return _round(best_high), _round(best_low)


def _level_price(x) -> float:
    if isinstance(x, (tuple, list)) and len(x) >= 1:
        return float(x[0])
    if isinstance(x, dict) and "price" in x:
        return float(x["price"])
    return float(x)

def _level_source(x) -> str:
    if isinstance(x, (tuple, list)) and len(x) >= 2:
        return str(x[1])
    if isinstance(x, dict) and "source" in x:
        return str(x["source"])
    return ""

def _價格列表(現價: float, 前高, 大量high, 平台高, 前低, 大量low, 平台低, 日ma20, 日ma60):
    """回傳壓力 / 支撐：[(price, source), ...]"""
    壓力候選 = []
    支撐候選 = []

    raw_pressure = [
        (前高, "前高"),
        (大量high, "大量K"),
        (平台高, "30M平台"),
        (日ma60, "MA60"),
    ]
    raw_support = [
        (前低, "前低"),
        (大量low, "大量K"),
        (平台低, "30M平台"),
        (日ma20, "MA20"),
    ]

    for price, source in raw_pressure:
        if price is None:
            continue
        try:
            p = float(price)
        except Exception:
            continue
        if p > 現價:
            壓力候選.append((round(p, 2), source))

    for price, source in raw_support:
        if price is None:
            continue
        try:
            p = float(price)
        except Exception:
            continue
        if p < 現價:
            支撐候選.append((round(p, 2), source))

    if len(壓力候選) == 0:
        壓力候選.append((round(float(現價) * 1.03, 2), "保守壓力"))
    if len(支撐候選) == 0:
        支撐候選.append((round(float(現價) * 0.97, 2), "保守支撐"))

    # 去重 + 排序
    壓力候選 = sorted(list({tuple(x) for x in 壓力候選}), key=lambda x: x[0])
    支撐候選 = sorted(list({tuple(x) for x in 支撐候選}), key=lambda x: x[0], reverse=True)

    return 壓力候選, 支撐候選

def _壓力支撐文字(壓力_list, 支撐_list):
    def _fmt(level):
        return f"{_price(level[0])}({level[1]})" if level else "-"

    壓力 = " / ".join(_fmt(x) for x in 壓力_list[:2]) if 壓力_list else "-"
    支撐 = " / ".join(_fmt(x) for x in 支撐_list[:2]) if 支撐_list else "-"
    return 壓力, 支撐

def _風報比(現價: float, 日方向: str, 壓力, 支撐) -> str:
    if not 壓力 or not 支撐:
        return "-"
    壓力價 = _level_price(壓力[0])
    支撐價 = _level_price(支撐[0])

    if 日方向 == "多":
        reward = 壓力價 - 現價
        risk = 現價 - 支撐價
    else:
        reward = 現價 - 支撐價
        risk = 壓力價 - 現價

    if risk <= 0:
        return "-"
    return f"{(reward / risk):.2f}"

def _量價(df5: pd.DataFrame, df30: pd.DataFrame) -> str:
    """判斷短線放量/量增/量縮狀態 (需預先跑過 _add_ma)"""
    v1 = float(df5.iloc[-1]["volume"])
    v1m = float(df5.iloc[-1]["VMA10"])
    v2 = float(df30.iloc[-1]["volume"])
    v2m = float(df30.iloc[-1]["VMA10"])

    ratio = 1.0
    if v1m > 0:
        ratio = max(ratio, v1 / v1m)
    if v2m > 0:
        ratio = max(ratio, v2 / v2m)

    if ratio >= 1.4:
        return "放量"
    if ratio >= 1.2:
        return "量增"
    return "量縮"

def _節奏(df: pd.DataFrame) -> str:
    """判斷極短線移動平均線節奏 (需預先跑過 _add_ma)"""
    if len(df) < 3:
        return "資料不足"

    last = df.iloc[-1]
    close = float(last["close"])
    ma5 = float(last["MA5"])
    ma10 = float(last["MA10"])
    ma20 = float(last["MA20"])

    if close > ma5 > ma10 > ma20:
        return "沿短均線攻擊"
    if close < ma5 < ma10 < ma20:
        return "沿短均線下殺"
    if close >= ma20 and _slope(df["MA20"], 3) > 0:
        return "均線上彎"
    if close <= ma20 and _slope(df["MA20"], 3) < 0:
        return "均線下彎"
    return "橫向整理"

def _市場位階(df_day: pd.DataFrame) -> str:
    """判斷個股相對於MA20、MA60的位階狀態 (需預先跑過 _add_ma)"""
    if len(df_day) < 2:
        return "資料不足"

    last = df_day.iloc[-1]
    close = float(last["close"])
    ma20 = float(last["MA20"])
    ma60 = float(last["MA60"])

    dist20 = (close - ma20) / close if close else 0
    dist60 = (close - ma60) / close if close else 0

    if dist20 > 0.12 or dist60 > 0.18:
        return "高檔延伸"
    if dist20 < -0.12 or dist60 < -0.18:
        return "低檔乖離"
    if abs(dist20) <= 0.03 and abs(dist60) <= 0.05:
        return "均線附近"
    return "中繼位階"

def _主力痕跡(df30: pd.DataFrame) -> str:
    """偵測30分K主力異動籌碼痕跡 (需預先跑過 _add_ma)"""
    if len(df30) < 4:
        return "資料不足"

    recent = df30.iloc[-min(6, len(df30)):]
    down_bars = sum(1 for _, r in recent.iterrows() if float(r["close"]) < float(r["open"]))
    up_bars = sum(1 for _, r in recent.iterrows() if float(r["close"]) > float(r["open"]))
    vma10 = float(recent["VMA10"].iloc[-1])
    vol_ratio = float(recent["volume"].iloc[-1]) / vma10 if vma10 > 0 else 1.0
    body = abs(float(recent["close"].iloc[-1]) - float(recent["open"].iloc[-1])) / max(1e-9, float(recent["open"].iloc[-1]))

    if vol_ratio >= 1.6 and body >= 0.015 and down_bars >= 4:
        return "爆量壓回"
    if vol_ratio >= 1.6 and body >= 0.015 and up_bars >= 4:
        return "爆量發動"
    if down_bars >= 4 and _slope(recent["MA20"], 3) < 0:
        return "高檔轉弱"
    if up_bars >= 4 and _slope(recent["MA20"], 3) > 0:
        return "短線轉強"
    if _收斂程度(df30) < 0.55:
        return "量縮壓縮"
    return "一般整理"

def _結構狀態(
    日方向: str,
    三十分方向: str,
    日構: str,
    三十分構: str,
    量價: str,
    位階: str,
    主力痕跡: str,
) -> str:
    if 日方向 == "多" and 三十分方向 == "多":
        if "爆量壓回" in 主力痕跡 or "高檔轉弱" in 主力痕跡:
            return "高檔強多轉弱"
        if 位階 == "高檔延伸":
            return "末升段"
        if "量縮壓縮" in 主力痕跡:
            return "多方壓縮"
        return "主升多"

    if 日方向 == "空" and 三十分方向 == "空":
        if 位階 == "低檔乖離":
            return "跌深反彈後轉弱"
        if "量縮壓縮" in 主力痕跡:
            return "空方壓縮"
        return "主跌空"

    if 日方向 == "多" and 三十分方向 == "空":
        if "高檔轉弱" in 主力痕跡 or "爆量壓回" in 主力痕跡:
            return "高檔出貨"
        return "多方回檔"

    if 日方向 == "空" and 三十分方向 == "多":
        if 位階 == "低檔乖離":
            return "跌深反彈"
        return "空方反彈"

    if "量縮壓縮" in 主力痕跡:
        return "橫盤壓縮"
    return "區間整理"


def _相對量(df5: pd.DataFrame, df30: pd.DataFrame) -> float:
    """以 5 分與 30 分相對量衡量異常量能，回傳倍率"""
    ratios = []
    for df in (df5, df30):
        if len(df) < 2:
            continue
        last = df.iloc[-1]
        v = float(last["volume"])
        vma = float(last["VMA10"]) if "VMA10" in df.columns else 0.0
        if vma > 0:
            ratios.append(v / vma)
    if not ratios:
        return 1.0
    return float(max(ratios))

def _市場狀態分類(結構結果: str, 位階: str, 量價: str, 主力痕跡: str, 日方向: str, 三十分方向: str) -> str:
    """把結構結果再細分成更有盤感的狀態"""
    if 結構結果 == "主升多":
        if "爆量發動" in 主力痕跡 or 量價 == "放量":
            return "初升"
        if 位階 == "高檔延伸":
            return "主升末段"
        return "主升"
    if 結構結果 == "多方壓縮":
        return "壓縮等待"
    if 結構結果 == "末升段":
        return "末升"
    if 結構結果 == "多方回檔":
        return "洗盤"
    if 結構結果 == "高檔強多轉弱":
        return "高檔轉弱"
    if 結構結果 == "高檔出貨":
        return "高檔出貨"
    if 結構結果 == "空方反彈":
        return "反彈"
    if 結構結果 == "空方壓縮":
        return "空方壓縮"
    if 結構結果 == "主跌空":
        return "主跌"
    if 結構結果 == "跌深反彈後轉弱":
        return "反彈失敗"
    if 結構結果 == "橫盤壓縮":
        return "整理"
    return "整理"

def _市場溫度(市場狀態: str, 相對量: float, 主力痕跡: str, 風報比: Optional[float] = None) -> Tuple[str, str]:
    """回傳溫度文字與 CSS 類別

    規則重點：
    1) 只有「結構強」不夠，RR 太差時不應顯示成攻擊盤。
    2) 量能與主力痕跡決定溫度，但風報比會做最後的降溫。
    """
    hot_states = {"初升", "主升", "壓縮等待"}
    warm_states = {"洗盤", "反彈"}
    neutral_states = {"整理", "空方壓縮"}
    orange_states = {"末升", "主升末段", "高檔轉弱"}
    danger_states = {"高檔出貨", "主跌", "反彈失敗"}

    rr = None
    if 風報比 not in (None, "", "-"):
        try:
            rr = float(風報比)
        except Exception:
            rr = None

    # 高風險：不管 RR，多半都不應再用攻擊語氣
    if 市場狀態 in danger_states or "爆量壓回" in 主力痕跡:
        return "⚠️ 高風險盤", "temp-danger"

    # 末升 / 高檔轉弱：若 RR 不足，降級為觀察盤
    if 市場狀態 in orange_states:
        if rr is not None and rr >= 1.2:
            return "🟠 偏熱盤", "temp-warm"
        return "🌤 觀察盤", "temp-neutral"

    # 主升 / 初升：只有在量能與 RR 都合理時才叫攻擊盤
    if 市場狀態 in hot_states and 相對量 >= 1.2:
        if rr is not None and rr < 1.0:
            return "🌤 觀察盤", "temp-neutral"
        if rr is None or rr >= 1.2:
            return "🔥 攻擊盤", "temp-hot"
        return "🌤 觀察盤", "temp-neutral"

    if 市場狀態 in warm_states:
        return "🌤 觀察盤", "temp-neutral"

    if 市場狀態 in neutral_states or 相對量 < 0.9:
        return "🌫 等待盤", "temp-cool"

    return "🌤 觀察盤", "temp-neutral"

def _多週期判讀文字(
    日方向: str,
    日強弱: str,
    三十分方向: str,
    三十分強弱: str,
    市場位階: str,
    量價: str,
    相對量: float,
    市場溫度: str,
) -> str:
    """去蕪存菁：只保留第一眼決策必要資訊。"""
    rv = f"{相對量:.2f}x"
    return (
        f"<div class='layer'><span class='layer-label'>日K</span><span class='layer-text'>{日方向}｜{日強弱}</span></div>"
        f"<div class='layer'><span class='layer-label'>30M</span><span class='layer-text'>{三十分方向}｜{三十分強弱}</span></div>"
        f"<div class='layer'><span class='layer-label'>位階</span><span class='layer-text'>{市場位階}</span></div>"
        f"<div class='layer'><span class='layer-label'>節奏</span><span class='layer-text'>{量價}｜RV {rv}</span></div>"
        f"<div class='layer'><span class='layer-label'>情緒</span><span class='layer-text'>{市場溫度}</span></div>"
    )

def _情境提醒(
    市場狀態: str,
    市場溫度: str,
    主力痕跡: str,
    相對量: float,
    結構結果: str,
    日方向: str,
    三十分方向: str,
    風報比: str,
) -> str:
    """更像交易員口吻的市場敘事，但不重複多週期判讀"""
    rr = None
    if 風報比 not in (None, "", "-"):
        try:
            rr = float(風報比)
        except Exception:
            rr = None

    base = {
        "初升": "資金開始集中，整理後仍有續攻味道。",
        "主升": "買盤仍在承接，但追價空間已開始變珍貴。",
        "主升末段": "趨勢還在，但攻擊節奏已有鈍化。",
        "末升": "漲勢仍在，但尾段容易出現震盪。",
        "壓縮等待": "價格先收斂，市場在等方向表態。",
        "洗盤": "短線壓回但承接尚在，較像換手整理。",
        "高檔轉弱": "上方開始有調節味道，追價效率變差。",
        "高檔出貨": "上方賣壓已明顯，爆量後若無法延續，容易轉弱。",
        "反彈": "屬於修復性反彈，重點看壓力能否消化。",
        "反彈失敗": "反彈已被破壞，若再跌破關鍵位，容易轉弱。",
        "空方壓縮": "空方整理中，若支撐守不住，往下可能加速。",
        "主跌": "空方主導，反彈先看壓力。",
        "整理": "方向尚未明顯，市場仍在等表態。",
    }.get(市場狀態, "目前多空仍在拉扯，先看關鍵位是否被確認。")

    if 市場溫度 == "🔥 攻擊盤":
        tail = "盤面偏積極，續強機率較高。"
    elif 市場溫度 == "🟠 偏熱盤":
        tail = "趨勢仍在，但短線已開始偏熱。"
    elif 市場溫度 == "🌤 觀察盤":
        tail = "目前較適合觀察確認。"
    elif 市場溫度 == "🌫 等待盤":
        tail = "市場還在收斂，等確認後再看。"
    else:
        tail = "風險開始升高，追價要更保守。"

    # 把 RR 的語氣拉回交易角度，避免「結構強但空間小」仍講成很熱
    if rr is not None:
        if rr < 0.8:
            tail += " 空間偏緊，較像要先等確認的盤。"
        elif rr < 1.5:
            tail += " 追價報酬效率普通，宜等更明確的突破。"
        elif rr >= 2.5:
            tail += " 空間條件不差，若結構延續，仍有操作彈性。"

    if "爆量壓回" in 主力痕跡:
        tail += " 近端有調節痕跡，短線要把風險放前面。"
    elif "爆量發動" in 主力痕跡:
        tail += " 量能有攻擊性，代表仍有主導資金存在。"
    elif "量縮壓縮" in 主力痕跡:
        tail += " 量縮壓縮味道明顯，等待方向確認即可。"

    if 市場狀態 in ("初升", "主升") and rr is not None and rr < 1.0:
        tail += " 雖然結構偏強，但這個位置先不要把它當成好追價點。"

    return f"{base}<br>{tail}"

def _AI策略劇本(
    市場狀態: str,
    市場溫度: str,
    壓力文字: str,
    支撐文字: str,
    風報比: str,
    主力痕跡: str,
) -> str:
    """偏情境式的隔日劇本，不重複多週期資訊"""
    if 壓力文字 == "-":
        壓力文字 = "上方暫無明確壓力"
    if 支撐文字 == "-":
        支撐文字 = "下方暫無明確支撐"

    rr = None
    if 風報比 not in (None, "", "-"):
        try:
            rr = float(風報比)
        except Exception:
            rr = None

    if 市場狀態 in ("初升", "主升", "壓縮等待"):
        核心 = "🟢 續強劇本"
        做法 = f"若開盤守住 {支撐文字}，可先觀察續攻。"
        防守 = f"若跌回 {支撐文字} 下方且無法快速收復，節奏會先轉弱。"
        目標 = f"上方先看 {壓力文字}；若能帶量穿越，代表攻擊延續。"
        if rr is None:
            rr_text = "空間待確認"
        elif rr < 0.8:
            rr_text = "空間偏緊"
        elif rr < 1.5:
            rr_text = "空間普通"
        else:
            rr_text = "空間仍充足"
        總結 = f"目前屬於偏積極的結構，{rr_text}，以守支撐觀察續攻為主。"
    elif 市場狀態 in ("末升", "主升末段", "高檔轉弱"):
        核心 = "🟠 高檔劇本"
        做法 = f"若開高但量能跟不上，先視為高檔震盪，不急著追。"
        防守 = f"{支撐文字} 一旦失守，容易進入較明顯的修正。"
        目標 = f"{壓力文字} 若無法快速站穩，上方就先當成壓力區。"
        總結 = "趨勢還沒壞，但短線已偏熱，追價的報酬效率開始下降。"
    elif 市場狀態 in ("洗盤", "反彈"):
        核心 = "🌤 觀察劇本"
        做法 = f"先看 {支撐文字} 是否守穩，若快速收回，較像整理或洗盤。"
        防守 = f"若連 {支撐文字} 都守不住，反彈容易失真。"
        目標 = f"上方先看 {壓力文字} 是否被消化。"
        總結 = "這一段重點不在追價，而在看賣壓有沒有真正降下來。"
    elif 市場狀態 in ("高檔出貨", "主跌", "反彈失敗"):
        核心 = "⚠️ 風險劇本"
        做法 = f"反彈到 {壓力文字} 若不過，容易仍在弱勢節奏中。"
        防守 = f"若 {支撐文字} 失守，短線風險會再升高。"
        目標 = f"上方壓力暫時當成調節區，不宜過度樂觀。"
        總結 = "目前以風險控管為先，除非重新站回關鍵位，否則不宜想得太快。"
    else:
        核心 = "🟦 等待劇本"
        做法 = f"先看 {壓力文字} 與 {支撐文字} 哪一邊先被確認。"
        防守 = "方向還沒選邊，先等更明確的表態。"
        目標 = "在突破或跌破前，先把它當整理盤。"
        總結 = "這種盤以等待為主，太早預設方向通常容易被洗。"

    if 市場溫度 == "⚠️ 高風險盤":
        總結 += " 盤面風險升高，先以保守應對為主。"
    elif 市場溫度 == "🔥 攻擊盤" and (rr is not None and rr >= 2):
        總結 += " 交易條件相對完整，若續強則偏向順勢。"
    elif rr is not None and rr < 1.0:
        總結 += " 空間不算漂亮，追價前要先想清楚風險。"

    if "爆量壓回" in 主力痕跡:
        總結 += " 近端有調節痕跡，短線要把風險放前面。"
    elif "爆量發動" in 主力痕跡:
        總結 += " 量能有攻擊性，代表仍有主導資金存在。"
    elif "量縮壓縮" in 主力痕跡:
        總結 += " 量縮壓縮味道明顯，等待方向確認即可。"

    return _render_script(核心, 做法, 防守, 目標, 總結)

def _AI分數(
    結構結果: str,
    市場狀態: str,
    市場溫度: str,
    日方向: str,
    三十分方向: str,
    量價: str,
    市場位階: str,
    風報比: str,
    主力痕跡: str,
    相對量: float,
) -> int:
    score = 45

    結構分數 = {
        "初升": 30,
        "主升": 28,
        "壓縮等待": 22,
        "洗盤": 18,
        "反彈": 14,
        "末升": 12,
        "主升末段": 10,
        "高檔轉弱": 8,
        "高檔出貨": 4,
        "反彈失敗": 6,
        "空方壓縮": 10,
        "主跌": 2,
        "整理": 10,
    }
    score += 結構分數.get(市場狀態, 10)

    if 日方向 == "多":
        score += 7
    else:
        score -= 4

    if 三十分方向 == "多":
        score += 6
    else:
        score -= 2

    量價分數 = {"放量": 10, "量增": 6, "量縮": 2}
    score += 量價分數.get(量價, 0)

    位階分數 = {
        "高檔延伸": -10,
        "低檔乖離": 7,
        "均線附近": 4,
        "中繼位階": 2,
        "資料不足": 0,
    }
    score += 位階分數.get(市場位階, 0)

    # 風報比：避免出現「分數很高但 RR 很差」的情況
    rr = None
    if isinstance(風報比, str) and 風報比 not in ("-", ""):
        try:
            rr = float(風報比)
            if rr >= 4:
                score += 10
            elif rr >= 3:
                score += 8
            elif rr >= 2:
                score += 6
            elif rr >= 1.5:
                score += 4
            elif rr >= 1:
                score += 2
            elif rr >= 0.8:
                score -= 10
            else:
                score -= 22
        except Exception:
            rr = None

    # 市場溫度與 RR 的交叉限制：不要出現「RR 很差卻還是攻擊盤 100 分」
    if 市場溫度 == "🔥 攻擊盤":
        score += 6
    elif 市場溫度 == "🟠 偏熱盤":
        score += 2
    elif 市場溫度 == "🌤 觀察盤":
        score += 0
    elif 市場溫度 == "🌫 等待盤":
        score -= 4
    elif 市場溫度 == "⚠️ 高風險盤":
        score -= 10

    if rr is not None and rr < 1.0:
        score = min(score, 78)
    elif rr is not None and rr < 1.5:
        score = min(score, 88)

    if 相對量 >= 1.8:
        score += 8
    elif 相對量 >= 1.3:
        score += 5
    elif 相對量 <= 0.7:
        score -= 2

    if "爆量發動" in 主力痕跡:
        score += 8
    elif "短線轉強" in 主力痕跡:
        score += 5
    elif "量縮壓縮" in 主力痕跡:
        score += 4
    elif "高檔轉弱" in 主力痕跡:
        score -= 6
    elif "爆量壓回" in 主力痕跡:
        score -= 8

    return max(0, min(100, int(round(score))))

def _族群等級(avg_score: float) -> Tuple[str, str]:
    if avg_score >= 80:
        return "主流", "sector-hot"
    if avg_score >= 65:
        return "偏強", "sector-warm"
    if avg_score >= 50:
        return "整理", "sector-neutral"
    return "偏弱", "sector-cool"

def _族群強弱表(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=["族群", "平均分數", "狀態", "代表股", "成員數"])

    rows = []
    cats = _分類設定()
    for cat in cats.keys():
        subset = df[df["_cats"].fillna("").apply(lambda s: cat in [x.strip() for x in str(s).split(",") if x.strip()])].copy()
        if len(subset) == 0:
            continue
        avg_score = float(subset["AI分數"].mean())
        status, _ = _族群等級(avg_score)
        top_row = subset.sort_values(by=["AI分數", "_sort", "股票"], ascending=[False, True, True]).iloc[0]
        rows.append({
            "族群": cat,
            "平均分數": round(avg_score, 1),
            "狀態": status,
            "代表股": top_row["股票"],
            "成員數": int(len(subset)),
        })

    out = pd.DataFrame(rows)
    if len(out) == 0:
        return out
    return out.sort_values(by=["平均分數", "成員數", "族群"], ascending=[False, False, True]).reset_index(drop=True)

def _Dashboard摘要(df: pd.DataFrame, sector_df: pd.DataFrame) -> dict:
    if df is None or len(df) == 0:
        return {
            "市場溫度": "無資料",
            "平均分數": 0,
            "強勢比": "0%",
            "最強族群": "-",
            "攻擊盤數": 0,
            "高風險數": 0,
            "最強股票": "-",
        }

    avg_score = float(df["AI分數"].mean()) if "AI分數" in df.columns else 0.0

    strong_ratio = f"{(len(df[df['AI分數'] >= 70]) / len(df) * 100):.0f}%" if "AI分數" in df.columns else "0%"

    top_stock = df.sort_values(["AI分數", "_sort", "股票"], ascending=[False, True, True]).iloc[0]["股票"] if len(df) > 0 else "-"

    if sector_df is not None and len(sector_df) > 0:
        top_sector = sector_df.sort_values(["平均分數", "成員數"], ascending=[False, False]).iloc[0]["族群"]
    else:
        top_sector = "-"

    hot_cnt = int((df["AI分數"] >= 80).sum()) if "AI分數" in df.columns else 0
    risk_cnt = int((df["AI分數"] < 50).sum()) if "AI分數" in df.columns else 0

    if avg_score >= 75:
        mood = "🔥 偏多攻擊"
    elif avg_score >= 65:
        mood = "🌤 中性偏多"
    elif avg_score >= 50:
        mood = "🌫 觀望整理"
    else:
        mood = "⚠️ 偏弱保守"

    return {
        "市場溫度": mood,
        "平均分數": round(avg_score, 1),
        "強勢比": strong_ratio,
        "最強族群": top_sector,
        "攻擊盤數": hot_cnt,
        "高風險數": risk_cnt,
        "最強股票": top_stock,
    }

def _render_script(核心: str, 做法: str, 防守: str, 空間: str, 總結: str) -> str:
    return f"""
<div class="script-wrap">
  <div class="script-core">{核心}</div>
  <div class="script-line">
    <span class="line-label line-green">做法</span>
    <span class="line-text">{做法}</span>
  </div>
  <div class="script-line">
    <span class="line-label line-red">防守</span>
    <span class="line-text">{防守}</span>
  </div>
  <div class="script-line">
    <span class="line-label line-blue">目標</span>
    <span class="line-text">{空間}</span>
  </div>
  <div class="script-row">
    <span class="tag tag-green">明日若直接跳空過大，先不追價。</span>
  </div>
  <div class="script-summary">{總結}</div>
</div>
"""


def _AI劇本(
    日方向: str,
    三十分方向: str,
    結構結果: str,
    現價: float,
    日ma60: Optional[float],
    壓力文字: str,
    支撐文字: str,
    風報比: str,
    市場位階: str,
    量價: str,
    節奏30: str,
    高低結構30: str,
    主力痕跡: str,
) -> str:
    """保留舊函式名，但內容改為更有溫度的情境卡"""
    市場狀態 = _市場狀態分類(結構結果, 市場位階, 量價, 主力痕跡, 日方向, 三十分方向)
    市場溫度, _ = _市場溫度(市場狀態, 1.0, 主力痕跡, 風報比=None)
    return _AI策略劇本(市場狀態, 市場溫度, 壓力文字, 支撐文字, 風報比, 主力痕跡)


def _analyze(symbol: str, loader: ShioajiSafeLoader):
    name = _股票池().get(symbol, symbol)

    start = (datetime.now() - timedelta(days=int(_cfg("回看天數", 90)))).strftime("%Y-%m-%d")
    end = datetime.now().strftime("%Y-%m-%d")

    # 1. 安全抓取 5分K（先讀 cache，不足才補）
    df5, _ = CACHE.load_or_update(
        symbol=symbol,
        loader=loader,
        start=start,
        end=end,
        force_refresh_today=False,
    )

    if df5 is None or len(df5) < 30:
        return None

    # 過濾正規交易時間
    df5 = df5.between_time("09:00", "13:30").copy()
    if len(df5) < 30:
        return None

    # 2. 合成多週期資料
    df30 = _resample_30(df5)
    df_day = _resample_day(df5)

    if len(df30) < 5 or len(df_day) < 3:
        return None

    # 3. 預先計算 MA 線
    df5 = _add_ma(df5)
    df30 = _add_ma(df30)
    df_day = _add_ma(df_day)

    # 4. 指標與趨勢訊號取得
    日方向 = _方向(df_day)
    三十分方向 = _方向(df30)
    日強弱 = _趨勢強度(df_day)
    三十分強弱 = _趨勢強度(df30)

    前高, 前低 = _前高前低(df_day)
    平台高, 平台低 = _平台(df30)
    日ma20, 日ma60 = _ma(df_day)

    現價 = float(df5.iloc[-1]["close"])
    大量high, 大量low = _大量K_high_low(df5, 日方向)
    壓力_list, 支撐_list = _價格列表(現價, 前高, 大量high, 平台高, 前低, 大量low, 平台低, 日ma20, 日ma60)
    壓力文字, 支撐文字 = _壓力支撐文字(壓力_list, 支撐_list)
    rr = _風報比(現價, 日方向, 壓力_list, 支撐_list)

    量價 = _量價(df5, df30)
    相對量 = _相對量(df5, df30)
    節奏30 = _節奏(df30)
    高低結構30 = _高低結構(df30)
    高低結構日 = _高低結構(df_day)
    市場位階 = _市場位階(df_day)
    主力痕跡 = _主力痕跡(df30)

    結構結果 = _結構狀態(日方向, 三十分方向, 高低結構日, 高低結構30, 量價, 市場位階, 主力痕跡)
    市場狀態 = _市場狀態分類(結構結果, 市場位階, 量價, 主力痕跡, 日方向, 三十分方向)
    市場溫度, 溫度類別 = _市場溫度(市場狀態, 相對量, 主力痕跡, rr)

    ai_score = _AI分數(
        結構結果=結構結果,
        市場狀態=市場狀態,
        市場溫度=市場溫度,
        日方向=日方向,
        三十分方向=三十分方向,
        量價=量價,
        市場位階=市場位階,
        風報比=rr,
        主力痕跡=主力痕跡,
        相對量=相對量,
    )

    族群 = _row_cats(symbol)
    # 純盤後版：不使用 FeatureEngine，維持純函式判讀

    if 結構結果 in ("主升多", "末升段"):
        try:
            rr_v = float(rr)
        except Exception:
            rr_v = None
        if rr_v is not None and rr_v < 0.8:
            交易狀態 = 結構結果 + "｜空間緊"
        else:
            交易狀態 = 結構結果 + ("｜等突破" if 壓力_list and _level_price(壓力_list[0]) > 現價 else "｜可續抱")
    elif 結構結果 in ("主跌空", "高檔出貨", "高檔強多轉弱"):
        交易狀態 = 結構結果 + "｜偏保守"
    elif 結構結果 in ("多方回檔", "空方反彈"):
        交易狀態 = 結構結果 + "｜看關鍵位"
    elif 結構結果 in ("橫盤壓縮", "多方壓縮", "空方壓縮"):
        交易狀態 = 結構結果 + "｜等方向"
    else:
        交易狀態 = 結構結果

    多週期判讀 = _多週期判讀文字(日方向, 日強弱, 三十分方向, 三十分強弱, 市場位階, 量價, 相對量, 市場溫度)
    情境提醒 = _情境提醒(市場狀態, 市場溫度, 主力痕跡, 相對量, 結構結果, 日方向, 三十分方向, rr)
    AI策略劇本 = _AI策略劇本(市場狀態, 市場溫度, 壓力文字, 支撐文字, rr, 主力痕跡)

    return {
        "股票": f"{symbol} {name}",
        "族群": 族群,
        "現價": _round(現價),
        "AI分數": ai_score,
        "交易狀態": 交易狀態,
        "市場溫度": 市場溫度,
        "市場溫度類別": 溫度類別,
        "市場狀態": 市場狀態,
        "多週期判讀": 多週期判讀,
        "情境提醒": 情境提醒,
        "AI策略劇本": AI策略劇本,
        "結構結果": 結構結果,
        "日K方向": f"{日方向}｜{日強弱}",
        "30分K方向": f"{三十分方向}｜{三十分強弱}",
        "量價判讀": f"{量價}｜RV {相對量:.2f}x｜{節奏30}",
        "_cats": 族群,
        "_sort": _結構排序(結構結果),
        "_df5": df5,
        "_df30": df30,
        "_df_day": df_day,
        "主力痕跡": 主力痕跡,
    }

def _結構排序(狀態: str) -> int:
    order = {
        "主升多": 0,
        "末升段": 1,
        "多方壓縮": 2,
        "多方回檔": 3,
        "橫盤壓縮": 4,
        "空方反彈": 5,
        "空方壓縮": 6,
        "高檔強多轉弱": 7,
        "高檔出貨": 8,
        "跌深反彈後轉弱": 9,
        "主跌空": 10,
    }
    return order.get(狀態, 50)

# =========================================================
# HTML 報表美化與自動推送機制
# =========================================================


def _html(df: pd.DataFrame, path: str) -> None:
    categories = _分類設定()

    buttons = '<button class="btn active" data-cat="全部">全部</button>\n'

    for cat in categories.keys():
        buttons += f'<button class="btn" data-cat="{cat}">{cat}</button>\n'


    sector_df = _族群強弱表(df)
    dashboard = _Dashboard摘要(df, sector_df)

    def _safe_html(v):
        return str(v).replace("\n", "<br>")


    cards = f"""
      <div class="dash-card"><div class="dash-k">市場情緒</div><div class="dash-v">{dashboard['市場溫度']}</div><div class="dash-s">平均分數 {dashboard['平均分數']}｜強勢比 {dashboard['強勢比']}</div></div>
      <div class="dash-card"><div class="dash-k">最強族群</div><div class="dash-v">{dashboard['最強族群']}</div><div class="dash-s">攻擊盤 {dashboard['攻擊盤數']}｜風險 {dashboard['高風險數']}</div></div>
      <div class="dash-card"><div class="dash-k">最強股票</div><div class="dash-v">{dashboard['最強股票']}</div><div class="dash-s">總檔數 {len(df)}</div></div>
      <div class="dash-card"><div class="dash-k">整體平均</div><div class="dash-v">{dashboard['平均分數']}</div><div class="dash-s">族群 {len(sector_df)} 個</div></div>
    """

    rows_html_list = []
    sort_cols = ["AI分數", "_sort", "股票"] if "AI分數" in df.columns else ["_sort", "股票"]
    for _, row in df.sort_values(sort_cols, ascending=[False, True, True]).iterrows():
        stock_html = (
            f"<div class='stock-main'>{_safe_html(row.get('股票', '-'))}</div>"
            f"<div class='stock-sub'>{_safe_html(row.get('族群', '-'))}｜AI {row.get('AI分數', '-')}｜現價 {_price(row.get('現價', None))}｜{_safe_html(row.get('市場溫度', '-'))}</div>"
        )

        cells = f"""
            <td class="stock-cell">{stock_html}</td>
            <td class="multi-cell">{_safe_html(row.get("多週期判讀", "-"))}</td>
            <td class="script-cell">{_safe_html(row.get("情境提醒", "-"))}</td>
            <td class="script">{_safe_html(row.get("AI策略劇本", "-"))}</td>
        """
        rows_html_list.append(f'<tr data-cats="{row.get("_cats","")}">{cells}</tr>')

    rows_html = "\n".join(rows_html_list)


    sector_rows_html_list = []
    if len(sector_df) > 0:
        for _, srow in sector_df.iterrows():
            status, badge_class = _族群等級(float(srow["平均分數"]))
            sector_rows_html_list.append(
                f'<tr>'
                f'<td>{srow["族群"]}</td>'
                f'<td><strong>{srow["平均分數"]}</strong></td>'
                f'<td><span class="sector-badge {badge_class}">{status}</span></td>'
                f'<td>{srow["代表股"]}</td>'
                f'<td>{srow["成員數"]}</td>'
                f'</tr>'
            )
    else:
        sector_rows_html_list.append('<tr><td colspan="5" style="text-align:center;color:#6b7280;">暫無族群資料</td></tr>')
    sector_rows_html = "\n".join(sector_rows_html_list)


    update_label = _now_str()

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI多週期盤後結構報表</title>
<style>
  :root {{
    --bg: #f5f7fb;
    --panel: rgba(255,255,255,0.96);
    --border: #e5e7eb;
    --text: #111827;
    --muted: #6b7280;
    --dark: #172033;
    --green: #0f766e;
    --green-soft: #ecfdf5;
    --red: #dc2626;
    --red-soft: #fef2f2;
    --blue: #2563eb;
    --blue-soft: #eff6ff;
    --gray-soft: #f3f4f6;
    --orange-soft: #fff7ed;
    --orange: #d97706;
  }}

  body {{
    font-family: "Segoe UI", "Microsoft JhengHei", Arial, sans-serif;
    background: var(--bg);
    margin:0;
    padding:20px;
    color: var(--text);
  }}

  .container {{ max-width: 2000px; margin: auto; }}
  .topbar {{ display:flex; justify-content:space-between; gap:16px; align-items:flex-start; margin-bottom:14px; flex-wrap:wrap; }}
  .brand h1 {{ margin:0 0 8px; font-size:28px; line-height:1.2; }}
  .sub {{ color: var(--muted); margin:0; font-size:13px; }}
  .update-card {{ display:flex; gap:12px; align-items:center; background: var(--panel); border:1px solid var(--border); border-radius:18px; padding:12px 16px; box-shadow:0 4px 18px rgba(15,23,42,.06); min-width: 300px; }}
  .update-icon {{ font-size:28px; line-height:1; }}
  .update-title {{ font-weight:800; font-size:15px; color: var(--dark); }}
  .update-sub {{ font-size:12px; color: var(--muted); margin-top:2px; }}
  .dash-grid {{ display:grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap:12px; margin: 10px 0 18px; }}
  .dash-card {{ background: var(--panel); border:1px solid var(--border); border-radius:18px; padding:14px 16px; box-shadow:0 8px 24px rgba(15,23,42,.08); }}
  .dash-k {{ font-size:12px; color: var(--muted); font-weight:800; margin-bottom:6px; }}
  .dash-v {{ font-size:20px; font-weight:900; color: var(--dark); margin-bottom:4px; }}
  .dash-s {{ font-size:12px; color: var(--muted); }}
  .section {{ margin-bottom:16px; background: var(--panel); border:1px solid var(--border); border-radius:18px; box-shadow:0 8px 24px rgba(15,23,42,.08); overflow:hidden; }}
  .section-head {{ padding:14px 16px; border-bottom:1px solid var(--border); display:flex; align-items:center; justify-content:space-between; gap:12px; background: linear-gradient(180deg, #ffffff, #f8fafc); }}
  .section-title {{ font-weight:900; font-size:18px; color: var(--dark); }}
  .section-sub {{ font-size:12px; color: var(--muted); }}
  .toolbar {{ margin-bottom:15px; display:flex; flex-wrap:wrap; gap:10px; align-items:center; }}
  .btn {{ padding:10px 16px; border:none; border-radius:999px; cursor:pointer; background:#e5e7eb; font-size:14px; font-weight:700; color:#1f2937; }}
  .btn.active {{ background:#2563eb; color:white; }}
  .search {{ margin-left:auto; min-width:220px; max-width:320px; width:100%; padding:10px 14px; border-radius:14px; border:1px solid #d7dee8; font-size:14px; background:white; color:#111827; outline:none; }}
  .search:focus {{ border-color:#2563eb; }}
  .table-scroll {{ width:100%; overflow-x:auto; overflow-y:hidden; -webkit-overflow-scrolling:touch; }}
  .sector-wrap {{ padding:16px; }}
  table {{ border-collapse:collapse; width:max-content; min-width:1200px; }}
  .sector-table {{ min-width:900px; width:max-content; }}
  .main-table {{ min-width:1320px; }}
  th {{ background: linear-gradient(180deg, #1f2937 0%, #0f172a 100%); color:white; padding:14px 12px; position:sticky; top:0; z-index:2; white-space:nowrap; font-size:15px; }}
  td {{ padding:14px 12px; border-bottom:1px solid #e5e7eb; vertical-align:top; background: rgba(255,255,255,0.9); }}
  tr:hover td {{ background:#f9fafb; }}
  .stock-cell {{ min-width:220px; max-width:260px; }}
  .stock-main {{ font-size:16px; font-weight:900; color: var(--dark); white-space:nowrap; }}
  .stock-sub {{ font-size:12px; color: var(--muted); font-weight:700; margin-top:3px; line-height:1.45; }}
  .multi-cell {{ min-width:260px; max-width:320px; white-space:normal; line-height:1.6; }}
  .script-cell {{ min-width:300px; max-width:360px; white-space:normal; line-height:1.65; color:#374151; font-size:13px; }}
  .script {{ min-width:340px; max-width:420px; white-space:normal; line-height:1.7; }}
  .temp-badge {{ display:inline-flex; align-items:center; padding:7px 10px; border-radius:999px; font-weight:800; font-size:13px; border:1px solid transparent; white-space:nowrap; }}
  .temp-hot {{ background:#dcfce7; color:#166534; border-color:#86efac; }}
  .temp-warm {{ background:#fff7ed; color:#9a3412; border-color:#fdba74; }}
  .temp-neutral {{ background:#eff6ff; color:#1d4ed8; border-color:#bfdbfe; }}
  .temp-cool {{ background:#f3f4f6; color:#4b5563; border-color:#d1d5db; }}
  .temp-danger {{ background:#fef2f2; color:#b91c1c; border-color:#fecaca; }}
  .layer {{ display:flex; gap:8px; align-items:center; margin:4px 0; line-height:1.45; }}
  .layer-label {{ display:inline-flex; align-items:center; justify-content:center; min-width:48px; padding:3px 8px; border-radius:999px; font-size:12px; font-weight:800; background:#eff6ff; color:#1d4ed8; flex:0 0 auto; }}
  .layer-text {{ color:#111827; font-size:13px; font-weight:600; }}
  .script-wrap {{ display:flex; flex-direction:column; gap:6px; background:#fff; border:1px solid var(--border); border-left:5px solid #22c55e; border-radius:14px; padding:10px 12px; }}
  .script-core {{ font-size:14px; font-weight:900; padding:3px 10px; border-radius:999px; background:#dcfce7; color:#14532d; width:fit-content; }}
  .script-line {{ display:flex; gap:8px; align-items:flex-start; padding:4px 8px; background:#f8fafc; border-radius:8px; font-size:13px; }}
  .line-label {{ padding:2px 8px; border-radius:6px; font-size:12px; font-weight:800; flex-shrink:0; }}
  .line-text {{ color:var(--dark); font-weight:600; flex:1 1 auto; line-height:1.55; }}
  .line-green {{ background:#ecfdf5; color:#047857; }}
  .line-red {{ background:var(--red-soft); color:var(--red); }}
  .line-blue {{ background:var(--blue-soft); color:var(--blue); }}
  .script-row {{ display:flex; align-items:center; }}
  .tag {{ display:inline-flex; align-items:center; padding:5px 9px; border-radius:999px; font-size:12px; font-weight:800; }}
  .tag-green {{ background:#ecfdf5; color:#047857; }}
  .script-summary {{ color:#166534; background:#f0fdf4; padding:6px 10px; border-radius:8px; font-size:13px; font-weight:700; margin-top:0; line-height:1.55; }}
  .sector-table th {{ position:static; font-size:14px; padding:12px 10px; }}
  .sector-table td {{ white-space:nowrap; font-size:14px; padding:12px 10px; background:white; }}
  .sector-badge {{ display:inline-flex; align-items:center; padding:6px 10px; border-radius:999px; font-weight:800; font-size:12px; border:1px solid transparent; }}
  .sector-hot {{ background:#dcfce7; color:#166534; border-color:#86efac; }}
  .sector-warm {{ background:#fff7ed; color:#9a3412; border-color:#fdba74; }}
  .sector-neutral {{ background:#eff6ff; color:#1d4ed8; border-color:#bfdbfe; }}
  .sector-cool {{ background:#f3f4f6; color:#4b5563; border-color:#d1d5db; }}
  @media (max-width: 900px) {{ body {{ padding:12px; }} .brand h1 {{ font-size:24px; }} .search {{ min-width:100%; margin-left:0; margin-top:10px; }} th, td {{ padding:12px 10px; font-size:13px; }} .dash-grid {{ grid-template-columns: 1fr 1fr; }} }}
</style>
</head>
<body>
<div class="container">
  <div class="topbar">
    <div class="brand">
      <h1>📊 AI多週期盤後結構報表</h1>
      <p class="sub">整合 日K 趨勢、真實30分K、族群強弱、溫度與情境敘事，讓報表更像交易員 briefing</p>
    </div>
    <div class="update-card">
      <div class="update-icon">📅</div>
      <div>
        <div class="update-title">更新時間：{update_label}</div>
        <div class="update-sub">每日盤後自動執行同步</div>
      </div>
    </div>
  </div>

  <div class="dash-grid">
    {cards}
  </div>

  <div class="section">
    <div class="section-head">
      <div>
        <div class="section-title">族群強弱總表</div>
        <div class="section-sub">依各族群成員的 AI 分數平均計算，分數越高代表族群越強</div>
      </div>
    </div>
    <div class="sector-wrap">
      <div class="table-scroll">
        <table class="sector-table">
          <thead>
            <tr>
              <th>族群</th>
              <th>平均分數</th>
              <th>狀態</th>
              <th>代表股</th>
              <th>成員數</th>
            </tr>
          </thead>
          <tbody>
            {sector_rows_html}
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <div class="toolbar">
    {buttons}
    <input id="searchBox" class="search" type="text" placeholder="搜尋股票、族群、情境、劇本...">
  </div>

  <div class="section">
    <div class="section-head">
      <div>
        <div class="section-title">個股 AI 盤後筆記</div>
        <div class="section-sub">股票欄已整合股票 / 族群 / AI分數 / 現價；多週期判讀只保留方向 / 位階 / 節奏 / 情緒</div>
      </div>
    </div>
    <div class="table-wrap table-scroll">
      <table class="main-table">
        <thead>
          <tr>
            <th>股票</th>
            <th>多週期判讀</th>
            <th>情境提醒</th>
            <th>AI策略劇本</th>
          </tr>
        </thead>
        <tbody>
          {rows_html}
        </tbody>
      </table>
    </div>
  </div>

  <div class="sub" style="margin-top:12px;">自動產生時間：{update_label}</div>
</div>

<script>
  const buttons = document.querySelectorAll('.btn');
  const searchBox = document.getElementById('searchBox');
  const rows = document.querySelectorAll('tbody tr[data-cats]');

  function applyFilter() {{
    const activeBtn = document.querySelector('.btn.active');
    const cat = activeBtn ? activeBtn.dataset.cat : '全部';
    const q = (searchBox.value || '').toLowerCase().trim();

    rows.forEach(row => {{
      const cats = row.dataset.cats || '';
      const text = row.innerText.toLowerCase();
      const catOk = (cat === '全部') || cats.includes(cat);
      const qOk = !q || text.includes(q);
      row.style.display = (catOk && qOk) ? '' : 'none';
    }});
  }}

  buttons.forEach(btn => {{
    btn.addEventListener('click', () => {{
      buttons.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      applyFilter();
    }});
  }});

  searchBox.addEventListener('input', applyFilter);
</script>

</body>
</html>
"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(html)

def _send_tg(html_path: str, cfg: Config) -> None:
    try:
        if not bool(_cfg("發送HTML到TG", True)):
            return

        token = str(getattr(cfg, "TG_TOKEN", "")).strip()
        chat_id = str(getattr(cfg, "TG_CHAT_ID", "")).strip()
        if not token or not chat_id or token == "*" or chat_id == "*":
            print("⚠️ TG 金鑰未配置完整，略過 TG 報表發送。")
            return

        url = f"https://api.telegram.org/bot{token}/sendDocument"
        with open(html_path, "rb") as f:
            r = requests.post(
                url,
                data={
                    "chat_id": chat_id,
                    "caption": f"📊 AI多週期盤後結構報表已更新 ({_today()})！",
                },
                files={"document": f},
                timeout=30,
            )

        if r.status_code == 200:
            print("📨 HTML 報表已成功發送至 TG 頻道")
        else:
            print(f"❌ TG 發送失敗: {r.text}")
    except Exception as e:
        print(f"❌ TG 發送發生未預期錯誤: {e}")

def _git_sync() -> None:
    try:
        if not bool(_cfg("自動Git同步", False)):
            print("ℹ️ 已關閉自動 Git 同步")
            return

        status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if not status.stdout.strip():
            print("✅ 無變更，略過 Git 操作")
            return

        print("🔄 自動 Git 同步與推送...")
        subprocess.run(["git", "add", "."], check=True, timeout=20)
        subprocess.run(["git", "commit", "-m", "auto update multicycle report"], check=True, timeout=20)
        subprocess.run(["git", "push"], check=True, timeout=60)
        print("✅ GitHub 倉儲同步完成")
    except Exception as e:
        print("❌ Git 同步失敗")
        print(e)

# =========================================================
# 主程式進入點
# =========================================================

def 產生報表():
    # A. 讀取安全金鑰與憑證 (config.py)
    try:
        sys_cfg = Config()
        print("✅ 成功讀取 config.py 安全金鑰設定")
    except Exception as e:
        print(f"❌ 錯誤：無法載入 config.py 金鑰設定檔！原因: {e}")
        return

    api_key = str(getattr(sys_cfg, "永豐API_KEY", "")).strip()
    secret_key = str(getattr(sys_cfg, "永豐SECRET_KEY", "")).strip()

    ca_path = getattr(sys_cfg, "CA_PATH", None)
    ca_password = getattr(sys_cfg, "CA_PASSWORD", None)
    person_id = getattr(sys_cfg, "PERSON_ID", None)

    if not api_key or not secret_key or api_key == "*" or secret_key == "*":
        print("❌ 錯誤：請先在 config.py 中填寫真實的 永豐API_KEY 與 SECRET_KEY！")
        return

    loader = ShioajiSafeLoader(
        api_key=api_key,
        secret_key=secret_key,
        simulation=False,
        ca_path=ca_path,
        ca_password=ca_password,
        person_id=person_id
    )

    if not loader.login():
        print("❌ 無法建立 Shioaji 連線，自動中斷報表生成。")
        return

    rows = []
    print("📊 開始依據策略設定產生分析報表...")

    try:
        for symbol in _股票池().keys():
            try:
                row = _analyze(symbol, loader)
                if row:
                    rows.append(row)
                    print(f"  {symbol} ✅")
                else:
                    print(f"  {symbol} ❌ 資料不足 / 讀取失敗")
            except Exception as e:
                print(f"  {symbol} ❌ 發生未預期錯誤: {e}")
    finally:
        loader.logout()

    if len(rows) == 0:
        print("❌ 所有的股票都沒有成功取得資料。")
        return

    df = pd.DataFrame(rows)
    if len(df) > 0:
        drop_cols = [c for c in ["_df5", "_df30", "_df_day", "_feature_score_adj"] if c in df.columns]
        if drop_cols:
            df = df.drop(columns=drop_cols)

        if "AI分數" in df.columns:
            df = df.sort_values(["AI分數", "_sort", "股票"], ascending=[False, True, True]).reset_index(drop=True)

        sector_df = _族群強弱表(df)
        dashboard = _Dashboard摘要(df, sector_df)
    else:
        sector_df = pd.DataFrame()
        dashboard = _Dashboard摘要(df, sector_df)

    outdir = _cfg("報表輸出目錄", "report_out")
    os.makedirs(outdir, exist_ok=True)
    os.makedirs("docs", exist_ok=True)

    today = _today()
    csv_path = os.path.join(outdir, f"report_{today}.csv")
    html_path = os.path.join(outdir, f"report_{today}.html")

    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    _html(df, html_path)

    shutil.copy2(html_path, "index.html")
    shutil.copy2(html_path, "docs/index.html")

    print("\n🎉 報表成功產出！已自動同步：")
    print("  📁 index.html")
    print("  📁 docs/index.html")
    print(f"  📝 CSV 資料檔: {csv_path}")
    print(f"  🎨 HTML 報表檔: {html_path}")

    _send_tg(html_path, sys_cfg)
    _git_sync()

if __name__ == "__main__":
    產生報表()