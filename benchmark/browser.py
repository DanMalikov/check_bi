"""Chromium observer of existing dashboards. Never rewrites panel SQL or requests."""
import asyncio
from datetime import datetime, timezone
import json
import time
from urllib.parse import urlsplit

from .contracts import InvalidRun, grafana_role, superset_role, grafana_data, superset_data


VIEWPORT = {"width": 1600, "height": 1100}
CONTEXT_OPTIONS = {"viewport": VIEWPORT, "service_workers": "block", "timezone_id": "Europe/Moscow"}

INIT_SCRIPT = """(() => {
    // Load event is a separate metric, never panel readiness.
    window.addEventListener('load', () => {
        if (window !== window.top && /\\/(embedded|d)\\//.test(location.pathname))
            window.__benchmarkFrameLoaded(performance.timeOrigin + performance.now()).catch(()=>{});
    }, {once:true});
    // Observe that a canvas has actually received drawing calls; no canvas pixels/data are exported.
    const proto = globalThis.CanvasRenderingContext2D?.prototype;
    if(proto) for(const method of ['stroke','fill','fillRect','fillText','drawImage']) {
        const original=proto[method];
        proto[method]=function(...args) {
            this.canvas.__benchmarkDraws=(this.canvas.__benchmarkDraws||0)+1;
            return original.apply(this,args);
        };
    }
})();"""

DOM_PROBE = """async ({variant, ids}) => {
    const visible = e => {
        const r=e.getBoundingClientRect(), style=getComputedStyle(e);
        return r.width>4 && r.height>4 && r.bottom>0 && r.right>0 && r.top<innerHeight && r.left<innerWidth && style.display!=='none' && style.visibility!=='hidden';
    };
    const result={};
    for(const role of ['hourly','raw']) {
        let panel;
        if(variant!=='superset') {
            const id=ids[role];
            panel=id ? document.querySelector('[data-viz-panel-key="panel-'+id+'"]') : null;
        } else {
            if(ids[role]) panel=document.querySelector('.dashboard-chart-id-'+ids[role]);
            if(!panel) panel=[...document.querySelectorAll('.dashboard-component-chart-holder')].find(e => {
                const title=e.querySelector('.header-title')?.textContent.toLowerCase()||'';
                return role==='hourly' ? title.includes('hourly') : title.includes('raw');
            });
        }
        if(!panel || !visible(panel)) {result[role]={ready:false}; continue;}
        const error=[...panel.querySelectorAll('[role=alert],[data-testid*="Panel data error"],[data-testid*="Panel status error"]')].some(visible);
        const busy=[...panel.querySelectorAll('[aria-busy=true],[role=progressbar],.loading,.ant-spin-spinning')].some(visible);
        let content=false;
        if(role==='raw') {
            content=[...panel.querySelectorAll('tbody tr,[role=row]')].some(row=>visible(row)&&row.querySelector('td,[role=cell],[role=gridcell]'));
            // Grafana table can use a canvas grid. This only supports an approximate readiness signal.
            if(!content) content=[...panel.querySelectorAll('canvas')].some(c=>visible(c)&&c.__benchmarkDraws>2);
        } else {
            const scope=variant==='superset' ? panel.querySelector('.chart-container,.slice_container')||panel : panel.querySelector('[data-testid*="panel content"]')||panel;
            content=[...scope.querySelectorAll('canvas')].some(c=>visible(c)&&c.__benchmarkDraws>2)
                 || [...scope.querySelectorAll('svg path[d],svg rect,svg circle')].filter(visible).length>3;
        }
        result[role]={ready:content&&!error&&!busy,error};
    }
    // Allow browser layout/paint opportunities; this is NOT proof of GPU/compositor completion.
    await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
    return {panels:result,at:performance.timeOrigin+performance.now()};
}"""


