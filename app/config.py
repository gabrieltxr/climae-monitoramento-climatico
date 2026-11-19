import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# Chaves públicas (padrão do código e exemplo do .env.example). A SECRET_KEY é o sal do
# hash anônimo dos relatos de energia: com uma chave conhecida, o hash de qualquer IP
# pode ser recalculado e o anonimato se perde.
INSECURE_SECRET_KEYS = {"", "dev-secret-key", "chave-secreta-desenvolvimento-mvp"}

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
