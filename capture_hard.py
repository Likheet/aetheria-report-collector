# capture_ultra.py
# Usage:
#   python capture_ultra.py "https://report.wax-apple.cn/#/Report/newPifu_play?id=...&sign=..."
#
# Outputs in ./captures/:
#   - summary.json
#   - xhr-*.json            (JSON-like payloads from network + hooks)
#   - chart-*.json          (chart configs if any lib used)
#   - canvas-text.json      (all text drawn onto canvas: numbers, ticks, labels)
#   - storage-local.json    (localStorage dump)
#   - storage-session.json  (sessionStorage dump)
#   - page.html, screenshot.png
#   - trace.zip             (open with: playwright show-trace captures/trace.zip)

import os
import sys
import json
import time
from pathlib import Path
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else None
if not URL:
    print("Provide the report URL as an argument.")
    sys.exit(1)

OUT = Path("captures")
OUT.mkdir(parents=True, exist_ok=True)

HEADLESS = os.getenv("HEADLESS", "0") in ("1", "true", "True")

def now_iso():
    return datetime.now(timezone.utc).isoformat()

INJECT_JS = r"""
// Shared bucket accessible from any frame
window.__CAPTURE__ = window.__CAPTURE__ || { json: [], charts: [], canvasText: [], storage: { local: {}, session: {} } };

// --- Hook fetch ---
(() => {
  const _fetch = window.fetch;
  if (!_fetch) return;
  window.fetch = async function(...args) {
    const res = await _fetch.apply(this, args);
    try {
      const ct = (res.headers.get('content-type') || '').toLowerCase();
      if (ct.includes('application/json')) {
        const cloned = res.clone();
        cloned.json().then(body => {
          try { window.__CAPTURE__.json.push({ src: 'fetch', url: res.url, body }); } catch(e){}
        }).catch(()=>{});
      } else {
        // Sometimes JSON is returned as text/html; still try to parse
        const cloned = res.clone();
        cloned.text().then(txt => {
          if (txt && txt.includes('{') && txt.includes('}')) {
            try {
              const guess = JSON.parse(txt);
              window.__CAPTURE__.json.push({ src: 'fetch-text', url: res.url, body: guess });
            } catch(e) { /* ignore */ }
          }
        }).catch(()=>{});
      }
    } catch(e) {}
    return res;
  };
})();

// --- Hook XHR ---
(() => {
  const open = XMLHttpRequest.prototype.open;
  const send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(...args) { this.__url = args[1]; return open.apply(this, args); };
  XMLHttpRequest.prototype.send = function(...args) {
    this.addEventListener('load', function() {
      try {
        const ct = (this.getResponseHeader('content-type') || '').toLowerCase();
        const url = this.__url || '';
        if (ct.includes('application/json')) {
          try { window.__CAPTURE__.json.push({ src: 'xhr', url, body: JSON.parse(this.responseText) }); } catch(e){}
        } else {
          const txt = this.responseText || '';
          if (/\{[\s\S]*\}/.test(txt)) {
            try {
              const guess = JSON.parse(txt);
              window.__CAPTURE__.json.push({ src: 'xhr-text', url, body: guess });
            } catch(e) { /* ignore */ }
          }
        }
      } catch(e) {}
    });
    return send.apply(this, args);
  };
})();

// --- Hook JSON.parse (often used to parse embedded JSON strings) ---
(() => {
  const _parse = JSON.parse;
  JSON.parse = function(s, reviver) {
    const out = _parse.call(this, s, reviver);
    try {
      if (out && typeof out === 'object') {
        window.__CAPTURE__.json.push({ src: 'JSON.parse', url: 'inline', body: out });
      }
    } catch(e){}
    return out;
  };
})();

// --- Hook ECharts (if they use it) ---
(() => {
  let REF;
  Object.defineProperty(window, 'echarts', {
    configurable: true,
    get(){ return REF; },
    set(v){
      REF = v;
      try {
        const oinit = v.init;
        v.init = function(...a){
          const inst = oinit.apply(this, a);
          const oset = inst.setOption;
          inst.setOption = function(opts, ...rest){
            try { window.__CAPTURE__.charts.push({ lib: 'echarts', opts }); } catch(e){}
            return oset.call(this, opts, ...rest);
          };
          return inst;
        };
      } catch(e){}
    }
  });
})();

// --- Hook Chart.js (best-effort) ---
(() => {
  let ChartREF;
  Object.defineProperty(window, 'Chart', {
    configurable: true,
    get(){ return ChartREF; },
    set(v){
      ChartREF = v;
      try {
        const ProxyChart = function(...args){
          try { window.__CAPTURE__.charts.push({ lib: 'chartjs', cfg: args[1] }); } catch(e){}
          return Reflect.construct(v, args, new.target);
        };
        Object.setPrototypeOf(ProxyChart, v);
        ProxyChart.prototype = v.prototype;
        window.Chart = ProxyChart;
      } catch(e){}
    }
  });
})();

// --- Hook Canvas text (captures numbers drawn on charts) ---
(() => {
  const patchCtx = (proto) => {
    if (!proto) return;
    const _fillText = proto.fillText;
    const _strokeText = proto.strokeText;
    proto.fillText = function(text, x, y, ...rest) {
      try { window.__CAPTURE__.canvasText.push({ kind:'fill', text:String(text), x, y, font:this.font, fillStyle:this.fillStyle }); } catch(e){}
      return _fillText.apply(this, arguments);
    };
    proto.strokeText = function(text, x, y, ...rest) {
      try { window.__CAPTURE__.canvasText.push({ kind:'stroke', text:String(text), x, y, font:this.font, strokeStyle:this.strokeStyle }); } catch(e){}
      return _strokeText.apply(this, arguments);
    };
  };
  try { patchCtx(CanvasRenderingContext2D.prototype); } catch(e){}
  try { patchCtx(OffscreenCanvasRenderingContext2D.prototype); } catch(e){}
})();

// --- Observe storage writes; we also snapshot later ---
(() => {
  const _ls_set = Storage.prototype.setItem;
  Storage.prototype.setItem = function(k,v){
    try {
      const isLocal = (this === window.localStorage);
      if (isLocal) window.__CAPTURE__.storage.local[k] = String(v);
      else window.__CAPTURE__.storage.session[k] = String(v);
    } catch(e){}
    return _ls_set.apply(this, arguments);
  };
})();
"""

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, slow_mo=80 if not HEADLESS else 0)
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(OUT / "profile"),  # keeps cookies if needed
            headless=HEADLESS,
            ignore_https_errors=True,
            locale="zh-CN",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
            viewport={"width": 1366, "height": 900},
        )

        # Add hooks for every page/frame created
        ctx.add_init_script(INJECT_JS)

        ctx.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = ctx.new_page()

        # Request log (debug)
        req_log = []
        def _on_request(r):
            try:
                req_log.append({
                    "time": now_iso(),
                    "method": r.method,      # property
                    "url": r.url,
                    "type": r.resource_type, # property
                })
            except Exception:
                pass
        page.on("request", _on_request)

        # Network-side JSON capture too
        json_payloads = []
        def on_response(res):
            url = res.url
            ct = (res.headers.get("content-type") or "").lower()
            try:
                if "application/json" in ct:
                    body = res.json()
                    json_payloads.append({"src": "network", "url": url, "body": body})
                elif url.lower().endswith(".json") or ".json?" in url.lower():
                    text = res.text()
                    try:
                        body = json.loads(text)
                    except Exception:
                        body = text
                    json_payloads.append({"src": "network-text", "url": url, "body": body})
            except Exception:
                pass
        page.on("response", on_response)

        t0 = time.time()
        page.goto(URL, wait_until="networkidle", timeout=120_000)
        print("Loaded:", page.url)

        # Gentle pulses to trigger late rendering
        for _ in range(8):
            time.sleep(1.25)
            try:
                page.evaluate("window.scrollBy(0, Math.floor(window.innerHeight/2));")
                page.wait_for_timeout(250)
                page.evaluate("window.dispatchEvent(new Event('resize'));")
            except Exception:
                pass

        # Wait up to ~75s for captures
        charts, injected_json = [], []
        for _ in range(75):
            try:
                charts = page.evaluate("window.__CAPTURE__ ? window.__CAPTURE__.charts : []") or []
                injected_json = page.evaluate("window.__CAPTURE__ ? window.__CAPTURE__.json : []") or []
                if charts or injected_json:
                    break
            except Exception:
                pass
            time.sleep(1)

        # Dump canvas text & storage
        canvas_text = []
        try:
            canvas_text = page.evaluate("window.__CAPTURE__ ? window.__CAPTURE__.canvasText : []") or []
        except Exception:
            pass

        try:
            ls = page.evaluate("Object.fromEntries(Object.keys(localStorage).map(k=>[k, localStorage.getItem(k)]))")
        except Exception:
            ls = {}
        try:
            ss = page.evaluate("Object.fromEntries(Object.keys(sessionStorage).map(k=>[k, sessionStorage.getItem(k)]))")
        except Exception:
            ss = {}

        duration = round(time.time() - t0, 2)

        # Write outputs
        merged_json = json_payloads + injected_json
        for i, payload in enumerate(merged_json):
            (OUT / f"xhr-{i:02d}.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        for i, payload in enumerate(charts):
            (OUT / f"chart-{i:02d}.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        if canvas_text:
            (OUT / "canvas-text.json").write_text(json.dumps(canvas_text, indent=2, ensure_ascii=False), encoding="utf-8")

        (OUT / "storage-local.json").write_text(json.dumps(ls, indent=2, ensure_ascii=False), encoding="utf-8")
        (OUT / "storage-session.json").write_text(json.dumps(ss, indent=2, ensure_ascii=False), encoding="utf-8")

        try:
            (OUT / "page.html").write_text(page.content(), encoding="utf-8")
        except Exception:
            pass
        try:
            page.screenshot(path=str(OUT / "screenshot.png"), full_page=True)
        except Exception:
            pass

        ctx.tracing.stop(path=str(OUT / "trace.zip"))

        summary = {
            "url": URL,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "duration_sec": duration,
            "counts": {
                "req": len(req_log),
                "json": len(merged_json),
                "chartConfigs": len(charts),
                "canvasText": len(canvas_text),
                "localStorageKeys": len(ls),
                "sessionStorageKeys": len(ss),
            }
        }
        (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

        print("=== Capture complete ===")
        print(json.dumps(summary, indent=2))

        ctx.close()
        browser.close()

if __name__ == "__main__":
    main()
