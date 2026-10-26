import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Aponta para instance/climae_zonasul.db como padrão oficial
    _default_db = f"sqlite:///{(BASE_DIR / 'instance' / 'climae_zonasul.db').as_posix()}"
    _db_url = os.getenv("DATABASE_URL", _default_db)

    if _db_url.startswith("postgresql://"):
        _db_url = _db_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    elif _db_url in ("sqlite:///instance/climae_zonasul.db", "sqlite:///climae_zonasul.db"):
        _db_url = _default_db

    SQLALCHEMY_DATABASE_URI = _db_url
