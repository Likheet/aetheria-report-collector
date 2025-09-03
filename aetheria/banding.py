from __future__ import annotations
import json, os
_COLORS = {"red":"#e74c3c","yellow":"#f1c40f","blue":"#3498db","green":"#2ecc71","unknown":"#777"}
_CFG = None
def _load():
    global _CFG
    if _CFG is None:
        p = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "bands.json")
        try:
            with open(p, "r", encoding="utf-8") as f:
                _CFG = json.load(f)
        except:
            _CFG = {"default":{"red":[0,49],"yellow":[50,59],"blue":[60,74],"green":[75,100]},"overrides":{}}
    return _CFG
def band_for(key: str, value):
    if value is None: return "unknown", _COLORS["unknown"]
    cfg = _load()
    rng = cfg.get("overrides", {}).get(key) or cfg["default"]
    v = float(value)
    for name in ["red","yellow","blue","green"]:
        lo, hi = rng[name]
        if lo <= v <= hi:
            return name, _COLORS[name]
    return "unknown", _COLORS["unknown"]
