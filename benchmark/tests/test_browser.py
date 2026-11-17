"""Real Chromium, synthetic local frames. No external ClickHouse or dashboard changes."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from urllib.parse import urlsplit, parse_qs

import pytest
from playwright.async_api import async_playwright

from benchmark.browser import BrowserMeasurement, VIEWPORT
from test_contracts import HOURLY, RAW


@pytest.fixture
def fixture_server():
    state = {"raw_count": 100000}
    queries = [{"refId": role, "rawSql": sql, "datasource": {"type": "grafana-clickhouse-datasource"}}
               for role, sql in (("hourly", HOURLY), ("raw", RAW))]
    superset_queries = [
        {"form_data": {"slice_id": 1}, "queries": [{"columns": [{"sqlExpression": "sent_at"}], "metrics": ["count"], "extras": {"time_grain_sqla": "PT1H"}}]},
        {"form_data": {"slice_id": 2}, "queries": [{"columns": ["sent_at", "device_id", "metric", "value_num", "value_str"], "row_limit": 100000, "orderby": [["sent_at", True], ["device_id", True], ["metric", True]]}]},
    ]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, content, kind="application/json"):
            data = content.encode()
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path.startswith("/api/dashboards/uid/"):
                self.send(json.dumps({"dashboard": {"panels": [
                    {"id": 1, "targets": [queries[0]]}, {"id": 2, "targets": [queries[1]]}]}}))
            elif path.startswith("/embedded/"):
                self.send('''<html><body><div class="dashboard-component-chart-holder dashboard-chart-id-1"><div class="header-title">Hourly Events</div><div class="chart-container"><canvas width="400" height="120"></canvas></div></div>
                <div class="dashboard-component-chart-holder dashboard-chart-id-2"><div class="header-title">Raw Records</div><table><tbody></tbody></table></div><script>
                async function load(){for(const query of QUERIES){await (await fetch('/api/v1/chart/data',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(query)})).json();}
                const ctx=document.querySelector('canvas').getContext('2d');
                for(let i=0;i<5;i++){ctx.beginPath();ctx.moveTo(10,i*10);ctx.lineTo(300,100);ctx.stroke();}
                document.querySelector('tbody').innerHTML='<tr><td>synthetic</td></tr>';}
                load();</script></body></html>'''.replace('QUERIES', json.dumps(superset_queries)), "text/html")
            elif path.startswith("/d/"):
                self.send('''<html><body><div data-viz-panel-key="panel-1"><div data-testid="data-testid panel content"><canvas width="400" height="120"></canvas></div></div>
                <div data-viz-panel-key="panel-2"><table><tbody></tbody></table></div><script>
                async function load(){await fetch('/api/dashboards/uid/fixture');
                const data=await (await fetch('/api/ds/query',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({queries:QUERIES})})).json();
                const ctx=document.querySelector('canvas').getContext('2d');
                for(let i=0;i<5;i++){ctx.beginPath();ctx.moveTo(10,i*10);ctx.lineTo(300,100);ctx.stroke();}
                document.querySelector('tbody').innerHTML='<tr><td>synthetic</td></tr>';}
                load();</script></body></html>'''.replace('QUERIES', json.dumps(queries)), "text/html")
            else:
                kind = "/embedded/fixture" if parse_qs(urlsplit(self.path).query).get("scenario") == ["superset"] else "/d/fixture"
                self.send(f'<html><body><iframe src="{kind}" width="1400" height="800"></iframe></body></html>', "text/html")

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            n = state["raw_count"]
            if self.path.startswith("/api/v1/chart/data"):
                rows = [{"sent_at": 1761955200000, "count": 100000}] if request["form_data"]["slice_id"] == 1 else [
                    {"sent_at": 1761955200000, "device_id": "synthetic", "metric": "synthetic", "value_num": 1, "value_str": ""}] * n
                self.send(json.dumps({"result": [{"status": "success", "data": rows, "rowcount": len(rows), "is_cached": False}]}))
                return
            def frame(names, values):
                return {"schema": {"fields": [{"name": name} for name in names]}, "data": {"values": values}}
            payload = {"results": {
                "hourly": {"frames": [frame(["time", "value"], [[1761955200000, 1761958800000], [50000, 50000]])]},
                "raw": {"frames": [frame(["sent_at", "device_id", "metric", "value_num", "value_str"],
                                         [[1761955200000] * n, ["synthetic"] * n, ["synthetic"] * n, [1] * n, [""] * n])]},
            }}
            self.send(json.dumps(payload))

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", state
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.mark.parametrize("count,success", [(100000, True), (99999, False)])
@pytest.mark.parametrize("variant", ["direct", "superset"])
def test_chromium_data_and_observed_dom_are_separate(fixture_server, count, success, variant):
    url, state = fixture_server
    state["raw_count"] = count

    async def check():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            context = await browser.new_context(viewport=VIEWPORT)
            result = await BrowserMeasurement(context, url, variant, 15, "measured", "cold", 1).run()
            await browser.close()
            return result
    result = asyncio.run(check())
    assert result["success"] == success, result
    if success:
        assert result["panels"]["raw"]["rows"] == 100000
        assert result["ready_ms"] >= result["data_ms"] > 0
        assert result["iframe_ms"] is not None
        assert result["readiness"] == "dom_observed_approximate"
    else:
        assert "row_count" in result["errors"]
        assert result["ready_ms"] is None
