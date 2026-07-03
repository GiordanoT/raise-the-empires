"""
perf_monitor.py — Real-time performance monitor for Raise the Empires server.

Features:
  - Measures every HTTP request (path, method, status, duration)
  - Measures every AMF gateway function call individually
  - Tracks slow requests (configurable threshold)
  - Exposes /perf_report  HTML dashboard
  - Exposes /perf_json    machine-readable JSON
  - Exposes /perf_reset   to clear stats
  - @timed decorator to profile any function

Usage in empires-server.py:
    from perf_monitor import PerfMonitor, timed
    perf = PerfMonitor(app, slow_threshold_ms=200)

Then wrap heavy functions:
    @timed("lookup_state_machine")
    def lookup_state_machine(...): ...
"""

import time
import threading
import collections
from functools import wraps
from flask import request, Response
import json

# ──────────────────────────────────────────────────────────────────────────────
# Internal storage (thread-safe via lock)
# ──────────────────────────────────────────────────────────────────────────────

_lock = threading.Lock()

# Per-path stats: {path: {"count": N, "total_ms": T, "max_ms": M, "min_ms": m, "errors": E}}
_route_stats = collections.defaultdict(lambda: {"count": 0, "total_ms": 0.0,
                                                 "max_ms": 0.0, "min_ms": float("inf"),
                                                 "errors": 0})

# Per AMF-function stats (same structure)
_amf_stats = collections.defaultdict(lambda: {"count": 0, "total_ms": 0.0,
                                               "max_ms": 0.0, "min_ms": float("inf"),
                                               "errors": 0})

# Per named-function stats (from @timed decorator)
_func_stats = collections.defaultdict(lambda: {"count": 0, "total_ms": 0.0,
                                                "max_ms": 0.0, "min_ms": float("inf"),
                                                "errors": 0})

# Slowest requests log (ring buffer of last 50)
_slow_log = collections.deque(maxlen=50)

_start_time = time.time()


def _update(store, key, elapsed_ms, error=False):
    with _lock:
        s = store[key]
        s["count"] += 1
        s["total_ms"] += elapsed_ms
        if elapsed_ms > s["max_ms"]:
            s["max_ms"] = elapsed_ms
        if elapsed_ms < s["min_ms"]:
            s["min_ms"] = elapsed_ms
        if error:
            s["errors"] += 1


def _snapshot(store):
    with _lock:
        result = {}
        for k, v in store.items():
            avg = v["total_ms"] / v["count"] if v["count"] else 0
            result[k] = {**v, "avg_ms": round(avg, 2),
                         "max_ms": round(v["max_ms"], 2),
                         "min_ms": round(v["min_ms"] if v["min_ms"] != float("inf") else 0, 2),
                         "total_ms": round(v["total_ms"], 2)}
        return dict(sorted(result.items(), key=lambda x: x[1]["avg_ms"], reverse=True))


# ──────────────────────────────────────────────────────────────────────────────
# @timed decorator
# ──────────────────────────────────────────────────────────────────────────────

def timed(name=None):
    """Decorator to measure a function's execution time and record it in perf stats."""
    def decorator(fn):
        label = name or fn.__qualname__
        @wraps(fn)
        def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            error = False
            try:
                return fn(*args, **kwargs)
            except Exception:
                error = True
                raise
            finally:
                elapsed = (time.perf_counter() - t0) * 1000
                _update(_func_stats, label, elapsed, error)
        return wrapper
    if callable(name):
        fn, name = name, None
        return decorator(fn)
    return decorator


# ──────────────────────────────────────────────────────────────────────────────
# AMF function timing (called from the gateway handler)
# ──────────────────────────────────────────────────────────────────────────────

def record_amf(func_name: str, elapsed_ms: float, error: bool = False):
    """Call this from the gateway for each AMF function dispatched."""
    _update(_amf_stats, func_name, elapsed_ms, error)


# ──────────────────────────────────────────────────────────────────────────────
# HTML report template
# ──────────────────────────────────────────────────────────────────────────────

