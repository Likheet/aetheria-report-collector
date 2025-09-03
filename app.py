from __future__ import annotations

import json
import os
import ssl
import urllib.parse
import urllib.request
from typing import Any, Dict, Tuple
from urllib.error import HTTPError

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response

# ---------- App constants ----------
APP_TITLE = "Aetheria · Scan Viewer"
VENDOR_ENDPOINT = "https://data.wax-apple.cn/Index/Report/pifu_profes"

# Supabase table/column names (adjust via env if needed)
CUSTOMER_TABLE = os.getenv("CUSTOMER_TABLE", "customer")
CUSTOMER_PHONE_COL = os.getenv("CUSTOMER_PHONE_COL", "phone_e164")
CUSTOMER_NAME_COL  = os.getenv("CUSTOMER_NAME_COL", "full_name")
DEFAULT_COUNTRY_CODE = os.getenv("DEFAULT_COUNTRY_CODE", "91")  # used for E.164 if only 10 digits

# ---------- Optional .env loader ----------
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

# ---------- Utils ----------
def _get_env(k: str) -> str:
    v = os.getenv(k)
    if not v:
        raise RuntimeError(f"missing env {k}")
    return v

def _http_get_json(url: str, params: Dict[str, str]) -> Dict[str, Any]:
    full = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        full,
        method="GET",
        headers={
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "Aetheria/1.0",
            "Referer": "https://report.wax-apple.cn/",
            "Origin": "https://report.wax-apple.cn",
        },
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, context=ctx, timeout=25.0) as r:
        t = r.read().decode("utf-8", errors="replace")
        try:
            return json.loads(t)
        except json.JSONDecodeError:
            s, e = t.find("{"), t.rfind("}")
            if s != -1 and e != -1 and e > s:
                return json.loads(t[s : e + 1])
            raise

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
    raise ValueError("missing id/sign in URL")

def _to_float(x):
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace("%", "")
    try:
        return float(s)
    except Exception:
        return None

def to_e164(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith("+"):
        digits = "".join(ch for ch in raw if ch.isdigit())
        return "+" + digits if digits else None
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return None
    if len(digits) == 10:
        return f"+{DEFAULT_COUNTRY_CODE}{digits}"
    return f"+{digits}"

# ---------- Banding ----------
_COLOR = {"red": "#e74c3c", "yellow": "#f1c40f", "blue": "#3498db", "green": "#2ecc71", "unknown": "#777"}
_DEFAULT_BANDS = {"red": (0, 49), "yellow": (50, 59), "blue": (60, 74), "green": (75, 100)}

def band_for(value: float | None) -> Tuple[str, str]:
    if value is None:
        return "unknown", _COLOR["unknown"]
    v = float(value)
    for name in ("red", "yellow", "blue", "green"):
        lo, hi = _DEFAULT_BANDS[name]
        if lo <= v <= hi:
            return name, _COLOR[name]
    return "unknown", _COLOR["unknown"]

# ---------- Vendor -> internal mapping ----------
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

def normalize_vendor_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "checkid": payload.get("checkid") if isinstance(payload.get("checkid"), int) else None,
        "name": payload.get("name"),
        "phone": payload.get("phone"),
        "skin_age": payload.get("age") if isinstance(payload.get("age"), int) else None,
        "sampling_images": {},
        "metrics": {},
        "raw": payload,
    }
    for s in payload.get("sampling") or []:
        nm, url = (s or {}).get("name"), (s or {}).get("url")
        if nm and url:
            out["sampling_images"][nm] = url
    for m in payload.get("datalist") or []:
        label = (m or {}).get("items")
        key = VENDOR_TO_INTERNAL.get(label)
        if not key:
            continue
        val = _to_float((m or {}).get("value"))
        cloud = _to_float((m or {}).get("cloudvalue"))
        delta = (val - cloud) if (val is not None and cloud is not None) else None
        band, color = band_for(val)
        out["metrics"][key] = {
            "key": key,
            "label": label,
            "value": val,
            "cloudvalue": cloud,
            "delta_from_cloud": delta,
            "vendor_level": ((m or {}).get("level") or "").strip() or None,
            "band": band,
            "color": color,
        }
    return out

def ingest_from_id_sign(id_: str, sign: str) -> Dict[str, Any]:
    vendor = _http_get_json(VENDOR_ENDPOINT, {"id": id_, "sign": sign})
    return normalize_vendor_payload(vendor)

# ---------- Supabase helpers ----------
def _sb_base() -> str:
    return _get_env("SUPABASE_URL").rstrip("/") + "/rest/v1"

