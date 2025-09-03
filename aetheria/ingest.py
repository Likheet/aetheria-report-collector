from __future__ import annotations
import ssl, json, urllib.parse, urllib.request
from typing import Any, Dict, Tuple
from .models import MachineScan, Metric

VENDOR_ENDPOINT = "https://data.wax-apple.cn/Index/Report/pifu_profes"
VENDOR_TO_INTERNAL = {
    "RGB Moisture": "moisture",
    "RGB Grease": "sebum",
    "PL Texture": "texture",
    "UV Pigmentation": "pigmentation_uv",
    "PL Hyperemia": "redness",
    "UV Pore": "pores",
    "UV Acne": "acne",
    "UV spot": "uv_spots",
    "Brown area": "brown_areas",
    "Sensitive Area": "sensitivity",
}

def parse_id_sign_from_url(report_url: str) -> Tuple[str, str]:
    u = urllib.parse.urlparse(report_url)
    qs = urllib.parse.parse_qs(u.query)
    if "id" in qs and "sign" in qs:
        return qs["id"][0], qs["sign"][0]
    frag = u.fragment or ""
    if "?" in frag:
        fqs = urllib.parse.parse_qs(frag.split("?", 1)[1])
        if "id" in fqs and "sign" in fqs:
            return fqs["id"][0], fqs["sign"][0]
    raise ValueError("missing id/sign")

def _get_json(url: str, params: Dict[str, str]) -> Dict[str, Any]:
    full = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(full, method="GET", headers={
        "Accept":"application/json, text/plain, */*",
        "User-Agent":"Aetheria/1.0",
        "Referer":"https://report.wax-apple.cn/",
        "Origin":"https://report.wax-apple.cn"
    })
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, context=ctx, timeout=20.0) as r:
        t = r.read().decode("utf-8", errors="replace")
        try:
            return json.loads(t)
        except json.JSONDecodeError:
            s, e = t.find("{"), t.rfind("}")
            if s != -1 and e != -1 and e > s:
                return json.loads(t[s:e+1])
            raise

def _to_float(x):
    if x is None: return None
    if isinstance(x,(int,float)): return float(x)
    s = str(x).strip().replace("%","")
    try: return float(s)
    except: return None

def normalize_machine_payload(payload: Dict[str, Any]) -> MachineScan:
    out = MachineScan(checkid=payload.get("checkid") if isinstance(payload.get("checkid"),int) else None,
                      name=payload.get("name"), phone=payload.get("phone"),
                      age=payload.get("age") if isinstance(payload.get("age"),int) else None,
                      sampling_images={}, metrics={}, raw=payload)
    for s in payload.get("sampling") or []:
        nm, url = (s or {}).get("name"), (s or {}).get("url")
        if nm and url:
            out.sampling_images[nm] = url
    for m in payload.get("datalist") or []:
        label = (m or {}).get("items")
        key = VENDOR_TO_INTERNAL.get(label)
        if not key: continue
        val = _to_float((m or {}).get("value"))
        cloud = _to_float((m or {}).get("cloudvalue"))
        d = (val - cloud) if (val is not None and cloud is not None) else None
        out.metrics[key] = Metric(key=key, label=label, value=val, cloudvalue=cloud,
                                  delta_from_cloud=d, vendor_level=((m or {}).get("level") or "").strip() or None)
    return out

def ingest_from_id_sign(id_: str, sign: str) -> MachineScan:
    payload = _get_json(VENDOR_ENDPOINT, {"id": id_, "sign": sign})
    return normalize_machine_payload(payload)
