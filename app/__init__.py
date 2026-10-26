import sqlite3
from flask import Flask
from flask_migrate import Migrate
from sqlalchemy import event
from sqlalchemy.engine import Engine
from app.config import Config
from app.models import db
from app.routes.main import main_bp
from app.scheduler import init_scheduler

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

    # Inicia o agendamento automático de coletas meteorológicas
    init_scheduler(app)

    return app