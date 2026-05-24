from __future__ import annotations

import time
import pandas as pd
import shioaji as sj
from typing import Optional

# 安全導入 Resolution：相容所有新舊版本 Shioaji 的 Fallback 機制
try:
    from shioaji import Resolution
except ImportError:
    try:
        from shioaji.constant import Resolution
    except ImportError:
        # 如果所有原生導入口都失效，建立相容的模擬類別
        class Resolution:
            MIN1 = "1Min"
            MIN5 = "5Min"
            DAILY = "1Day"

class ShioajiSafeLoader:
    """
    永豐 Shioaji API 安全資料加載器
    內建自動頻率限制、自動登入登出維護、憑證安全啟用、重試機制與連線數管理
    """
    def __init__(
        self, 
        api_key: str, 
        secret_key: str, 
        simulation: bool = False,
        ca_path: Optional[str] = None,
        ca_password: Optional[str] = None,
        person_id: Optional[str] = None
    ):
        self.api_key = api_key.strip()
        self.secret_key = secret_key.strip()
        self.simulation = simulation
        
        # 憑證安全參數
        self.ca_path = ca_path.strip() if ca_path else None
        self.ca_password = ca_password.strip() if ca_password else None
        self.person_id = person_id.strip() if person_id else None
        
        self.api: Optional[sj.Shioaji] = None
        
        # 頻率控制器設定 (5秒50次限制，每秒最多 8 次，或間隔不低於 0.15 秒)
        self.last_request_time = 0.0
        self.min_interval = 0.15  # 秒
        
    def login(self) -> bool:
        """安全登入，若已有連線則複用，並於登入成功後自動啟用 CA 憑證"""
        if self.api is not None:
            return True
        
        try:
            print("🔑 正在建立 Shioaji 安全連線...")
            self.api = sj.Shioaji()
            self.api.login(
                api_key=self.api_key,
                secret_key=self.secret_key,
                contracts_cb=lambda percentage: None # 靜音載入商品合約，避免終端洗版
            )
            print("✅ Shioaji 登入成功！")
            
            # 自動啟用憑證 (憑證路徑、密碼與 ID 皆齊備時啟用)
            if self.ca_path and self.ca_password and self.person_id:
                try:
                    print("🪪 偵測到憑證設定，正在啟用安全憑證...")
                    self.api.activate_ca(
                        ca_path=self.ca_path,
                        ca_passwd=self.ca_password,
                        person_id=self.person_id
                    )
                    print("✅ 永豐憑證安全啟用完成！")
                except Exception as ca_err:
                    print(f"⚠️ 憑證啟用失敗（僅查詢歷史行情時通常不受影響）: {ca_err}")
            
            return True
        except Exception as e:
            print(f"❌ Shioaji 登入失敗: {e}")
            self.api = None
            return False

    def logout(self):
        """主動釋放連線資源，避免佔用連線數"""
        if self.api is not None:
            try:
                self.api.logout()
                print("🚪 Shioaji 連線已安全中斷並登出。")
            except Exception as e:
                print(f"⚠️ Shioaji 登出時發生異常: {e}")
            finally:
                self.api = None

    def _wait_for_rate_limit(self):
        """強制遵守 5 秒 50 次的行情查詢限制"""
        now = time.time()
        elapsed = now - self.last_request_time
        if elapsed < self.min_interval:
            sleep_time = self.min_interval - elapsed
            time.sleep(sleep_time)
        self.last_request_time = time.time()

    def fetch_kbars(
        self, 
        symbol: str, 
        start_date: str, 
        end_date: str, 
        retry_limit: int = 3
    ) -> Optional[pd.DataFrame]:
        """
        安全獲取 K 線資料（自動相容新舊版本 API 並統一輸出為 5 分鐘 K 線）。
        
        Args:
            symbol: 股票代碼 (例如 "2330")
            start_date: 開始日期 (YYYY-MM-DD)
            end_date: 結束日期 (YYYY-MM-DD)
            retry_limit: 遇到超限或失敗時的最大重試次數
            
        Returns:
            標準化的 pandas DataFrame 或 None
        """
        if not self.login() or self.api is None:
            return None

        # 1. 取得合約資訊
        contract = self.api.Contracts.Stocks.get(symbol)
        if not contract:
            try:
                self._wait_for_rate_limit()
                contract = self.api.Contracts.Stocks[symbol]
            except Exception:
                print(f"  {symbol} ❌ 找不到股票合約，請確認代碼是否正確。")
                return None

        # 2. 執行帶有指數退避重試的 K 線拉取
        backoff_delay = 2.0  # 基礎等待秒數
        
        for attempt in range(1, retry_limit + 1):
            try:
                # 行情查詢前，強制過濾頻率
                self._wait_for_rate_limit()
                
                # 🟢 相容性安全呼叫機制
                try:
                    # 首先嘗試使用新版 resolution=Resolution.MIN5 呼叫
                    kbars = self.api.kbars(
                        contract=contract,
                        start=start_date,
                        end=end_date,
                        resolution=Resolution.MIN5
                    )
                except TypeError as te:
                    # 如果是因為舊版 Shioaji 不支援 resolution 關鍵字而報錯
                    if "resolution" in str(te) or "unexpected keyword argument" in str(te):
                        # 舊版 Fallback：不帶 resolution 參數直接拉取（預設會拉回 1 分鐘 K 線）
                        kbars = self.api.kbars(
                            contract=contract,
                            start=start_date,
                            end=end_date
                        )
                    else:
                        raise te
                
                df = pd.DataFrame({**kbars})
                if df.empty:
                    raise ValueError("API 回傳空資料 (可能觸發當日流量限制或該時段無交易)")
                
                # 轉換索引為時間
                df["ts"] = pd.to_datetime(df["ts"])
                df = df.set_index("ts")
                
                # 回傳標準化 OHLCV
                df = df.rename(columns={
                    "Open": "open",
                    "High": "high",
                    "Low": "low",
                    "Close": "close",
                    "Volume": "volume"
                })
                
                # 轉換型態
                for col in ["open", "high", "low", "close", "volume"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                
                df = df.dropna()
                
                # 🟢 終極相容重採樣：不論 API 回傳 1 分鐘還是 5 分鐘，統一 Resample 成標準 5 分鐘 K 線！
                df = df.resample("5min", closed="left", label="left").agg({
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                }).dropna()
                
                return df

            except Exception as e:
                print(f"  ⚠️ {symbol} 抓取 K 線失敗 (嘗試 {attempt}/{retry_limit}): {e}")
                if attempt < retry_limit:
                    actual_delay = backoff_delay * (2 ** (attempt - 1))
                    if "Too Many Requests" in str(e) or "空資料" in str(e):
                        actual_delay = max(actual_delay, 15.0) # 行情被限制時，提高等待門檻
                    print(f"  ⏳ 等待 {actual_delay} 秒後重試...")
                    time.sleep(actual_delay)
                else:
                    print(f"  ❌ {symbol} 達到重試上限，放棄本次抓取。")
                    
        return None