def _sb_headers() -> dict:
    key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not key:
        raise RuntimeError("missing env SUPABASE_SERVICE_KEY (or SUPABASE_ANON_KEY)")
    return {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Prefer": "return=representation"}

def _sb_req(method: str, path: str, query: dict | None = None, body: dict | list | None = None):
    url = _sb_base() + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, method=method, headers=_sb_headers(), data=data)
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=25.0) as r:
            raw = r.read().decode("utf-8", errors="replace")
            return None if not raw else json.loads(raw)
    except HTTPError as e:
        err_txt = e.read().decode("utf-8", errors="replace")
        try:
            err_json = json.loads(err_txt)
        except Exception:
            err_json = {"error": err_txt}
        print("Supabase HTTPError", e.code, path, "->", err_json)
        raise HTTPException(status_code=e.code, detail=err_json)

# ---------- FastAPI ----------
app = FastAPI(title=APP_TITLE)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"], allow_credentials=False)

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/ingest")
async def ingest_endpoint(req: Request):
    try:
        body = await req.json()
    except Exception:
        body = {}
    url = (body.get("url") or "").strip()
    id_ = (body.get("id") or "").strip()
    sign = (body.get("sign") or "").strip()
    try:
        input_url = url  # keep the original if present
        if url and (not id_ or not sign):
            id_, sign = parse_id_sign_from_url(url)
        if not id_ or not sign:
            return JSONResponse({"error": "need url or id+sign"}, status_code=400)

        scan = ingest_from_id_sign(id_, sign)

        # NEW: include URL identity for saving
        scan["url_id"] = int(id_) if str(id_).isdigit() else None
        scan["url_sign"] = sign
    # (vendor_url removed) keep url_id/url_sign only

        ph = scan.get("phone")
        if isinstance(ph, str) and len(ph) >= 6:
            scan["phone_masked"] = ph[:2] + "****" + ph[-2:]
        return JSONResponse(scan)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/img")
def proxy_image(u: str):
    try:
        p = urllib.parse.urlparse(u)
        if p.scheme not in ("http", "https"):
            return JSONResponse({"error": "bad url"}, status_code=400)
        req = urllib.request.Request(
            u,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://report.wax-apple.cn/", "Origin": "https://report.wax-apple.cn", "Accept": "image/*"},
        )
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, context=ctx, timeout=25.0) as r:
            data = r.read()
            ct = r.headers.get("Content-Type", "image/jpeg").split(";")[0]
            return Response(content=data, media_type=ct)
    except Exception as e:
        return JSONResponse({"error": f"image fetch failed: {e}"}, status_code=502)

