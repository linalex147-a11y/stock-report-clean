
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests

import report_tick_human_v5 as base
from config import Config
from market_cache import MarketCache


# =========================================================
# 快取層
# =========================================================

CACHE = MarketCache(
    root_dir=str(getattr(base.報表設定, "Tick快取目錄", "tick_cache")),
    overlap_days=1,
)


# =========================================================
# 小工具
# =========================================================

def _cfg(name: str, default=None):
    return getattr(base.報表設定, name, default)


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


def _股票池() -> dict:
    return dict(_cfg("報表標的", {}))


def _分類設定() -> dict:
    return dict(_cfg("分類設定", {}))


def _row_cats(symbol: str) -> str:
    matched = []
    for cat, symbols in _分類設定().items():
        if symbol in symbols:
            matched.append(cat)
    if not matched:
        matched = ["其他"]
    return ",".join(matched)


def _main_sector(symbol: str) -> str:
    cats = [c for c in _row_cats(symbol).split(",") if c and c != "自選"]
    return cats[0] if cats else "其他"


def _unique_keep_order(seq):
    out = []
    for x in seq:
        if x is None:
            continue
        if x not in out:
            out.append(x)
    return out


def _sector_label(avg_score: float) -> str:
    if avg_score >= 80:
        return "sector-hot"
    if avg_score >= 65:
        return "sector-warm"
    if avg_score >= 50:
        return "sector-neutral"
    return "sector-cool"


def _temp_label(score: int, rr: float) -> str:
    if score >= 80 and rr >= 1.5:
        return "🔥 攻擊盤"
    if score >= 65 and rr >= 1.0:
        return "🌤 積極盤"
    if score >= 50:
        return "🌥 中性盤"
    return "⚠️ 保守盤"


def _交易狀態(結構結果: str, 暫看: str) -> str:
    icon = {
        "主升多": "🔥",
        "末升段": "⚠️",
        "多方壓縮": "💤",
        "多方回檔": "👀",
        "高檔強多轉弱": "🟠",
        "高檔出貨": "🔴",
        "空方反彈": "🟠",
        "空方壓縮": "💤",
        "主跌空": "🔴",
        "跌深反彈後轉弱": "🟤",
        "橫盤壓縮": "🟦",
        "區間整理": "⭕",
    }.get(結構結果, "⭕")
    return f"{icon} {結構結果}｜{暫看}"


def _市場溫度(score: int, rr: float) -> str:
    if score >= 80 and rr >= 1.5:
        return "🔥 攻擊盤"
    if score >= 65 and rr >= 1.0:
        return "🌤 積極盤"
    if score >= 50:
        return "🌥 中性盤"
    return "⚠️ 保守盤"


def _情境提醒(
    結構結果: str,
    日強弱: str,
    三十分強弱: str,
    市場位階: str,
    量價: str,
    主力痕跡: str,
    rr: float,
    sector_strength: str,
) -> str:
    rr_txt = "空間不足" if rr < 0.8 else ("空間普通" if rr < 1.5 else "空間健康")
    if 結構結果 == "主升多":
        return f"資金剛開始集中，價格整理後仍有續攻味道，屬於偏積極的推進階段。{sector_strength}，{rr_txt}。"
    if 結構結果 == "末升段":
        return f"趨勢仍偏多，但節奏已經開始鈍化，追價風險升高。{sector_strength}，{rr_txt}。"
    if 結構結果 == "多方壓縮":
        return f"量縮整理中，賣壓不重，但需要等明確突破才有延續。{sector_strength}，{rr_txt}。"
    if 結構結果 == "多方回檔":
        return f"主趨勢還沒壞，但短線先進入回檔觀察，重點看支撐是否守穩。{sector_strength}，{rr_txt}。"
    if 結構結果 == "高檔強多轉弱":
        return f"高檔開始出現轉弱訊號，若量能續縮，容易轉成震盪整理。{sector_strength}，{rr_txt}。"
    if 結構結果 == "高檔出貨":
        return f"高檔調節味道明顯，反彈若無法站穩關鍵位，容易續弱。{sector_strength}，{rr_txt}。"
    if 結構結果 == "空方反彈":
        return f"反彈屬性較強，先看壓力是否有效，暫不急著翻多。{sector_strength}，{rr_txt}。"
    if 結構結果 == "空方壓縮":
        return f"空方整理中，若支撐失守，容易再往下找平衡。{sector_strength}，{rr_txt}。"
    if 結構結果 == "主跌空":
        return f"空方主導，反彈主要是測壓力，追價風險高。{sector_strength}，{rr_txt}。"
    if 結構結果 == "跌深反彈後轉弱":
        return f"跌深後的反彈力道有限，若無法站回壓力，仍偏弱勢。{sector_strength}，{rr_txt}。"
    if 結構結果 == "橫盤壓縮":
        return f"多空都在等方向，先觀察是否有帶量突破。{sector_strength}，{rr_txt}。"
    return f"目前結構仍在整理與確認之間，先等方向更明朗。{sector_strength}，{rr_txt}。"