_HTML = """<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="5">
<title>Perf Monitor — Raise the Empires</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #0f1117; color: #e2e8f0; padding: 24px; }}
  h1 {{ font-size: 1.5rem; color: #7dd3fc; margin-bottom: 4px; }}
  .subtitle {{ font-size: 0.8rem; color: #64748b; margin-bottom: 24px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap: 20px; }}
  .card {{ background: #1e2330; border-radius: 10px; padding: 18px; border: 1px solid #2d3748; }}
  .card h2 {{ font-size: 0.9rem; font-weight: 600; color: #94a3b8; text-transform: uppercase;
               letter-spacing: .06em; margin-bottom: 12px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.78rem; }}
  th {{ text-align: left; padding: 5px 8px; color: #64748b; font-weight: 500;
        border-bottom: 1px solid #2d3748; }}
  td {{ padding: 5px 8px; border-bottom: 1px solid #1a1f2e; font-variant-numeric: tabular-nums; }}
  tr:last-child td {{ border-bottom: none; }}
  .hot {{ color: #f87171; font-weight: 600; }}
  .warm {{ color: #fb923c; }}
  .ok {{ color: #4ade80; }}
  .bar-wrap {{ background: #111827; border-radius: 4px; height: 6px; width: 80px; display:inline-block; vertical-align:middle; }}
  .bar {{ height: 6px; border-radius: 4px; background: linear-gradient(90deg,#3b82f6,#818cf8); }}
  .slow-item {{ background: #1a1020; border-left: 3px solid #f87171; border-radius: 4px;
                padding: 6px 10px; margin-bottom: 6px; font-size: 0.75rem; }}
  .slow-meta {{ color: #64748b; font-size: 0.7rem; }}
  .badge {{ display:inline-block; padding: 1px 6px; border-radius: 10px; font-size: 0.65rem; font-weight:700; }}
  .badge-get {{ background:#0369a1; }} .badge-post {{ background:#7c3aed; }}
  .uptime {{ color:#94a3b8; font-size:0.78rem; }}
  .actions {{ margin-bottom: 20px; }}
  .actions a {{ color: #7dd3fc; text-decoration: none; font-size: 0.8rem; margin-right: 16px;
                padding: 5px 12px; border: 1px solid #2d3748; border-radius: 6px; }}
  .actions a:hover {{ background: #1e2330; }}
</style>
</head>
<body>
<h1>⚡ Performance Monitor</h1>
<p class="subtitle">Raise the Empires Server &mdash; aggiornamento automatico ogni 5s &mdash; Uptime: {uptime}</p>
<div class="actions">
  <a href="/perf_report">🔄 Refresh</a>
  <a href="/perf_reset">🗑 Reset Stats</a>
  <a href="/perf_json">📄 JSON Raw</a>
</div>
<div class="grid">

  <div class="card">
    <h2>🌐 Route HTTP ({route_count} rotte)</h2>
    <table>
      <tr><th>Path</th><th>N</th><th>Avg ms</th><th>Max ms</th><th>Err</th></tr>
      {route_rows}
    </table>
  </div>

  <div class="card">
    <h2>📡 AMF Gateway Functions ({amf_count} funzioni)</h2>
    <table>
      <tr><th>Function</th><th>N</th><th>Avg ms</th><th>Max ms</th><th>Err</th></tr>
      {amf_rows}
    </table>
  </div>

  <div class="card">
    <h2>🔧 Funzioni @timed ({func_count} funzioni)</h2>
    <table>
      <tr><th>Function</th><th>N</th><th>Avg ms</th><th>Max ms</th><th>Err</th></tr>
      {func_rows}
    </table>
  </div>

  <div class="card">
    <h2>🐢 Richieste lente (ultime 50 &gt; soglia)</h2>
    {slow_items}
  </div>

</div>
</body>
</html>"""


def _ms_class(ms):
    if ms >= 500:
        return "hot"
    if ms >= 150:
        return "warm"
    return "ok"


