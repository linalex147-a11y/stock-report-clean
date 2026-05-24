
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd


def _to_dt(value: Any) -> pd.Timestamp:
    if value is None:
        return pd.Timestamp.utcnow().normalize()
    return pd.to_datetime(value)


def _normalize_bars(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    out = df.copy()

    if "ts" in out.columns:
        out["ts"] = pd.to_datetime(out["ts"], errors="coerce")
        out = out.set_index("ts")
    else:
        out.index = pd.to_datetime(out.index, errors="coerce")

    rename_map = {}
    for src, dst in [("Open", "open"), ("High", "high"), ("Low", "low"), ("Close", "close"), ("Volume", "volume"),
                     ("open", "open"), ("high", "high"), ("low", "low"), ("close", "close"), ("volume", "volume")]:
        if src in out.columns and src != dst:
            rename_map[src] = dst
    if rename_map:
        out = out.rename(columns=rename_map)

    keep_cols = [c for c in ["open", "high", "low", "close", "volume"] if c in out.columns]
    out = out[keep_cols].copy()

    for col in keep_cols:
        out[col] = pd.to_numeric(out[col], errors="coerce")

    out = out.dropna()
    out = out[~out.index.isna()]
    out = out.sort_index()
    out = out[~out.index.duplicated(keep="last")]
    out.index.name = "ts"
    return out


@dataclass
class CacheInfo:
    source: str
    path: str
    updated: bool
    rows: int
    last_ts: Optional[str] = None
    fetched_start: Optional[str] = None
    fetched_end: Optional[str] = None


class MarketCache:
    """股票K棒快取：第一次抓完整區間，之後只抓缺的區間並與快取合併。"""

    def __init__(self, root_dir: str = "tick_cache", overlap_days: int = 1):
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.overlap_days = max(0, int(overlap_days))
        self.meta_path = self.root_dir / "_meta.json"
        self._meta = self._load_meta()

    def _load_meta(self) -> Dict[str, Any]:
        if self.meta_path.exists():
            try:
                return json.loads(self.meta_path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_meta(self) -> None:
        try:
            self.meta_path.write_text(json.dumps(self._meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _path(self, symbol: str) -> Path:
        return self.root_dir / f"{symbol}.parquet"

    def load(self, symbol: str) -> Optional[pd.DataFrame]:
        path = self._path(symbol)
        if not path.exists():
            return None
        try:
            df = pd.read_parquet(path)
            return _normalize_bars(df)
        except Exception:
            try:
                df = pd.read_pickle(path)
                return _normalize_bars(df)
            except Exception:
                return None

    def save(self, symbol: str, df: pd.DataFrame, source: str = "cache") -> None:
        path = self._path(symbol)
        df = _normalize_bars(df)
        if len(df) == 0:
            return
        try:
            df.to_parquet(path)
        except Exception:
            df.to_pickle(path)

        last_ts = str(df.index[-1]) if len(df) else None
        self._meta[symbol] = {
            "source": source,
            "last_ts": last_ts,
            "rows": int(len(df)),
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._save_meta()

    def load_or_update(
        self,
        symbol: str,
        loader,
        start: Any,
        end: Any,
        force_refresh_today: bool = False,
    ) -> Tuple[Optional[pd.DataFrame], CacheInfo]:
        """回傳 (df, info)。若已有快取，只補抓重疊區間與缺漏區間。"""
        start_dt = _to_dt(start)
        end_dt = _to_dt(end)

        cached = self.load(symbol)
        path = str(self._path(symbol))

        if cached is None or len(cached) == 0:
            df = self._fetch(loader, symbol, start_dt, end_dt)
            if df is None or len(df) == 0:
                return None, CacheInfo("miss", path, False, 0)
            self.save(symbol, df, source="fresh")
            return df, CacheInfo("fresh", path, True, len(df), str(df.index[-1]), str(start_dt), str(end_dt))

        last_ts = cached.index[-1]
        last_day = pd.Timestamp(last_ts).normalize()
        end_day = end_dt.normalize()

        # 如果快取已經涵蓋今天，預設直接回傳；必要時可強制刷新今天。
        if last_day >= end_day and not force_refresh_today:
            meta = self._meta.get(symbol, {})
            return cached, CacheInfo(
                "cache",
                path,
                False,
                int(len(cached)),
                str(last_ts),
                None,
                None,
            )

        fetch_start = max(start_dt, last_ts.normalize() - timedelta(days=self.overlap_days))
        fetch_end = end_dt

        new_df = self._fetch(loader, symbol, fetch_start, fetch_end)
        if new_df is None or len(new_df) == 0:
            # 抓不到新資料就先回傳快取，避免整體失敗
            meta = self._meta.get(symbol, {})
            return cached, CacheInfo(
                "cache",
                path,
                False,
                int(len(cached)),
                str(last_ts),
                str(fetch_start),
                str(fetch_end),
            )

        merged = pd.concat([cached, new_df], axis=0)
        merged = _normalize_bars(merged)
        if len(merged) == 0:
            return cached, CacheInfo("cache", path, False, int(len(cached)), str(last_ts), str(fetch_start), str(fetch_end))

        self.save(symbol, merged, source="incremental")
        return merged, CacheInfo("incremental", path, True, int(len(merged)), str(merged.index[-1]), str(fetch_start), str(fetch_end))

    @staticmethod
    def _fetch(loader, symbol: str, start_dt: pd.Timestamp, end_dt: pd.Timestamp) -> Optional[pd.DataFrame]:
        try:
            start = start_dt.strftime("%Y-%m-%d")
            end = end_dt.strftime("%Y-%m-%d")
            df = loader.fetch_kbars(symbol, start, end)
            if df is None or len(df) == 0:
                return None
            return _normalize_bars(df)
        except Exception:
            return None