def _AI分數(
    日方向: str,
    三十分方向: str,
    日強弱: str,
    三十分強弱: str,
    結構結果: str,
    市場位階: str,
    量價: str,
    主力痕跡: str,
    rr: float,
    sector_avg: float,
) -> int:
    score = 50

    if 日方向 == "多":
        score += 10
    else:
        score -= 8

    if 三十分方向 == "多":
        score += 14
    else:
        score -= 10

    score += {"強多": 12, "偏多": 8, "盤整": 0, "偏空": -8, "強空": -12}.get(日強弱, 0)
    score += {"強多": 14, "偏多": 8, "盤整": 0, "偏空": -8, "強空": -14}.get(三十分強弱, 0)

    score += {
        "主升多": 15,
        "多方壓縮": 10,
        "多方回檔": 4,
        "末升段": -5,
        "高檔強多轉弱": -10,
        "高檔出貨": -18,
        "空方反彈": -8,
        "空方壓縮": -10,
        "主跌空": -20,
        "跌深反彈後轉弱": -12,
        "橫盤壓縮": 2,
        "區間整理": 0,
    }.get(結構結果, 0)

    score += {
        "高檔延伸": -10,
        "中繼位階": 8,
        "均線附近": 4,
        "低檔乖離": 2,
    }.get(市場位階, 0)

    score += {
        "放量": 8,
        "量增": 4,
        "量縮": 1,
    }.get(量價, 0)

    score += {
        "爆量發動": 10,
        "短線轉強": 6,
        "量縮壓縮": 3,
        "一般整理": 0,
        "高檔轉弱": -8,
        "爆量壓回": -12,
    }.get(主力痕跡, 0)

    if rr < 0.8:
        score -= 28
    elif rr < 1.2:
        score -= 10
    elif rr < 2.0:
        score += 5
    else:
        score += 12

    if sector_avg >= 80:
        score += 6
    elif sector_avg >= 65:
        score += 3
    elif sector_avg < 50:
        score -= 4

    return max(0, min(100, int(round(score))))


def _市場位階_文字(df_day: pd.DataFrame) -> str:
    return base._市場位階(df_day)  # type: ignore[attr-defined]


def _載入K棒(symbol: str, loader) -> Tuple[Optional[pd.DataFrame], Dict[str, str]]:
    start = (datetime.now() - timedelta(days=int(_cfg("回看天數", 90)))).strftime("%Y-%m-%d")
    end = datetime.now().strftime("%Y-%m-%d")

    df5, info = CACHE.load_or_update(
        symbol=symbol,
        loader=loader,
        start=start,
        end=end,
        force_refresh_today=False,
    )
    meta = {
        "source": info.source,
        "path": info.path,
        "updated": str(info.updated),
        "rows": str(info.rows),
        "last_ts": info.last_ts or "",
        "fetched_start": info.fetched_start or "",
        "fetched_end": info.fetched_end or "",
    }
    if df5 is not None:
        df5 = df5.sort_index()
    return df5, meta


