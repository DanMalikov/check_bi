"""Recreate the four local containers, compare existing metadata IDs, run smoke checks.

No database records or dashboards are created. Docker volumes are retained.
"""
import base64
import json
import subprocess
from verify_stack import environment, request, main as smoke


def snapshot():
    code = """import sqlite3, json
c = sqlite3.connect('file:/app/superset_home/superset.db?mode=ro', uri=True)
tables = ('ab_user', 'dbs', 'tables', 'slices', 'dashboards')
print(json.dumps({t: c.execute('SELECT id FROM [' + t + '] ORDER BY id').fetchall() for t in tables}))
c.close()
"""
    superset = json.loads(subprocess.check_output(
        ["docker", "compose", "exec", "-T", "superset", "python", "-"], input=code, text=True))
    env, url = environment("grafana")
    credentials = base64.b64encode((env["GF_SECURITY_ADMIN_USER"] + ":" + env["GF_SECURITY_ADMIN_PASSWORD"]).encode()).decode()
    sources = request(url + "/api/datasources", headers={"Authorization": "Basic " + credentials})
    return {"superset": superset, "grafana": sorted((source["uid"], source["id"]) for source in sources)}


before = snapshot()
subprocess.run(["docker", "compose", "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "300"], check=True)
assert snapshot() == before, "Metadata IDs changed after container recreation"
print("PASS existing Superset metadata IDs and Grafana datasource IDs survived container recreation")
smoke()