@app.post("/save_to_supabase")
async def save_to_supabase(req: Request):
    try:
        payload = await req.json()
    except Exception:
        payload = {}
    data = payload.get("scan") or payload
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="invalid payload")

    # Require the URL identity
    url_id = data.get("url_id")
    url_sign = (data.get("url_sign") or "").strip()


    if not url_id or not url_sign:
        raise HTTPException(status_code=400, detail="missing url_id/url_sign")

    # Normalize phone to E.164 (default country +91 if 10 digits)
    phone_raw = (data.get("phone") or "").strip()
    phone_e164 = to_e164(phone_raw)
    if not phone_e164:
        raise HTTPException(status_code=400, detail="scan.phone missing or invalid")
    full_name = (data.get("name") or "").strip() or None

    # 1) get-or-create customer by phone_e164
    try:
        found = _sb_req(
            "GET",
            f"/{CUSTOMER_TABLE}",
            {"select": f"id,{CUSTOMER_PHONE_COL},{CUSTOMER_NAME_COL}", CUSTOMER_PHONE_COL: f"eq.{phone_e164}", "limit": "1"},
        )
    except HTTPException as e:
        raise HTTPException(status_code=e.status_code, detail={"stage": "customer_get", "detail": e.detail})

    if found:
        customer_id = found[0]["id"]
        if full_name and (found[0].get(CUSTOMER_NAME_COL) or "") != full_name:
            try:
                _sb_req("PATCH", f"/{CUSTOMER_TABLE}", {CUSTOMER_PHONE_COL: f"eq.{phone_e164}"}, {CUSTOMER_NAME_COL: full_name})
            except HTTPException as e:
                raise HTTPException(status_code=e.status_code, detail={"stage": "customer_update", "detail": e.detail})
    else:
        try:
            ins = _sb_req(
                "POST",
                f"/{CUSTOMER_TABLE}",
                {"select": f"id,{CUSTOMER_PHONE_COL},{CUSTOMER_NAME_COL}"},
                [{CUSTOMER_PHONE_COL: phone_e164, CUSTOMER_NAME_COL: full_name}],
            )
        except HTTPException as e:
            raise HTTPException(status_code=e.status_code, detail={"stage": "customer_insert", "detail": e.detail})
        customer_id = ins[0]["id"]

    # Before creating a session: check if this report already exists and short-circuit
    try:
        existing = _sb_req(
            "GET",
            "/machine_scan",
            {
                "select": "id,session_id",
                "url_id": f"eq.{url_id}",
                "url_sign": f"eq.{url_sign}",
                "limit": "1",
            },
        )
    except HTTPException as e:
        raise HTTPException(status_code=e.status_code, detail={"stage": "scan_lookup", "detail": e.detail})

    if existing:
        return {"ok": True, "duplicate": True, "scan_id": existing[0]["id"], "session_id": existing[0]["session_id"]}

    # 2) create assessment_session
    try:
        sess = _sb_req(
            "POST",
            "/assessment_session",
            {"select": "id,customer_id,created_at"},
            [{"customer_id": customer_id}],
        )
    except HTTPException as e:
        raise HTTPException(status_code=e.status_code, detail={"stage": "session_insert", "detail": e.detail})
    session_id = sess[0]["id"]

    # 3) insert machine_scan (trigger fills machine_analysis)
    # 3) insert machine_scan (idempotent on vendor pair)
    try:
        scan_row = _sb_req(
            "POST",
            "/machine_scan",
            {
                "select": "id,session_id,created_at",
                "on_conflict": "url_id,url_sign",
            },
            [
                {
                    "session_id": session_id,
                    "checkid": data.get("checkid"),
                    "url_id": url_id,
                    "url_sign": url_sign,

                    "customer_name": full_name,
                    "customer_phone": phone_e164,  # your machine_scan has text; storing E.164 is fine
                    "skin_age": data.get("skin_age") or data.get("age"),

                    "sampling_images": data.get("sampling_images"),
                    "metrics": data.get("metrics"),
                    "raw_report": data.get("raw") or data,
                }
            ],
        )
    except HTTPException as e:
        raise HTTPException(status_code=e.status_code, detail={"stage": "scan_insert", "detail": e.detail})
    scan_id = scan_row[0]["id"]

    return {"ok": True, "customer_id": customer_id, "session_id": session_id, "scan_id": scan_id}

