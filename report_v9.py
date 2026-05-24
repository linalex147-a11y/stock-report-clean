from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests

try:
    from config import 設定 as 基本設定  # type: ignore
except Exception:
    基本設定 = None

try:
    from FinMind.data import DataLoader
except Exception as e:
    raise RuntimeError(
        "找不到 FinMind，請先執行：pip install FinMind"
    ) from e


# =========================================================
# 報表設定載入
# =========================================================

try:
    from report_config import 報表設定  # type: ignore
except Exception:
    try:
        from report_config_v2 import 報表設定  # type: ignore
    except Exception:
        @dataclass
        class _Fallback報表設定:
            報表標的: Dict[str, str] = field(default_factory=lambda: {
                "5443": "均豪",
                "2492": "華新科",
                "8054": "安國",
                "2337": "旺宏",
                "2367": "燿華",
                "6919": "康霈",
                "2359": "所羅門",
                "1513": "中興電",
                "4979": "華星光",
                "3264": "欣銓",
                "3017": "奇鋐",
                "3324": "雙鴻",
                "2382": "廣達",
                "3231": "緯創",
                "2376": "技嘉",
                "2356": "英業達",
                "2301": "光寶科",
                "2421": "建準",
                "6669": "緯穎",
                "3706": "神達",
                "3035": "智原",
                "3661": "世芯-KY",
                "3443": "創意",
                "2454": "聯發科",
                "6526": "達發",
                "2363": "矽統",
                "8046": "南電",
                "3189": "景碩",
                "3037": "欣興",
                "2368": "金像電",
                "6274": "台燿",
                "5469": "瀚宇博",
                "4958": "臻鼎-KY",
                "8039": "台虹",
                "2344": "華邦電",
                "2408": "南亞科",
                "5351": "鈺創",
                "8299": "群聯",
                "4967": "十銓",
                "3260": "威剛",
                "2451": "創見",
                "4908": "前鼎",
                "3081": "聯亞",
                "3363": "上詮",
                "3450": "聯鈞",
                "3163": "波若威",
                "2049": "上銀",
                "1536": "和大",
                "1597": "直得",
                "4562": "穎漢",
                "4571": "鈞興-KY",
                "2383": "台光電",
                "6213": "聯茂",
            })
            回看天數: int = 90
            前高前低天數: int = 3
            平台回看根數: int = 8
            大量區回看根數: int = 30
            大量K量能均線根數: int = 10
            大量K量能倍率: float = 1.4
            大量K實體最小比例: float = 0.006
            報表輸出目錄: str = "report_out"
            發送HTML到TG: bool = False
            FinMindToken: str = ""
            使用FinMindKBar: bool = False
            分類設定: Dict[str, List[str]] = field(default_factory=lambda: {
                "自選": ["5443", "2492", "8054", "2337", "2367", "6919", "2359", "1513", "4979", "3264"],
                "AI伺服器": ["3017", "3324", "2382", "3231", "2376", "2356", "2301", "2421", "6669", "3706"],
                "ASIC": ["3035", "3661", "3443", "2454", "6526", "2363", "8299"],
                "封測": ["2449", "3711", "6239", "8150", "6271", "6147", "2441", "3265", "6515"],
                "PCB": ["2367", "8046", "3189", "3037", "2368", "6274", "5469", "4958", "8039"],
                "記憶體": ["2344", "2337", "2408", "5351", "8299", "4967", "3260", "2451"],
                "光通訊": ["4979", "4908", "3081", "3363", "3450", "3163"],
                "機器人": ["2049", "1536", "1597", "4562", "2359", "5443", "4571"],
                "玻纖布": ["2383", "6213", "6274", "8039"],
            })
        報表設定 = _Fallback報表設定()

print("🔥 AI 盤後結構分析器 啟動")

_dl: Optional[DataLoader] = None


# =========================================================
# 基本工具
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


def _price(v):
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
        "6271": "同欣電",
        "8150": "南茂",
    }


def _default_封測分類() -> List[str]:
    return ["2449", "3711", "6239", "6271", "8150"]


def _股票池() -> dict:
    pool = dict(_cfg("報表標的", {}))
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


def _finmind_api() -> DataLoader:
    global _dl
    if _dl is None:
        _dl = DataLoader()
        token = str(_cfg("FinMindToken", "") or "").strip()
        if token:
            try:
                _dl.login_by_token(api_token=token)
                print("✅ FinMind Token 登入成功")
            except Exception as e:
                print(f"⚠️ FinMind Token 登入失敗：{e}")
    return _dl


def _trading_days(start: str, end: str) -> List[str]:
    # 先用工作日篩掉週末；假日若有回空，後面會自動略過。
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start=start, end=end)]


