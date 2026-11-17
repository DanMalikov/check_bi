"""Create .env with fresh local service credentials; never overwrite an existing file."""
from pathlib import Path
import secrets

root = Path(__file__).resolve().parent.parent
target = root / ".env"
if target.exists():
    raise SystemExit(".env already exists; left unchanged.")
content = (root / ".env.example").read_text(encoding="utf-8")
for key in ("SUPERSET_SECRET_KEY", "SUPERSET_GUEST_TOKEN_SECRET", "SUPERSET_ADMIN_PASSWORD",
            "SUPERSET_TECH_PASSWORD", "GRAFANA_ADMIN_PASSWORD"):
    content = content.replace(key + "=\n", key + "=" + secrets.token_urlsafe(48) + "\n")
target.write_text(content, encoding="utf-8")
print("Created .env with random local credentials. Fill ClickHouse settings before reading external data.")