def _analyze(symbol: str, loader):
    name = _股票池().get(symbol, symbol)
    df5, cache_meta = _載入K棒(symbol, loader)
    if df5 is None or len(df5) < 30:
        return None

    # 只保留正規交易時間，避免午休造成假30M
    df5 = df5.between_time("09:00", "11:30").copy()
    afternoon = df5.between_time("13:00", "13:30").copy()
    df5 = pd.concat([df5, afternoon]).sort_index()

    if len(df5) < 30:
        return None

    df30 = base._resample_30(df5)  # type: ignore[attr-defined]
    df_day = base._resample_day(df5)  # type: ignore[attr-defined]

    if len(df30) < 5 or len(df_day) < 3:
        return None

    df5 = base._add_ma(df5)  # type: ignore[attr-defined]
    df30 = base._add_ma(df30)  # type: ignore[attr-defined]
    df_day = base._add_ma(df_day)  # type: ignore[attr-defined]

    日方向 = base._方向(df_day)  # type: ignore[attr-defined]
    三十分方向 = base._方向(df30)  # type: ignore[attr-defined]
    日強弱 = base._趨勢強度(df_day)  # type: ignore[attr-defined]
    三十分強弱 = base._趨勢強度(df30)  # type: ignore[attr-defined]

    前高, 前低 = base._前高前低(df_day)  # type: ignore[attr-defined]
    平台高, 平台低 = base._平台(df30)  # type: ignore[attr-defined]
    日ma20, 日ma60 = base._ma(df_day)  # type: ignore[attr-defined]

    現價 = float(df5.iloc[-1]["close"])
    大量high, 大量low = base._大量K_high_low(df5, 日方向)  # type: ignore[attr-defined]
    壓力_list, 支撐_list = base._價格列表(現價, 前高, 大量high, 平台高, 前低, 大量low, 平台低, 日ma20, 日ma60)  # type: ignore[attr-defined]
    壓力文字, 支撐文字 = base._壓力支撐文字(壓力_list, 支撐_list)  # type: ignore[attr-defined]

    量價 = base._量價(df5, df30)  # type: ignore[attr-defined]
    節奏30 = base._節奏(df30)  # type: ignore[attr-defined]
    高低結構30 = base._高低結構(df30)  # type: ignore[attr-defined]
    高低結構日 = base._高低結構(df_day)  # type: ignore[attr-defined]
    市場位階 = base._市場位階(df_day)  # type: ignore[attr-defined]
    主力痕跡 = base._主力痕跡(df30)  # type: ignore[attr-defined]

    結構結果 = base._結構狀態(日方向, 三十分方向, 高低結構日, 高低結構30, 量價, 市場位階, 主力痕跡)  # type: ignore[attr-defined]
    rr_text = base._風報比(現價, 日方向, 壓力_list, 支撐_list)  # type: ignore[attr-defined]
    try:
        rr = float(rr_text)
    except Exception:
        rr = 0.0

    sector = _main_sector(symbol)
    sector_scores = []  # 之後會在產生報表時統計，先用暫值
    暫算族群平均 = 0.0

    ai_score = _AI分數(
        日方向, 三十分方向, 日強弱, 三十分強弱, 結構結果,
        市場位階, 量價, 主力痕跡, rr, 暫算族群平均
    )
    市場溫度 = _市場溫度(ai_score, rr)

    if 結構結果 in ("主升多", "末升段"):
        暫看 = "等突破" if (壓力_list and 壓力_list[0] and 壓力_list[0] > 現價) else "可續抱"
    elif 結構結果 in ("主跌空", "高檔出貨", "高檔強多轉弱"):
        暫看 = "偏保守"
    elif 結構結果 in ("多方回檔", "空方反彈"):
        暫看 = "看關鍵位"
    elif 結構結果 in ("橫盤壓縮", "多方壓縮", "空方壓縮"):
        暫看 = "等方向"
    else:
        暫看 = 結構結果

    交易狀態 = _交易狀態(結構結果, 暫看)

    sector_strength = f"族群：{sector}｜{len(_股票池())}檔清單"
    情境提醒 = _情境提醒(
        結構結果,
        日強弱,
        三十分強弱,
        市場位階,
        量價,
        主力痕跡,
        rr,
        sector_strength,
    )

    多週期判讀 = (
        f"日K：{日方向}｜{日強弱}\n"
        f"30M：{三十分方向}｜{三十分強弱}\n"
        f"位階：{市場位階}\n"
        f"量能：{量價}｜RV {float(df30['volume'].iloc[-1] / max(1.0, float(df30['VMA10'].iloc[-1]))):.2f}x\n"
        f"主力：{主力痕跡}"
    )

    AI策略劇本 = base._AI劇本(  # type: ignore[attr-defined]
        日方向, 三十分方向, 結構結果, 現價, 日ma60,
        壓力文字, 支撐文字, rr_text, 市場位階, 量價, 節奏30, 高低結構30, 主力痕跡
    )

    關鍵撐壓RR = (
        f"壓：{壓力文字}\n"
        f"支：{支撐文字}\n"
        f"RR: {rr_text}"
    )

    return {
        "股票": f"{symbol} {name}",
        "族群": sector,
        "現價": _round(現價),
        "AI分數": ai_score,
        "交易狀態": 交易狀態,
        "市場溫度": 市場溫度,
        "多週期判讀": 多週期判讀,
        "情境提醒": 情境提醒,
        "AI策略劇本": AI策略劇本,
        "關鍵撐壓 / RR": 關鍵撐壓RR,
        "_cats": _row_cats(symbol),
        "_sort": base._結構排序(結構結果),  # type: ignore[attr-defined]
    }


