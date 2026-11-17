"""Read-only smoke checks. Run on host: python scripts/verify_stack.py.

Credentials are read from running containers, kept in memory and never printed.
No charts, dashboards or external database objects are created.
"""
import base64
import json
import subprocess
import urllib.request


def environment(service):
    container = subprocess.check_output(["docker", "compose", "ps", "-q", service], text=True).strip()
    info = json.loads(subprocess.check_output(["docker", "inspect", container], text=True))[0]
    port_map = info["NetworkSettings"]["Ports"]
    port = next(binding[0]["HostPort"] for binding in port_map.values() if binding)
    return dict(item.split("=", 1) for item in info["Config"]["Env"]), f"http://127.0.0.1:{port}"


def request(url, data=None, headers=None):
    raw = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(url, data=raw, headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)


def main():
    api_env, api = environment("telemetry-api")
    gf_env, gf = environment("grafana")
    _, admin = environment("admin")
    _, superset = environment("superset")
    assert request(api + "/health")["status"] == "ok"
    assert "supersetDomain" in request(admin + "/api/config")
    with urllib.request.urlopen(admin) as response:
        assert "Telemetry Benchmark" in response.read().decode()
    with urllib.request.urlopen(superset + "/login/") as response:
        assert response.status == 200
    print("PASS API health, Admin HTML/API proxy, Superset login page")
    credentials = base64.b64encode((gf_env["GF_SECURITY_ADMIN_USER"] + ":" + gf_env["GF_SECURITY_ADMIN_PASSWORD"]).encode()).decode()
    headers = {"Authorization": "Basic " + credentials}
    sources = request(gf + "/api/datasources", headers=headers)
    assert {source["uid"] for source in sources} >= {"clickhouse-direct", "telemetry-infinity"}
    for plugin, version in (("grafana-clickhouse-datasource", "4.23.5"), ("yesoreyeram-infinity-datasource", "4.1.6")):
        info = request(gf + "/api/plugins/" + plugin + "/settings", headers=headers)
        assert info["info"]["version"] == version
    print("PASS both Grafana plugin versions match pinned versions")
    assert request(gf + "/api/datasources/uid/telemetry-infinity/health", headers=headers)["status"] == "OK"
    result = request(gf + "/api/ds/query", {
        "from": "0", "to": "1", "queries": [{
            "refId": "A", "datasource": {"uid": "telemetry-infinity", "type": "yesoreyeram-infinity-datasource"},
            "type": "json", "source": "url", "url": gf_env["TELEMETRY_INTERNAL_URL"] + "/health",
            "parser": "backend", "format": "table", "root_selector": "",
            "columns": [{"selector": "status", "text": "status", "type": "string"}],
            "url_options": {"method": "GET"},
        }],
    }, headers)
    assert not result["results"]["A"].get("error"), result
    values = result["results"]["A"]["frames"][0]["data"]["values"]
    assert values and values[0] == ["ok"], result
    print("PASS Grafana provisioning and Infinity backend -> telemetry-api over Docker network")
    # Exercise the exact API client against live Superset without creating a dashboard.
    code = """import asyncio, uuid, httpx
from app.clients.superset import guest_token
from app.clients import superset
from fastapi import HTTPException
original_client = httpx.AsyncClient
errors = []
async def log_error(response):
    if response.status_code >= 400:
        await response.aread()
        errors.append((response.request.url.path, response.status_code, response.json()))
def client_with_diagnostics(**kwargs):
    return original_client(event_hooks={'response': [log_error]}, **kwargs)
superset.httpx.AsyncClient = client_with_diagnostics
async def check():
    try:
        token = await guest_token(str(uuid.uuid4()))
        assert len(token.split('.')) == 3
        print('PASS live Superset login -> CSRF -> signed guest token for a test UUID (dashboard does not exist; embedding NOT verified)')
    except HTTPException:
        assert errors == [('/api/v1/security/guest_token/', 400, {'message': 'EmbeddedDashboard not found.'})], errors
        print('PASS live Superset login and CSRF; guest-token endpoint authorizes tech user and rejects nonexistent dashboard as expected')
        print('Token issuance for a real dashboard and embedding NOT verified: create dashboard manually first')
asyncio.run(check())
"""
    subprocess.run(["docker", "compose", "exec", "-T", "telemetry-api", "python", "-"], input=code, text=True, check=True)
    print("External ClickHouse and rendering manually-created dashboards are NOT checked by this script.")


if __name__ == "__main__":
    main()
