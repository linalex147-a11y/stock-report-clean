from __future__ import annotations

import contextlib
import importlib
import io
from typing import Any

BASE_REPORT_MODULE = "report_v9"
with contextlib.redirect_stdout(io.StringIO()):
    _base = importlib.import_module(BASE_REPORT_MODULE)

_config_module = None
try:
    _config_module = importlib.import_module("report_config_tick_v1")
except ModuleNotFoundError:
    _config_module = importlib.import_module("report_config")

globals()["\u5831\u8868\u8a2d\u5b9a"] = getattr(_config_module, "\u5831\u8868\u8a2d\u5b9a")
ShioajiSafeLoader = getattr(importlib.import_module("shioaji_loader"), "ShioajiSafeLoader")

_REQUIRED_BASE_MEMBERS = [
    '_AI\u5287\u672c',
    '_add_ma',
    '_ma',
    '_resample_30',
    '_resample_day',
    '_\u4e3b\u529b\u75d5\u8de1',
    '_\u50f9\u683c\u5217\u8868',
    '_\u524d\u9ad8\u524d\u4f4e',
    '_\u58d3\u529b\u652f\u6490\u6587\u5b57',
    '_\u5927\u91cfK_high_low',
    '_\u5e02\u5834\u4f4d\u968e',
    '_\u5e73\u53f0',
    '_\u65b9\u5411',
    '_\u7bc0\u594f',
    '_\u7d50\u69cb\u6392\u5e8f',
    '_\u7d50\u69cb\u72c0\u614b',
    '_\u8da8\u52e2\u5f37\u5ea6',
    '_\u91cf\u50f9',
    '_\u98a8\u5831\u6bd4',
    '_\u9ad8\u4f4e\u7d50\u69cb',
    '\u5831\u8868\u8a2d\u5b9a',
]

_missing = [name for name in _REQUIRED_BASE_MEMBERS if not hasattr(_base, name)]
if _missing:
    raise ImportError(f"Base report module {BASE_REPORT_MODULE!r} missing required members: {', '.join(_missing)}")

for _name in _REQUIRED_BASE_MEMBERS:
    globals()[_name] = getattr(_base, _name)

__all__ = [
    "BASE_REPORT_MODULE",
    "\u5831\u8868\u8a2d\u5b9a",
    "ShioajiSafeLoader",
    *_REQUIRED_BASE_MEMBERS,
]

def base_module_path() -> str:
    return str(getattr(_base, "__file__", ""))

def __getattr__(name: str) -> Any:
    return getattr(_base, name)
