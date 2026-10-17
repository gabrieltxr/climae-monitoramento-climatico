from flask import Flask
from flask_migrate import Migrate
from app.config import Config
from app.models import db
from app.routes.main import main_bp
from app.scheduler import init_scheduler

migrate = Migrate()

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    migrate.init_app(app, db)

    app.register_blueprint(main_bp)

    # Inicia o agendamento automático de coletas meteorológicas
    init_scheduler(app)

    return app