class BrowserMeasurement:
    def __init__(self, context, admin_url, variant, timeout_seconds, phase, cache, repetition):
        self.context = context
        self.admin_url = admin_url.rstrip("/")
        self.variant = variant
        self.timeout = timeout_seconds
        self.phase, self.cache, self.repetition = phase, cache, repetition
        self.errors = set()
        self.panels = {}
        self.ids = {}
        self.renderers = {}
        self.tasks = set()
        self.request_roles = {}
        self.role_requests = set()
        self.iframe_ms = None
        self.start_epoch = 0

    def millis(self, epoch):
        return round(max(0, epoch - self.start_epoch), 3)

    def request(self, req):
        path = urlsplit(req.url).path.rstrip("/")
        try:
            if self.variant == "superset" and path == "/api/v1/chart/data":
                payload = req.post_data_json
                role = superset_role(payload)
                self.request_roles[req] = (role, payload)
                chart_id = payload.get("form_data", {}).get("slice_id")
                if isinstance(chart_id, int):
                    self.ids[role] = chart_id
                viz = payload.get("form_data", {}).get("viz_type")
                self.renderers[role] = viz if viz in ("table", "echarts_timeseries_bar", "echarts_timeseries_line") else "unknown"
                if role in self.role_requests:
                    raise InvalidRun("unsupported_query")
                self.role_requests.add(role)
            elif self.variant != "superset" and path == "/api/ds/query":
                queries = req.post_data_json.get("queries", [])
                roles = [grafana_role(q, self.variant) for q in queries]
                if not roles or len(set(roles)) != len(roles) or any(r in self.role_requests for r in roles):
                    raise InvalidRun("unsupported_query")
                self.role_requests.update(roles)
                self.request_roles[req] = (roles, queries)
        except InvalidRun as e:
            self.errors.add(e.code)
        except Exception:
            if path in ("/api/v1/chart/data", "/api/ds/query"):
                self.errors.add("unsupported_query")

    async def response(self, response):
        path = urlsplit(response.url).path.rstrip("/")
        try:
            if self.variant != "superset" and "/api/dashboards/uid/" in path and response.ok:
                metadata = (await response.json()).get("dashboard", {})
                if metadata.get("refresh"):
                    raise InvalidRun("unsupported_query")
                panels = metadata.get("panels", [])
                if len(panels) != 2:
                    raise InvalidRun("missing_panels")
                for panel in panels:
                    if panel.get("transformations"):
                        raise InvalidRun("filtered_query")
                    targets = panel.get("targets", [])
                    if len(targets) != 1:
                        raise InvalidRun("unsupported_query")
                    role = grafana_role(targets[0], self.variant)
                    if role in self.ids and self.ids[role] != panel["id"]:
                        raise InvalidRun("unsupported_query")
                    self.ids[role] = panel["id"]
                    self.renderers[role] = panel.get("type") if panel.get("type") in ("table", "timeseries") else "unknown"
            if path in ("/api/config", "/api/superset/embed") and not response.ok:
                raise InvalidRun("embed_error")
            req = response.request
            if req not in self.request_roles:
                return
            if not response.ok:
                raise InvalidRun("query_error")
            body = await response.body()
            timing = req.timing
            response_end = timing.get("responseEnd", -1)
            if response_end < 0:
                raise InvalidRun("network_error")
            finished_ms = self.millis(timing["startTime"] + response_end)
            length = int((await req.sizes()).get("responseBodySize", 0))
            if length > 100_000_000:
                raise InvalidRun("invalid_data")
            # Body inspected only in memory. Never save HAR/traces, tokens or raw rows.
            if len(body) > 100_000_000:
                raise InvalidRun("invalid_data")
            role, request_data = self.request_roles[req]
            def decode():
                payload = json.loads(body)
                if self.variant == "superset":
                    data, cached = superset_data(payload, role)
                    return {role: data}, cached
                return grafana_data(payload, request_data, self.variant), None
            # Fingerprinting 100k records must not block the observer's event loop.
            blocks, cached = await asyncio.to_thread(decode)
            for kind, data in blocks.items():
                self.panels[kind] = {**data, "data_ms": finished_ms, "ready_ms": None,
                                     "response_bytes": length, "cache_hit": cached, "renderer": self.renderers.get(kind, "unknown")}
                length = 0  # A batched response is counted once, not once per panel.
        except InvalidRun as e:
            self.errors.add(e.code)
        except Exception:
            self.errors.add("invalid_data")

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def run(self):
        page = await self.context.new_page()
        await page.expose_binding("__benchmarkFrameLoaded", self.frame_loaded)
        await page.add_init_script(INIT_SCRIPT)
        page.on("request", self.request)
        page.on("response", lambda response: self.spawn(self.response(response)))
        page.on("requestfailed", lambda req: self.errors.add("network_error") if req in self.request_roles else None)
        self.start_epoch = time.time() * 1000
        started_at = datetime.now(timezone.utc).isoformat()
        deadline = time.monotonic() + self.timeout
        try:
            await page.goto(self.admin_url + f"/?scenario={self.variant}&autostart=1&benchmark=1", wait_until="commit", timeout=self.timeout * 1000)
            stable = {"hourly": 0, "raw": 0}
            observations = {"hourly": [], "raw": []}
            while time.monotonic() < deadline:
                if self.errors:
                    break
                for frame in page.frames[1:]:
                    if not ("/embedded/" in frame.url or "/d/" in frame.url):
                        continue
                    observed = await asyncio.wait_for(frame.evaluate(DOM_PROBE, {"variant": self.variant, "ids": self.ids}), timeout=min(5, max(.1, deadline - time.monotonic())))
                    for role in ("hourly", "raw"):
                        status = observed["panels"].get(role, {})
                        if status.get("error"):
                            self.errors.add("render_error")
                        if status.get("ready"):
                            stable[role] += 1
                            if stable[role] >= 2:
                                observations[role].append(self.millis(observed["at"]))
                        else:
                            stable[role] = 0
                        if role in self.panels and self.panels[role]["ready_ms"] is None:
                            after_data = [t for t in observations[role] if t >= self.panels[role]["data_ms"]]
                            if after_data:
                                self.panels[role]["ready_ms"] = min(after_data)
                if len(self.panels) == 2 and all(p["ready_ms"] is not None for p in self.panels.values()):
                    break
                await asyncio.sleep(.1)
            else:
                self.errors.add("render_unobservable" if len(self.panels) == 2 else "timeout")
        except asyncio.TimeoutError:
            self.errors.add("timeout")
        except Exception:
            self.errors.add("embed_error")
        finally:
            for task in tuple(self.tasks):
                task.cancel()
            if self.tasks:
                await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
            await page.close()
        success = not self.errors and len(self.panels) == 2 and all(p["ready_ms"] is not None for p in self.panels.values())
        return {
            "variant": self.variant, "phase": self.phase, "cache": self.cache, "repetition": self.repetition,
            "started_at": started_at, "success": success, "errors": sorted(self.errors), "iframe_ms": self.iframe_ms,
            "data_ms": max((p["data_ms"] for p in self.panels.values()), default=None) if len(self.panels) == 2 else None,
            "ready_ms": max((p["ready_ms"] for p in self.panels.values() if p["ready_ms"] is not None), default=None) if success else None,
            "panels": self.panels, "readiness": "dom_observed_approximate", "sql_ms": None,
        }

    async def frame_loaded(self, source, epoch):
        if self.iframe_ms is None:
            self.iframe_ms = self.millis(epoch)
