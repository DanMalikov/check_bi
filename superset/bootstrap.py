"""Only local Superset metadata is written; never contacts ClickHouse."""
import os
from superset.app import create_app
from superset.extensions import appbuilder, db

app = create_app()
with app.app_context():
    sm = appbuilder.sm
    role = sm.add_role("BenchmarkTokenIssuer")
    for name in ("can_grant_guest_token", "can_read"):
        permission = sm.find_permission_view_menu(name, "SecurityRestApi")
        if permission is None:
            raise RuntimeError(f"Superset permission {name} missing after superset init")
        sm.add_permission_role(role, permission)
    # Gamma UI/dashboard permissions, without broad database access.
    guest = sm.add_role("BenchmarkGuest")
    gamma = sm.find_role("Gamma")
    guest.permissions = list(gamma.permissions)
    for username, password, email, roles in [
        (os.environ["SUPERSET_ADMIN_USER"], os.environ["SUPERSET_ADMIN_PASSWORD"],
         os.environ["SUPERSET_ADMIN_EMAIL"], [sm.find_role("Admin")]),
        (os.environ["SUPERSET_TECH_USER"], os.environ["SUPERSET_TECH_PASSWORD"],
         "embed@localhost.invalid", [role]),
    ]:
        user = sm.find_user(username=username)
        if user is None:
            user = sm.add_user(username, "Benchmark", "Service", email, roles, password)
            if not user:
                raise RuntimeError("Failed to create Superset user")
        else:
            user.roles = roles
            sm.reset_password(user.id, password)
    db.session.commit()
    # Once the user has opted in to creating the connection, keep it in sync with .env.
    if os.getenv("CLICKHOUSE_HOST") and os.getenv("CLICKHOUSE_USER"):
        from configure_clickhouse import configure
        configure(create=False)
