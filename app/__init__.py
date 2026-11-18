import os
import sqlite3
from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_migrate import Migrate
from sqlalchemy import event
from sqlalchemy.engine import Engine
from app.config import Config
from app.models import db
from app.routes.main import main_bp

migrate = Migrate()


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """
    Otimiza conexões SQLite:
    - WAL (Write-Ahead Logging): leituras e escritas concorrentes sem travamento.
    - synchronous=NORMAL: performance otimizada de I/O em disco com segurança de transação.
    - busy_timeout=5000: aguarda até 5 segundos em concorrência antes de falhar com lock.
    """
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    migrate.init_app(app, db)

    app.register_blueprint(main_bp)

    # Só confia em X-Forwarded-For quando há proxy reverso declarado (ex.: Nginx = 1).
    trusted_proxies = int(os.getenv("TRUSTED_PROXY_COUNT", "0") or 0)
    if trusted_proxies > 0:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=trusted_proxies)

    # O agendador NÃO é iniciado aqui: create_app() também é usado por scripts,
    # testes e pelo preflight. Ele é iniciado explicitamente em run.py.

    return app