def _standardize_ohlcv(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    if df is None or len(df) == 0:
        return None

    df = df.copy()

    rename_map = {
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
        "Trading_Volume": "volume",
        "max": "high",
        "min": "low",
        "date": "ts",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    need_cols = {"open", "high", "low", "close"}
    if not need_cols.issubset(df.columns):
        return None

    if "volume" not in df.columns:
        df["volume"] = 0

    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    if "ts" in df.columns:
        df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
    else:
        df["ts"] = pd.to_datetime(df.index, errors="coerce")

    df = df.dropna(subset=["ts", "open", "high", "low", "close", "volume"])
    df = df.sort_values("ts")
    df = df[~df["ts"].duplicated(keep="last")]
    df = df.set_index("ts")

    return df[["open", "high", "low", "close", "volume"]].copy()


def _load_kbars(symbol: str, start: str, end: str) -> Tuple[Optional[pd.DataFrame], str]:
    """
    回傳：
      df, source
    source:
      - FinMindKBar
      - FinMindDaily
    """
    api = _finmind_api()

    # 先試分鐘 K（sponsor）
    if bool(_cfg("使用FinMindKBar", True)):
        frames = []
        for d in _trading_days(start, end):
            try:
                raw = api.taiwan_stock_kbar(stock_id=symbol, date=d)
            except Exception as e:
                print(f"  {symbol} KBar 失敗 {d}：{e}")
                raw = None

            if raw is None or len(raw) == 0:
                continue

            raw = raw.copy()

            rename_map = {
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
                "minute": "minute",
                "date": "date",
            }
            raw = raw.rename(columns={k: v for k, v in rename_map.items() if k in raw.columns})

            if not {"open", "high", "low", "close"}.issubset(raw.columns):
                continue

            if "volume" not in raw.columns:
                raw["volume"] = 0

            # FinMind KBar: date + minute
            if "minute" in raw.columns and "date" in raw.columns:
                raw["ts"] = pd.to_datetime(
                    raw["date"].astype(str) + " " + raw["minute"].astype(str),
                    errors="coerce",
                )
            elif "date" in raw.columns:
                raw["ts"] = pd.to_datetime(raw["date"], errors="coerce")
            else:
                raw["ts"] = pd.to_datetime(d, errors="coerce")

            for c in ["open", "high", "low", "close", "volume"]:
                raw[c] = pd.to_numeric(raw[c], errors="coerce")

            raw = raw.dropna(subset=["ts", "open", "high", "low", "close", "volume"])
            if len(raw) == 0:
                continue

            frames.append(raw[["ts", "open", "high", "low", "close", "volume"]])

        if frames:
            df = pd.concat(frames, ignore_index=True)
            df = df.sort_values("ts")
            df = df.drop_duplicates(subset=["ts"], keep="last")
            df = df.set_index("ts")
            return df, "FinMindKBar"

        print(f"  {symbol} ⚠️ 分K無資料，改用日K")

    # 再試日K（free）
    try:
        raw = api.taiwan_stock_daily(stock_id=symbol, start_date=start, end_date=end)
    except Exception as e:
        print(f"  {symbol} 日K失敗：{e}")
        return None, "FinMindDaily"

    if raw is None or len(raw) == 0:
        return None, "FinMindDaily"

    raw = raw.copy()
    rename_map = {
        "date": "ts",
        "max": "high",
        "min": "low",
        "Trading_Volume": "volume",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
    }
    raw = raw.rename(columns={k: v for k, v in rename_map.items() if k in raw.columns})

    if "ts" not in raw.columns:
        return None, "FinMindDaily"

    raw["ts"] = pd.to_datetime(raw["ts"], errors="coerce")

    for c in ["open", "high", "low", "close", "volume"]:
        if c not in raw.columns:
            raw[c] = 0
        raw[c] = pd.to_numeric(raw[c], errors="coerce")

    raw = raw.dropna(subset=["ts", "open", "high", "low", "close"])
    if len(raw) == 0:
        return None, "FinMindDaily"

    raw = raw.sort_values("ts").set_index("ts")
    raw = raw[["open", "high", "low", "close", "volume"]].copy()
    return raw, "FinMindDaily"


def _resample_30(df: pd.DataFrame) -> pd.DataFrame:
    return df.resample("30min", closed="left", label="left").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()


def _resample_day(df: pd.DataFrame) -> pd.DataFrame:
    return df.resample("D", closed="left", label="left").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()


# =========================================================
# 指標
# =========================================================

def _add_ma(df: pd.DataFrame) -> pd.DataFrame:
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
    df = _add_ma(df)
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
    df = _add_ma(df)
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
# 撐壓 / 結構
# =========================================================

def _前高前低(df_day: pd.DataFrame) -> Tuple[Optional[float], Optional[float]]:
    n = int(_cfg("前高前低天數", 3))
    recent = df_day.iloc[-min(n, len(df_day)):]
    return _round(recent["close"].max()), _round(recent["close"].min())


def _平台(df30: pd.DataFrame) -> Tuple[Optional[float], Optional[float]]:
    n = int(_cfg("平台回看根數", 8))
    recent = df30.iloc[-min(n, len(df30)):]
    return _round(recent["close"].max()), _round(recent["close"].min())


def _ma(df_day: pd.DataFrame) -> Tuple[Optional[float], Optional[float]]:
    df_day = _add_ma(df_day)
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
    if len(df5) < 15:
        return None, None

    df = _add_ma(df5)
    window_n = int(_cfg("大量區回看根數", 30))
    vma_n = int(_cfg("大量K量能均線根數", 10))
    mul = float(_cfg("大量K量能倍率", 1.4))
    min_body = float(_cfg("大量K實體最小比例", 0.006))

    window = df.iloc[-min(window_n, len(df)):]
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


def _價格列表(現價, 前高, 大量high, 平台高, 前低, 大量low, 平台低, 日ma20, 日ma60):
    壓力候選 = []
    支撐候選 = []

    for x in [前高, 大量high, 平台高, 日ma60]:
        if x is not None:
            壓力候選.append(float(x))

    for x in [前低, 大量low, 平台低, 日ma20]:
        if x is not None:
            支撐候選.append(float(x))

    # 沒有自然壓力 / 支撐時，提供保底參考位，避免欄位空白
    if len(壓力候選) == 0:
        壓力候選.append(round(float(現價) * 1.05, 2))
    if len(支撐候選) == 0:
        支撐候選.append(round(float(現價) * 0.95, 2))

    壓力 = sorted(set([x for x in 壓力候選 if x > 現價]))
    支撐 = sorted(set([x for x in 支撐候選 if x <= 現價]), reverse=True)

    if len(壓力) == 0:
        壓力 = [round(float(現價) * 1.05, 2)]
    if len(支撐) == 0:
        支撐 = [round(float(現價) * 0.95, 2)]

    return 壓力, 支撐


def _壓力支撐文字(壓力_list, 支撐_list):
    壓力 = " / ".join(_price(x) for x in 壓力_list[:2]) if 壓力_list else "-"
    支撐 = " / ".join(_price(x) for x in 支撐_list[:2]) if 支撐_list else "-"
    return 壓力, 支撐


def _風報比(現價: float, 日方向: str, 壓力: List[float], 支撐: List[float]) -> str:
    if not 壓力 or not 支撐:
        return "-"
    if 日方向 == "多":
        reward = 壓力[0] - 現價
        risk = 現價 - 支撐[0]
    else:
        reward = 現價 - 支撐[0]
        risk = 壓力[0] - 現價
    if risk <= 0:
        return "-"
    return f"{(reward / risk):.2f}"


def _量價(df5: pd.DataFrame, df30: pd.DataFrame) -> str:
    df5 = _add_ma(df5)
    df30 = _add_ma(df30)

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
    df = _add_ma(df)
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
    if len(df_day) < 2:
        return "資料不足"

    df = _add_ma(df_day)
    last = df.iloc[-1]
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
    if len(df30) < 4:
        return "資料不足"

    df = _add_ma(df30)
    recent = df.iloc[-min(6, len(df)):]
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
    if 壓力文字 == "-":
        壓力文字 = "上方暫無明確壓力"
    if 支撐文字 == "-":
        支撐文字 = "下方暫無明確支撐"

    if 結構結果 == "主升多":
        return _render_script("🟢 主升結構", f"做多參考：突破 {壓力文字} 後可順勢看強。",
                              f"防守線：先看 {支撐文字}，30分K收破才算轉弱。",
                              f"上方空間：{風報比 if 風報比 != '-' else '待估'}",
                              "多方主導，逢回守住支撐仍可偏多看待。")

    if 結構結果 == "末升段":
        return _render_script("⚠️ 末升段", f"做多參考：不追價，等回測 {支撐文字} 再看。",
                              f"防守線：{支撐文字} 守不住，容易進入轉弱。",
                              f"上方空間：{風報比 if 風報比 != '-' else '偏少'}",
                              "上方剩餘肉不多，追價風險大。")

    if 結構結果 == "多方壓縮":
        return _render_script("🟦 多方壓縮", f"做多參考：先等 {壓力文字} 帶量突破。",
                              f"防守線：回到 {支撐文字} 下方要保守。",
                              f"上方空間：{風報比 if 風報比 != '-' else '未明'}",
                              "整理中，等方向，不急著先猜。")

    if 結構結果 == "多方回檔":
        return _render_script("🟡 多方回檔", f"做多參考：先看 {支撐文字} 是否守穩，再決定。",
                              f"防守線：若 30分K 收破 {支撐文字}，多方節奏會轉弱。",
                              f"回檔風險：{風報比 if 風報比 != '-' else '待看'}",
                              "方向還可以，但上方剩的不多，等回穩再說。")

    if 結構結果 == "高檔強多轉弱":
        return _render_script("🟠 高檔轉弱", f"做多參考：不追，先等 {支撐文字} 企穩。",
                              f"防守線：{支撐文字} 失守，短線容易續跌。",
                              f"上方空間：{風報比 if 風報比 != '-' else '有限'}",
                              "雖然方向還可，但上方剩的不多，先偏保守。")

    if 結構結果 == "高檔出貨":
        return _render_script("🔴 高檔出貨", f"做空參考：反彈到 {壓力文字} 不過，才考慮偏空。",
                              f"防守線：若重新站回 {支撐文字} 之上，需重新評估。",
                              f"回檔風險：{風報比 if 風報比 != '-' else '偏大'}",
                              "高檔壓力重，反彈不追多。")

    if 結構結果 == "空方反彈":
        return _render_script("🟠 空方反彈", f"做空參考：反彈到 {壓力文字} 不過，可留意壓回。",
                              f"防守線：{支撐文字} 若跌破，空方仍有優勢。",
                              f"反彈空間：{風報比 if 風報比 != '-' else '有限'}",
                              "反彈看壓力，不要直接當翻多。")

    if 結構結果 == "空方壓縮":
        return _render_script("🔻 空方壓縮", f"做空參考：等 {支撐文字} 跌破，再看是否延續。",
                              f"防守線：{壓力文字} 沒收復前，偏空思維不變。",
                              f"下方空間：{風報比 if 風報比 != '-' else '待估'}",
                              "弱勢整理，先看支撐是否守不住。")

    if 結構結果 == "主跌空":
        return _render_script("🔴 主跌空", f"做空參考：反彈到 {壓力文字} 不過，可續看空。",
                              f"防守線：{支撐文字} 若失守，容易再開新低。",
                              f"趨勢風險：{風報比 if 風報比 != '-' else '偏高'}",
                              "空方主導，反彈先看壓力。")

    if 結構結果 == "跌深反彈後轉弱":
        return _render_script("🟤 跌深反彈後轉弱", f"做多參考：暫不追，等 {支撐文字} 重新站穩。",
                              f"防守線：{壓力文字} 無法突破，仍偏弱勢。",
                              f"回升空間：{風報比 if 風報比 != '-' else '有限'}",
                              "反彈失敗，還是先保守。")

    if 結構結果 == "橫盤壓縮":
        return _render_script("🟦 橫盤壓縮", f"做多參考：等 {壓力文字} 突破再看。",
                              f"防守線：{支撐文字} 跌破就不妙。",
                              f"風報比：{風報比 if 風報比 != '-' else '未定'}",
                              "目前還在等方向，先觀察不急。")

    return _render_script("🟡 方向待確認", f"做多參考：看 {壓力文字} 是否突破。",
                          f"防守線：看 {支撐文字} 是否守住。",
                          f"風報比：{風報比 if 風報比 != '-' else '未定'}",
                          "先觀察，不急著猜方向。")




def _AI分數(
    結構結果: str,
    日方向: str,
    三十分方向: str,
    量價: str,
    市場位階: str,
    風報比: str,
    主力痕跡: str,
) -> int:
    score = 50

    結構分數 = {
        "主升多": 32,
        "末升段": 20,
        "多方壓縮": 24,
        "多方回檔": 18,
        "高檔強多轉弱": 12,
        "高檔出貨": 8,
        "空方反彈": 16,
        "空方壓縮": 18,
        "主跌空": 6,
        "跌深反彈後轉弱": 10,
        "橫盤壓縮": 20,
        "區間整理": 15,
    }
    score += 結構分數.get(結構結果, 10)

    if 日方向 == "多":
        score += 8
    else:
        score -= 4

    if 三十分方向 == "多":
        score += 5
    else:
        score -= 2

    量價分數 = {
        "放量": 10,
        "量增": 6,
        "量縮": 2,
    }
    score += 量價分數.get(量價, 0)

    位階分數 = {
        "高檔延伸": -10,
        "低檔乖離": 8,
        "均線附近": 4,
        "中繼位階": 2,
        "資料不足": 0,
    }
    score += 位階分數.get(市場位階, 0)

    if isinstance(風報比, str) and 風報比 not in ("-", ""):
        try:
            rr = float(風報比)
            if rr >= 4:
                score += 12
            elif rr >= 3:
                score += 10
            elif rr >= 2:
                score += 7
            elif rr >= 1:
                score += 4
            else:
                score -= 4
        except Exception:
            pass

    if "爆量發動" in 主力痕跡:
        score += 10
    elif "短線轉強" in 主力痕跡:
        score += 6
    elif "量縮壓縮" in 主力痕跡:
        score += 4
    elif "高檔轉弱" in 主力痕跡:
        score -= 6
    elif "爆量壓回" in 主力痕跡:
        score -= 8

    return max(0, min(100, int(round(score))))


def _族群等級(avg_score: float) -> Tuple[str, str]:
    if avg_score >= 80:
        return "主流", "sector-badge sector-hot"
    if avg_score >= 65:
        return "偏強", "sector-badge sector-warm"
    if avg_score >= 50:
        return "整理", "sector-badge sector-neutral"
    return "偏弱", "sector-badge sector-cool"


def _族群強弱表(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=["族群", "平均分數", "狀態", "代表股", "成員數"])

    cats = _分類設定()
    rows = []

    for cat, symbols in cats.items():
        subset = df[df["_cats"].fillna("").apply(
            lambda s: cat in [x.strip() for x in str(s).split(",") if x.strip()]
        )].copy()

        if len(subset) == 0:
            continue

        avg_score = float(subset["AI分數"].mean())
        status, _ = _族群等級(avg_score)

        top_row = subset.sort_values(
            by=["AI分數", "_sort", "股票"],
            ascending=[False, True, True],
        ).iloc[0]

        rows.append({
            "族群": cat,
            "平均分數": round(avg_score, 1),
            "狀態": status,
            "代表股": top_row["股票"],
            "成員數": int(len(subset)),
        })

    sector_df = pd.DataFrame(rows)
    if len(sector_df) == 0:
        return sector_df

    sector_df = sector_df.sort_values(
        by=["平均分數", "成員數", "族群"],
        ascending=[False, False, True],
    ).reset_index(drop=True)
    return sector_df

# =========================================================
# 分析
# =========================================================

def _analyze(symbol: str):
    name = _股票池().get(symbol, symbol)

    start = (datetime.now() - timedelta(days=int(_cfg("回看天數", 90)))).strftime("%Y-%m-%d")
    end = datetime.now().strftime("%Y-%m-%d")

    df5, source = _load_kbars(symbol, start, end)
    if df5 is None or len(df5) < 20:
        return None

    # 如果拿到的是分鐘K，就走原本邏輯；如果是日K，就用日K代用
    if source == "FinMindKBar":
        df30 = _resample_30(df5)
        df_day = _resample_day(df5)
    else:
        df30 = df5.copy()
        df_day = df5.copy()

    if len(df30) < 3 or len(df_day) < 2:
        return None

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

    量價 = _量價(df5, df30)
    節奏30 = _節奏(df30)
    高低結構30 = _高低結構(df30)
    高低結構日 = _高低結構(df_day)
    市場位階 = _市場位階(df_day)
    主力痕跡 = _主力痕跡(df30)

    結構結果 = _結構狀態(日方向, 三十分方向, 高低結構日, 高低結構30, 量價, 市場位階, 主力痕跡)
    rr = _風報比(現價, 日方向, 壓力_list, 支撐_list)
    ai_score = _AI分數(
        結構結果=結構結果,
        日方向=日方向,
        三十分方向=三十分方向,
        量價=量價,
        市場位階=市場位階,
        風報比=rr,
        主力痕跡=主力痕跡,
    )

    if 結構結果 in ("主升多", "末升段"):
        交易狀態 = "🔥 " + 結構結果 + ("｜等突破" if 壓力_list and 壓力_list[0] and 壓力_list[0] > 現價 else "｜可續抱")
    elif 結構結果 in ("主跌空", "高檔出貨", "高檔強多轉弱"):
        交易狀態 = "⚠️ " + 結構結果 + "｜偏保守"
    elif 結構結果 in ("多方回檔", "空方反彈"):
        交易狀態 = "👀 " + 結構結果 + "｜看關鍵位"
    elif 結構結果 in ("橫盤壓縮", "多方壓縮", "空方壓縮"):
        交易狀態 = "💤 " + 結構結果 + "｜等方向"
    else:
        交易狀態 = "⭕ " + 結構結果

    return {
        "股票": f"{symbol} {name}",
        "現價": _round(現價),
        "AI分數": ai_score,
        "交易狀態": 交易狀態,
        "結構結果": 結構結果,
        "日K方向": f"{日方向}｜{日強弱}",
        "30分K方向": f"{三十分方向}｜{三十分強弱}",
        "量價判讀": f"{量價}｜{節奏30}｜{主力痕跡}",
        "壓力": 壓力文字,
        "支撐": 支撐文字,
        "壓力 / 支撐": f"{壓力文字} / {支撐文字}",
        "RR": rr,
        "AI交易劇本": _AI劇本(
            日方向, 三十分方向, 結構結果, 現價, 日ma60,
            壓力文字, 支撐文字, rr, 市場位階, 量價, 節奏30, 高低結構30, 主力痕跡
        ),
        "_cats": _row_cats(symbol),
        "_sort": _結構排序(結構結果),
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
# HTML / TG / Git
# =========================================================


def _html(df: pd.DataFrame, path: str) -> None:
    categories = _分類設定()

    buttons = '<button class="btn active" data-cat="全部">全部</button>\n'
    for cat in categories.keys():
        buttons += f'<button class="btn" data-cat="{cat}">{cat}</button>\n'

    cols = ["股票", "現價", "AI分數", "交易狀態", "AI交易劇本", "壓力 / 支撐"]

    rows_html_list = []
    for _, row in df.sort_values(["_sort", "AI分數", "股票"], ascending=[True, False, True]).iterrows():
        cell_html = []
        for col in cols:
            v = row.get(col, "-")
            if col == "AI交易劇本":
                cell_html.append(f'<td class="script">{str(v)}</td>')
            elif col == "股票":
                cell_html.append(f'<td class="stock-cell">{v}</td>')
            else:
                cell_html.append(f'<td>{v}</td>')
        rows_html_list.append(f'<tr data-cats="{row["_cats"]}">{"".join(cell_html)}</tr>')
    rows_html = "\n".join(rows_html_list)

    sector_df = _族群強弱表(df)
    sector_rows_html_list = []
    if len(sector_df) > 0:
        for _, srow in sector_df.iterrows():
            status, badge_class = _族群等級(float(srow["平均分數"]))
            sector_rows_html_list.append(
                f'<tr>'
                f'<td>{srow["族群"]}</td>'
                f'<td><strong>{srow["平均分數"]}</strong></td>'
                f'<td><span class="{badge_class}">{status}</span></td>'
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
<title>AI盤後結構報表</title>
<style>
  :root {{
    --bg: #f5f7fb;
    --panel: rgba(255,255,255,0.92);
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
    --orange: #d97706;
    --orange-soft: #fff7ed;
    --purple: #7c3aed;
    --purple-soft: #f3e8ff;
  }}

  body {{
    font-family: Arial, "Microsoft JhengHei", sans-serif;
    background: var(--bg);
    margin:0;
    padding:20px;
    color: var(--text);
  }}

  .container {{
    max-width: 1600px;
    margin: auto;
  }}

  .topbar {{
    display:flex;
    justify-content:space-between;
    gap:16px;
    align-items:flex-start;
    margin-bottom:14px;
    flex-wrap:wrap;
  }}

  .brand h1 {{
    margin:0 0 8px;
    font-size:28px;
    line-height:1.2;
  }}

  .sub {{
    color: var(--muted);
    margin:0;
    font-size:13px;
  }}

  .update-card {{
    display:flex;
    gap:12px;
    align-items:center;
    background: var(--panel);
    border:1px solid var(--border);
    border-radius:18px;
    padding:12px 16px;
    box-shadow:0 4px 18px rgba(15,23,42,.06);
    min-width: 300px;
  }}

  .update-icon {{
    font-size:28px;
    line-height:1;
  }}

  .update-title {{
    font-weight:800;
    font-size:15px;
    color: var(--dark);
  }}

  .update-sub {{
    font-size:12px;
    color: var(--muted);
    margin-top:2px;
  }}

  .section {{
    margin-bottom:16px;
    background: var(--panel);
    border:1px solid var(--border);
    border-radius:18px;
    box-shadow:0 8px 24px rgba(15,23,42,.08);
    overflow:hidden;
  }}

  .section-head {{
    padding:14px 16px;
    border-bottom:1px solid var(--border);
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:12px;
    background: linear-gradient(180deg, #ffffff, #f8fafc);
  }}

  .section-title {{
    font-weight:900;
    font-size:18px;
    color: var(--dark);
  }}

  .section-sub {{
    font-size:12px;
    color: var(--muted);
  }}

  .toolbar {{
    margin-bottom:15px;
    display:flex;
    flex-wrap:wrap;
    gap:10px;
    align-items:center;
  }}

  .btn {{
    padding:10px 16px;
    border:none;
    border-radius:999px;
    cursor:pointer;
    background:#e5e7eb;
    font-size:14px;
    font-weight:700;
    color:#1f2937;
    box-shadow:0 1px 1px rgba(0,0,0,.03);
  }}

  .btn.active {{
    background:#2563eb;
    color:white;
  }}

  .search {{
    margin-left:auto;
    min-width:220px;
    max-width:320px;
    width:100%;
    padding:10px 14px;
    border-radius:14px;
    border:1px solid #d7dee8;
    font-size:14px;
    background:white;
    color:#111827;
    box-shadow:0 1px 2px rgba(15,23,42,.04);
  }}

  .table-wrap {{
    overflow-x:auto;
    background: var(--panel);
    border-radius:18px;
    box-shadow:0 8px 24px rgba(15,23,42,.08);
    border:1px solid var(--border);
  }}

  table {{
    width:100%;
    border-collapse:collapse;
    min-width:980px;
  }}

  th {{
    background: linear-gradient(180deg, #1f2937 0%, #0f172a 100%);
    color:white;
    padding:14px 12px;
    position:sticky;
    top:0;
    z-index:2;
    white-space:nowrap;
    font-size:16px;
  }}

  td {{
    padding:14px 12px;
    border-bottom:1px solid #e5e7eb;
    vertical-align:top;
    white-space:nowrap;
    background: rgba(255,255,255,0.8);
  }}

  tr:hover td {{
    background:#f9fafb;
  }}

  td.script {{
    white-space:normal;
    min-width:560px;
    line-height:1.6;
  }}

  .stock-cell {{
    font-size:18px;
    font-weight:800;
  }}

  .script-wrap {{
    display:flex;
    flex-direction:column;
    gap:8px;
    background: linear-gradient(180deg, #0f172a, #111827);
    border:1px solid #334155;
    border-left:5px solid #22c55e;
    border-radius:16px;
    padding:12px;
  }}

  .script-core {{
    font-size:18px;
    font-weight:900;
    padding:8px 12px;
    border-radius:999px;
    background:#dcfce7;
    color:#14532d;
    width:fit-content;
  }}

  .script-line {{
    display:flex;
    flex-wrap:wrap;
    gap:10px;
    align-items:flex-start;
    padding:8px 10px;
    background:rgba(255,255,255,.96);
    border:1px solid #e5e7eb;
    border-radius:12px;
    color:#111827;
    box-shadow:0 1px 0 rgba(15,23,42,.03);
  }}

  .line-label {{
    display:inline-flex;
    align-items:center;
    justify-content:center;
    min-width:58px;
    padding:4px 10px;
    border-radius:999px;
    font-size:13px;
    font-weight:800;
    flex:0 0 auto;
  }}

  .line-text {{
    color:#111827;
    font-size:14px;
    font-weight:600;
    flex:1 1 auto;
  }}

  .line-green {{ background:#ecfdf5; color:#047857; }}
  .line-red {{ background:#fef2f2; color:#dc2626; }}
  .line-blue {{ background:#eff6ff; color:#2563eb; }}

  .script-row {{
    display:flex;
    flex-wrap:wrap;
    gap:8px;
    margin-top:2px;
  }}

  .tag {{
    display:inline-flex;
    align-items:center;
    padding:7px 12px;
    border-radius:12px;
    font-weight:800;
    font-size:13px;
    border:1px solid transparent;
  }}

  .tag-green {{
    background:#ecfdf5;
    color:#065f46;
    border-color:#a7f3d0;
  }}

  .tag-blue {{
    background:#eff6ff;
    color:#1d4ed8;
    border-color:#bfdbfe;
  }}

  .script-summary {{
    margin-top:2px;
    color:#dbeafe;
    background:#0b1220;
    border-left:4px solid #22c55e;
    padding:10px 12px;
    border-radius:12px;
    font-weight:700;
  }}

  .sector-wrap {{
    padding:16px;
  }}

  .sector-table {{
    width:100%;
    border-collapse:separate;
    border-spacing:0;
    overflow:hidden;
  }}

  .sector-table th {{
    position:static;
    font-size:14px;
    padding:12px 10px;
  }}

  .sector-table td {{
    white-space:nowrap;
    font-size:14px;
    padding:12px 10px;
    background:white;
  }}

  .sector-badge {{
    display:inline-flex;
    align-items:center;
    padding:6px 10px;
    border-radius:999px;
    font-weight:800;
    font-size:12px;
    border:1px solid transparent;
  }}

  .sector-hot {{
    background:#dcfce7;
    color:#166534;
    border-color:#86efac;
  }}

  .sector-warm {{
    background:#fff7ed;
    color:#9a3412;
    border-color:#fdba74;
  }}

  .sector-neutral {{
    background:#eff6ff;
    color:#1d4ed8;
    border-color:#bfdbfe;
  }}

  .sector-cool {{
    background:#f3f4f6;
    color:#4b5563;
    border-color:#d1d5db;
  }}

  .ts {{
    margin-top:10px;
    color:#6b7280;
    font-size:12px;
  }}

  @media (max-width: 720px) {{
    body {{ padding:12px; }}
    .brand h1 {{ font-size:22px; }}
    .update-card {{
      min-width:100%;
      box-sizing:border-box;
    }}
    .search {{
      min-width:100%;
      max-width:100%;
      margin-left:0;
    }}
    .script {{ min-width:280px; }}
    table {{ min-width:820px; }}
    th, td {{ padding:10px 10px; font-size:12px; }}
  }}
</style>
</head>
<body>
<div class="container">
  <div class="topbar">
    <div class="brand">
      <h1>📊 AI盤後結構報表</h1>
      <p class="sub">整合趨勢、結構、高低點、均線節奏、量價、平台、位階、風報比、類股分類</p>
    </div>

    <div class="update-card">
      <div class="update-icon">📅</div>
      <div>
        <div class="update-title">更新日期：{update_label}</div>
        <div class="update-sub">每日盤後自動更新</div>
      </div>
    </div>
  </div>

  <div class="section">
    <div class="section-head">
      <div>
        <div class="section-title">族群強弱總表</div>
        <div class="section-sub">依各族群成員的 AI 分數平均計算，分數越高代表族群越強</div>
      </div>
    </div>
    <div class="sector-wrap">
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

  <div class="toolbar">
    {buttons}
    <input id="searchBox" class="search" type="text" placeholder="搜尋股票或名稱">
  </div>

  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>股票</th>
          <th>現價</th>
          <th>AI分數</th>
          <th>交易狀態</th>
          <th>AI交易劇本</th>
          <th>壓力 / 支撐</th>
        </tr>
      </thead>
      <tbody id="reportBody">
        {rows_html}
      </tbody>
    </table>
  </div>

  <div class="ts">產生時間：{_now_str()}</div>
</div>

<script>
(function() {{
  const buttons = Array.from(document.querySelectorAll('.btn'));
  const searchBox = document.getElementById('searchBox');
  const rows = Array.from(document.querySelectorAll('#reportBody tr'));
  let currentCat = '全部';

  function normalize(s) {{
    return (s || '').toString().toLowerCase();
  }}

  function applyFilter() {{
    const q = normalize(searchBox.value);
    rows.forEach(row => {{
      const cats = row.dataset.cats || '';
      const text = normalize(row.innerText);
      const catOk = currentCat === '全部' || cats.split(',').includes(currentCat);
      const textOk = !q || text.includes(q);
      row.style.display = (catOk && textOk) ? '' : 'none';
    }});
  }}

  buttons.forEach(btn => {{
    btn.addEventListener('click', () => {{
      buttons.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentCat = btn.dataset.cat || '全部';
      applyFilter();
    }});
  }});

  searchBox.addEventListener('input', applyFilter);
}})();
</script>
</body>
</html>
"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def _send_tg(html_path: str) -> None:
    try:
        if not getattr(報表設定, "發送HTML到TG", False):
            return

        token = getattr(報表設定, "TG_TOKEN", "")
        chat_id = getattr(報表設定, "TG_CHAT_ID", "")
        if not token or not chat_id:
            return

        url = f"https://api.telegram.org/bot{token}/sendDocument"
        with open(html_path, "rb") as f:
            r = requests.post(
                url,
                data={
                    "chat_id": chat_id,
                    "caption": "📊 AI盤後結構報表",
                },
                files={"document": f},
                timeout=30,
            )

        if r.status_code == 200:
            print("📨 HTML 已發送 TG")
        else:
            print("❌ TG 發送失敗")
            print(r.text)
    except Exception as e:
        print("❌ TG 發送錯誤")
        print(e)

def _git_sync() -> None:
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if not status.stdout.strip():
            print("✅ 無變更，略過 Git 操作")
            return

        print("🔄 自動 Git 同步...")
        subprocess.run(["git", "add", "."], check=True, timeout=20)
        subprocess.run(["git", "commit", "-m", "auto update report"], check=True, timeout=20)
        subprocess.run(["git", "push"], check=True, timeout=60)
        print("✅ GitHub 已更新")
    except Exception as e:
        print("❌ Git 同步失敗")
        print(e)


# =========================================================
# 主程式
# =========================================================

def 產生報表():
    rows = []
    print("📊 產生 AI 盤後結構報表...")

    for symbol in _股票池().keys():
        try:
            row = _analyze(symbol)
            if row:
                rows.append(row)
                print(f"  {symbol} ✅")
            else:
                print(f"  {symbol} ❌ 無資料 / 不足")
        except Exception as e:
            print(f"  {symbol} ❌ {e}")

    if len(rows) == 0:
        print("❌ 無資料")
        return

    df = pd.DataFrame(rows)

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

    print("✅ 已同步：")
    print("  index.html")
    print("  docs/index.html")
    print(f"CSV：{csv_path}")
    print(f"HTML：{html_path}")

    _send_tg(html_path)
    _git_sync()


if __name__ == "__main__":
    產生報表()
