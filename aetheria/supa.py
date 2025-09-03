from __future__ import annotations
import os, json, urllib.request, urllib.parse, ssl
from typing import Any, Dict, List, Optional

def _env(k): 
    v = os.getenv(k)
    if not v: raise RuntimeError(f"missing env {k}")
    return v

def _base():
    return _env("SUPABASE_URL").rstrip("/") + "/rest/v1"

def _hdr():
    k = _env("SUPABASE_ANON_KEY")
    return {"apikey": k, "Authorization": f"Bearer {k}", "Content-Type":"application/json", "Prefer":"return=representation"}

def _req(method: str, path: str, query: Dict[str,str]|None=None, body: Any|None=None):
    url = _base() + path
    if query: url += "?" + urllib.parse.urlencode(query)
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, method=method, headers=_hdr(), data=data)
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, context=ctx, timeout=20.0) as r:
        t = r.read().decode("utf-8", errors="replace")
        if not t: return None
        return json.loads(t)

def _norm_phone(s: str|None) -> str|None:
    if not s: return None
    return "".join(ch for ch in str(s) if ch.isdigit()) or None

def upsert_customer(name: str|None, phone: str|None) -> str:
    ph = _norm_phone(phone)
    if not ph: raise RuntimeError("phone required")
    body = [{"phone": ph, "name": name or None}]
    res = _req("POST", "/customers", {"on_conflict":"phone","select":"id,phone,name"}, body)
    return res[0]["id"]

def insert_machine_scan(customer_id: str, scan: Dict) -> str:
    body = [{
        "customer_id": customer_id,
        "vendor_checkid": scan.get("checkid"),
        "age": scan.get("age"),
        "metrics": scan.get("metrics"),
        "sampling_images": scan.get("sampling_images"),
        "raw": scan.get("raw")
    }]
    res = _req("POST", "/machine_scans", {"select":"id"}, body)
    return res[0]["id"]

def list_customers(limit: int = 50) -> List[Dict]:
    return _req("GET", "/customers", {"select":"id,name,phone,created_at","order":"created_at.desc","limit":str(limit)})

def latest_scan_for_customer(customer_id: str) -> Optional[Dict]:
    res = _req("GET", "/machine_scans", {"select":"*","customer_id":f"eq.{customer_id}","order":"created_at.desc","limit":"1"})
    return res[0] if res else None
