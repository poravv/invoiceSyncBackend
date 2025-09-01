from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, Optional

from app.config.settings import settings

_LOCK = threading.Lock()
_PREFS_PATH = os.path.join(settings.EXCEL_OUTPUT_DIR if settings.EXCEL_OUTPUT_DIR else "/app/data", "prefs.json")


def _ensure_dir():
    d = os.path.dirname(_PREFS_PATH)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass


def _read_all() -> Dict[str, Any]:
    _ensure_dir()
    if not os.path.exists(_PREFS_PATH):
        return {}
    try:
        with open(_PREFS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            return {}
    except Exception:
        return {}


def _write_all(data: Dict[str, Any]) -> None:
    _ensure_dir()
    tmp = _PREFS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, _PREFS_PATH)


def get_auto_refresh(uid: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        data = _read_all()
        node = data.get("auto_refresh", {}) if isinstance(data, dict) else {}
        return node.get(uid)


def set_auto_refresh(uid: str, enabled: bool, interval_ms: int) -> Dict[str, Any]:
    interval_ms = max(5000, int(interval_ms or 30000))
    with _LOCK:
        data = _read_all()
        auto = data.get("auto_refresh")
        if not isinstance(auto, dict):
            auto = {}
        auto[uid] = {"enabled": bool(enabled), "interval_ms": interval_ms}
        data["auto_refresh"] = auto
        _write_all(data)
    return auto[uid]