# ---------- Minimal UI ----------
HTML_TEMPLATE = r"""
<!doctype html><html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>__APP_TITLE__</title>
<style>
:root{--bg:#0f1115;--panel:#141820;--ink:#e9edf1;--muted:#9aa3af;--accent:#c7a770;--card:#161b22;--border:#252a34}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Arial,"Noto Sans","Helvetica Neue",sans-serif}
.wrap{max-width:1100px;margin:28px auto;padding:0 16px}
.panel{background:#141820;border:1px solid var(--border);border-radius:12px;padding:14px}
.row{display:grid;grid-template-columns:1fr auto;gap:10px}
.subrow{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:8px}
input[type=text]{background:#0c0f14;border:1px solid #222834;color:var(--ink);padding:10px 12px;border-radius:10px;outline:none}
button{background:var(--accent);color:#0d0f14;border:none;padding:10px 14px;border-radius:10px;font-weight:600;cursor:pointer}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px;margin-top:14px}
.imgs{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
.imgs img{width:100%;height:220px;object-fit:cover;border-radius:10px;border:1px solid var(--border)}
.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.metric{padding:12px;border:1px solid var(--border);border-radius:12px;background:#12161d}
.metric .head{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px}
.bar{position:relative;height:10px;background:#0d1117;border-radius:999px;overflow:hidden;border:1px solid #1f2630}
.fill{height:100%;width:0%;background:#444}
.avg{position:absolute;top:-3px;width:2px;height:16px;background:#e9edf1;opacity:.6;border-radius:1px}
.pill{display:inline-block;font-size:11px;padding:3px 8px;border-radius:999px;border:1px solid var(--border);color:#cbd5e1}
pre{background:#0b0f14;color:#cfe3ff;border:1px solid #1b2230;border-radius:10px;padding:12px;overflow:auto}
</style></head><body>
<div class="wrap">
  <h2>__APP_TITLE__</h2>
  <div class="panel">
    <div class="row">
      <input id="url" type="text" placeholder="Paste full report URL (#/Report/newPifu_play?id=...&sign=...)">
      <div style="display:flex;gap:8px;">
        <button id="loadBtn">Load Report</button>
        <button id="saveBtn" disabled>Save to Supabase</button>
      </div>
    </div>
    <div class="subrow">
      <input id="id" type="text" placeholder="…or enter id">
      <input id="sign" type="text" placeholder="…and sign">
    </div>
  </div>

  <div id="content" style="display:none;">
        <div class="card">
                <div><strong id="name">—</strong></div>
                <div style="color:var(--muted)">Skin age: <span id="age">—</span> · Phone: <span id="phone">—</span> · Check ID: <span id="checkid">—</span></div>
                <div class="imgs" id="imgs"></div>
            </div>
    <div class="card"><div class="metrics" id="metrics"></div></div>
    <div class="card"><pre id="jsonOut">// JSON</pre></div>
  </div>
</div>

<script>
const $=s=>document.querySelector(s); let lastData=null;

function mask(p){if(!p)return"—";const s=String(p);return s.length<6?s:(s.slice(0,2)+"****"+s.slice(-2))}
function metricCard(m){
  const v=(typeof m.value==="number"&&isFinite(m.value))?Math.max(0,Math.min(100,m.value)):null;
  const c=(typeof m.cloudvalue==="number"&&isFinite(m.cloudvalue))?Math.max(0,Math.min(100,m.cloudvalue)):null;
  const el=document.createElement("div"); el.className="metric";
  el.innerHTML=`<div class="head"><div>${m.label||m.key}</div><div class="pill" style="border-color:${m.color}33;color:${m.color}">${m.band||"?"}</div></div>
  <div style="display:flex;gap:14px;align-items:center;margin-bottom:6px;">
    <div>${v===null?"—":v.toFixed(0)}/100</div>
    <div style="color:#9aa3af">avg: <strong>${c===null?"—":c.toFixed(1)}</strong></div>
    <div style="color:#9aa3af">Δ: <strong>${typeof m.delta_from_cloud==="number"?m.delta_from_cloud.toFixed(1):"—"}</strong></div>
  </div>
  <div class="bar">
    <div class="fill" style="width:${v||0}%;background:${m.color||"#555"}"></div>
    ${c===null?"":`<div class="avg" style="left:${c}%"></div>`}
  </div>`;
  return el;
}

async function ingest(){
  const url=$("#url").value.trim(), id=$("#id").value.trim(), sign=$("#sign").value.trim();
  const payload={}; if(url)payload.url=url; if(id&&sign){payload.id=id;payload.sign=sign;}
  if(!payload.url && !(payload.id&&payload.sign)){alert("Provide url or id+sign");return;}
  const r=await fetch("/ingest",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
  const data=await r.json(); if(!r.ok){alert(data.error||"error");return;}
  lastData=data; $("#content").style.display="block";
    $("#name").textContent=data.name||"—";
    $("#age").textContent=(typeof data.skin_age === "number")?data.skin_age:(typeof data.age === "number"?data.age:"—");
  $("#phone").textContent=mask(data.phone_masked||data.phone);
  $("#checkid").textContent=data.checkid??"—";

  const imgs=$("#imgs"); imgs.innerHTML=""; const sm=data.sampling_images||{};
  Object.keys(sm).forEach(k=>{const f=document.createElement("figure");const i=document.createElement("img");i.alt=k;i.src=sm[k];i.referrerPolicy="no-referrer";i.onerror=()=>{i.src="/img?u="+encodeURIComponent(sm[k])};f.appendChild(i);imgs.appendChild(f);});

  const mm=data.metrics||{}; const wrap=$("#metrics"); wrap.innerHTML="";
  Object.keys(mm).sort().forEach(k=>wrap.appendChild(metricCard(mm[k])));

  $("#jsonOut").textContent=JSON.stringify(data,null,2);
  $("#saveBtn").disabled=false;
}

async function saveToSupabase(){
  if(!lastData){alert("Load a report first");return;}
  const r=await fetch("/save_to_supabase",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({scan:lastData})});
  const j=await r.json();
  if(!r.ok){
    const msg = typeof j.detail === 'string' ? j.detail : (j.detail ? JSON.stringify(j.detail,null,2) : (j.error||"Save failed"));
    alert(msg);
    return;
  }
  alert("Saved!\\ncustomer_id: "+j.customer_id+"\\nsession_id: "+j.session_id+"\\nscan_id: "+j.scan_id);
}

$("#loadBtn").addEventListener("click",ingest);
$("#saveBtn").addEventListener("click",saveToSupabase);
</script>
</body></html>
"""

@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(HTML_TEMPLATE.replace("__APP_TITLE__", APP_TITLE))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