def _sector_summary_html(df: pd.DataFrame) -> str:
    if df is None or len(df) == 0 or "族群" not in df.columns:
        return "<div class='empty'>無族群資料</div>"

    g = (
        df.groupby("族群")
        .agg(平均分數=("AI分數", "mean"), 成員數=("股票", "count"))
        .reset_index()
        .sort_values(["平均分數", "成員數"], ascending=[False, False])
    )
    rows = []
    for _, r in g.iterrows():
        avg = float(r["平均分數"])
        rows.append(
            f"""
            <tr>
              <td>{r['族群']}</td>
              <td><span class="sector-badge {_sector_label(avg)}">{avg:.1f}</span></td>
              <td>{'🔥' if avg>=80 else ('🌤' if avg>=65 else ('🌥' if avg>=50 else '⚪'))}</td>
              <td>{r['成員數']}</td>
            </tr>
            """
        )
    return "\n".join(rows)


def _html(df: pd.DataFrame, path: str) -> None:
    categories = _分類設定()
    buttons = '<button class="btn active" data-cat="全部">全部</button>\n'
    for cat in categories.keys():
        buttons += f'<button class="btn" data-cat="{cat}">{cat}</button>\n'

    sector_rows = _sector_summary_html(df)

    rows_html = ""
    for _, row in df.sort_values(["_sort", "股票"]).iterrows():
        multi_html = str(row.get("多週期判讀", "-")).replace("\n", "<br>")
        hint_html = str(row.get("情境提醒", "-")).replace("\n", "<br>")
        script_html = str(row.get("AI策略劇本", "-")).replace("\n", "<br>")
        rr_html = str(row.get("關鍵撐壓 / RR", "-")).replace("\n", "<br>")
        rows_html += f"""
        <tr data-cats="{row.get('_cats','')}">
          <td class="stock">{row.get("股票","-")}</td>
          <td class="sector">{row.get("族群","-")}</td>
          <td class="price">{row.get("現價","-")}</td>
          <td class="score">{row.get("AI分數","-")}</td>
          <td class="status">{row.get("交易狀態","-")}</td>
          <td class="temp">{row.get("市場溫度","-")}</td>
          <td class="multi">{multi_html}</td>
          <td class="hint">{hint_html}</td>
          <td class="script">{script_html}</td>
          <td class="rr">{rr_html}</td>
        </tr>
        """

    update_label = _now_str()

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI盤後結構報表</title>
<style>
  :root {{
    --bg:#f6f7fb; --panel:#fff; --line:#e5e7eb; --text:#111827; --muted:#6b7280;
    --green:#15803d; --green-bg:#ecfdf5; --orange:#c2410c; --orange-bg:#fff7ed;
    --blue:#1d4ed8; --blue-bg:#eff6ff; --red:#b91c1c; --red-bg:#fef2f2;
  }}
  body {{ font-family: "Microsoft JhengHei", system-ui, sans-serif; background:var(--bg); margin:0; padding:20px; color:var(--text); }}
  .container {{ max-width: 1800px; margin:auto; }}
  .topbar {{ display:flex; justify-content:space-between; gap:16px; align-items:flex-start; flex-wrap:wrap; margin-bottom:16px; }}
  .brand h1 {{ margin:0 0 6px; font-size:28px; font-weight:900; }}
  .sub {{ margin:0; color:var(--muted); }}
  .update-card {{ background:var(--panel); border:1px solid var(--line); border-radius:16px; padding:12px 18px; min-width:260px; box-shadow:0 6px 18px rgba(0,0,0,.03); }}
  .update-title {{ font-weight:900; font-size:15px; }}
  .update-sub {{ font-size:12px; color:var(--muted); margin-top:4px; }}
  .section {{ background:var(--panel); border:1px solid var(--line); border-radius:18px; margin-bottom:16px; overflow:hidden; box-shadow:0 10px 24px rgba(0,0,0,.04); }}
  .section-head {{ padding:16px 18px; border-bottom:1px solid var(--line); }}
  .section-title {{ font-weight:900; font-size:16px; }}
  .section-sub {{ color:var(--muted); font-size:12px; margin-top:4px; }}
  .sector-wrap {{ padding:14px 18px; }}
  .sector-table, .main-table {{ width:100%; border-collapse:separate; border-spacing:0; }}
  .sector-table th, .sector-table td, .main-table th, .main-table td {{ padding:12px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
  .sector-table th, .main-table th {{ background:#0f172a; color:#fff; position:sticky; top:0; z-index:2; font-size:14px; text-align:left; }}
  .sector-badge {{ display:inline-flex; align-items:center; padding:5px 10px; border-radius:999px; font-weight:800; font-size:12px; }}
  .sector-hot {{ background:#dcfce7; color:#166534; }}
  .sector-warm {{ background:#fff7ed; color:#9a3412; }}
  .sector-neutral {{ background:#eff6ff; color:#1d4ed8; }}
  .sector-cool {{ background:#f3f4f6; color:#4b5563; }}
  .toolbar {{ margin:14px 0; display:flex; gap:10px; flex-wrap:wrap; align-items:center; }}
  .btn {{ padding:8px 14px; border-radius:999px; border:1px solid var(--line); background:var(--panel); cursor:pointer; font-weight:700; }}
  .btn.active {{ background:#2563eb; color:#fff; border-color:#2563eb; }}
  .search {{ margin-left:auto; min-width:280px; padding:10px 14px; border-radius:999px; border:1px solid var(--line); outline:none; background:#fff; }}
  .table-wrap {{ background:var(--panel); border:1px solid var(--line); border-radius:18px; overflow:auto; box-shadow:0 10px 24px rgba(0,0,0,.04); }}
  .main-table {{ min-width: 1800px; }}
  .main-table td {{ font-size:13px; line-height:1.45; }}
  .stock {{ white-space:nowrap; font-weight:900; }}
  .sector {{ white-space:nowrap; font-weight:700; color:#0f172a; }}
  .price, .score {{ white-space:nowrap; font-weight:800; font-family:monospace; }}
  .status {{ white-space:nowrap; font-weight:700; }}
  .temp {{ white-space:nowrap; font-weight:900; }}
  .multi, .hint, .script, .rr {{ white-space:normal; }}
  .multi {{ min-width: 220px; }}
  .hint {{ min-width: 260px; }}
  .script {{ min-width: 560px; }}
  .rr {{ min-width: 180px; }}
  .hint {{ background:#f8fafc; border-left:3px solid #3b82f6; }}
  .script {{ background:#fff; border-left:3px solid #10b981; }}
  .rr {{ background:#fafafa; }}
  .empty {{ padding:12px 18px; color:var(--muted); }}
  .ts {{ margin-top:12px; color:var(--muted); font-size:12px; }}
  @media (max-width: 900px) {{
    body {{ padding:10px; }}
    .search {{ min-width:100%; margin-left:0; }}
    .brand h1 {{ font-size:22px; }}
  }}
</style>
</head>
<body>
<div class="container">
  <div class="topbar">
    <div class="brand">
      <h1>📊 AI盤後結構報表</h1>
      <p class="sub">整合趨勢、結構、高低點、均線節奏、量價、平台、位階、風報比、族群溫度</p>
    </div>
    <div class="update-card">
      <div class="update-title">更新時間：{update_label}</div>
      <div class="update-sub">CACHE 增量更新版</div>
    </div>
  </div>

  <div class="section">
    <div class="section-head">
      <div class="section-title">族群強弱總表</div>
      <div class="section-sub">依族群內股票的 AI 分數平均計算</div>
    </div>
    <div class="sector-wrap">
      <table class="sector-table">
        <thead>
          <tr>
            <th>族群</th>
            <th>平均分數</th>
            <th>狀態</th>
            <th>成員數</th>
          </tr>
        </thead>
        <tbody>
          {sector_rows}
        </tbody>
      </table>
    </div>
  </div>

  <div class="toolbar">
    {buttons}
    <input id="searchBox" class="search" type="text" placeholder="搜尋股票、族群或關鍵字">
  </div>

  <div class="table-wrap">
    <table class="main-table">
      <thead>
        <tr>
          <th>股票</th>
          <th>族群</th>
          <th>現價</th>
          <th>AI分數</th>
          <th>交易狀態</th>
          <th>市場溫度</th>
          <th>多週期判讀</th>
          <th>情境提醒</th>
          <th>AI策略劇本</th>
          <th>關鍵撐壓 / RR</th>
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

  function normalize(s) {{ return (s || '').toString().toLowerCase().trim(); }}

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
    Path(path).write_text(html, encoding="utf-8")


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
                    "caption": f"📊 AI盤後結構報表已更新 ({_today()})！",
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
        subprocess.run(["git", "commit", "-m", "auto update cache report"], check=True, timeout=20)
        subprocess.run(["git", "push"], check=True, timeout=60)
        print("✅ GitHub 倉儲同步完成")
    except Exception as e:
        print("❌ Git 同步失敗")
        print(e)


def 產生報表():
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

    loader = base.ShioajiSafeLoader(
        api_key=api_key,
        secret_key=secret_key,
        simulation=False,
        ca_path=ca_path,
        ca_password=ca_password,
        person_id=person_id,
    )

    if not loader.login():
        print("❌ 無法建立 Shioaji 連線，自動中斷報表生成。")
        return

    rows = []
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
