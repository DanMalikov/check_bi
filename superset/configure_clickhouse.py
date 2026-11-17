"""Explicit, optional command: updates a Superset connection from environment only."""
from superset.app import create_app
from superset.extensions import db
from superset_config import benchmark_clickhouse_uri

def configure(create=True):
    from superset.models.core import Database
    uri = benchmark_clickhouse_uri()
    database = db.session.query(Database).filter_by(database_name="Telemetry ClickHouse").one_or_none()
    if database is None:
        if not create:
            return
        database = Database(database_name="Telemetry ClickHouse")
        db.session.add(database)
    database.set_sqlalchemy_uri(uri)
    database.allow_dml = False
    database.allow_ctas = False
    database.allow_cvas = False
    database.expose_in_sqllab = True
    db.session.commit()
    print("Superset connection updated from environment. No external queries executed.")


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        configure()