def _make_rows(data, max_items=30):
    rows = []
    for key, s in list(data.items())[:max_items]:
        avg = s["avg_ms"]
        css = _ms_class(avg)
        pct = min(100, avg / 10)
        bar = f'<div class="bar-wrap"><div class="bar" style="width:{pct:.0f}%"></div></div>'
        rows.append(
            f'<tr><td title="{key}">{key[:45]}</td>'
            f'<td>{s["count"]}</td>'
            f'<td class="{css}">{avg:.1f} {bar}</td>'
            f'<td class="{_ms_class(s["max_ms"])}">{s["max_ms"]:.1f}</td>'
            f'<td>{"⚠️" + str(s["errors"]) if s["errors"] else "—"}</td></tr>'
        )
    return "".join(rows) if rows else '<tr><td colspan="5" style="color:#64748b">Nessun dato</td></tr>'


def _make_slow_items(log):
    if not log:
        return '<p style="color:#64748b;font-size:0.8rem">Nessuna richiesta lenta registrata.</p>'
    items = []
    for entry in reversed(list(log)):
        method = entry.get("method", "GET")
        badge_cls = "badge-post" if method == "POST" else "badge-get"
        items.append(
            f'<div class="slow-item">'
            f'<span class="badge {badge_cls}">{method}</span> '
            f'<span class="hot">{entry["ms"]:.0f}ms</span> '
            f'<code>{entry["path"][:70]}</code>'
            f'<div class="slow-meta">{entry["time"]} &mdash; status {entry["status"]}</div>'
            f'</div>'
        )
    return "".join(items)


# ──────────────────────────────────────────────────────────────────────────────
# PerfMonitor class — attaches to Flask app
# ──────────────────────────────────────────────────────────────────────────────

class PerfMonitor:
    def __init__(self, app, slow_threshold_ms=300):
        self.app = app
        self.threshold = slow_threshold_ms
        self._attach(app)

    def _attach(self, app):
        @app.before_request
        def _before():
            request._perf_t0 = time.perf_counter()

        @app.after_request
        def _after(response):
            t0 = getattr(request, "_perf_t0", None)
            if t0 is None:
                return response
            elapsed = (time.perf_counter() - t0) * 1000
            path = request.path
            if path.startswith("/nullassets/") or path.startswith("/files/"):
                parts = path.split("/")
                path = "/" + "/".join(parts[1:3]) + "/…"
            error = response.status_code >= 400
            _update(_route_stats, f"{request.method} {path}", elapsed, error)
            if elapsed >= self.threshold:
                import datetime
                with _lock:
                    _slow_log.append({
                        "path": request.path,
                        "method": request.method,
                        "ms": round(elapsed, 1),
                        "status": response.status_code,
                        "time": datetime.datetime.now().strftime("%H:%M:%S"),
                    })
            return response

        @app.route("/perf_report")
        def perf_report():
            uptime_s = int(time.time() - _start_time)
            h, rem = divmod(uptime_s, 3600)
            m, s = divmod(rem, 60)
            uptime = f"{h}h {m}m {s}s"

            routes = _snapshot(_route_stats)
            amf = _snapshot(_amf_stats)
            funcs = _snapshot(_func_stats)

            html = _HTML.format(
                uptime=uptime,
                route_count=len(routes),
                amf_count=len(amf),
                func_count=len(funcs),
                route_rows=_make_rows(routes),
                amf_rows=_make_rows(amf),
                func_rows=_make_rows(funcs),
                slow_items=_make_slow_items(_slow_log),
            )
            return Response(html, mimetype="text/html")

        @app.route("/perf_json")
        def perf_json():
            data = {
                "uptime_s": int(time.time() - _start_time),
                "routes": _snapshot(_route_stats),
                "amf_functions": _snapshot(_amf_stats),
                "timed_functions": _snapshot(_func_stats),
                "slow_log": list(_slow_log),
            }
            return Response(json.dumps(data, indent=2), mimetype="application/json")

        @app.route("/perf_reset")
        def perf_reset():
            with _lock:
                _route_stats.clear()
                _amf_stats.clear()
                _func_stats.clear()
                _slow_log.clear()
            return Response('{"status":"reset"}', mimetype="application/json",
                            headers={"Location": "/perf_report"},
                            status=303)
