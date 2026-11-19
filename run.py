import os
import sys
from app import create_app
from app.config import INSECURE_SECRET_KEYS
from app.scheduler import init_scheduler
from scripts.preflight_check import run_preflight
from app.db_schema import ensure_schema

app = create_app()

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def resolve_server_settings(env=os.environ) -> tuple[str, int, bool]:
    """
    Define host, porta e modo debug a partir do ambiente.
    - Padrão: 127.0.0.1 (só a própria máquina) com debug ligado.
    - CLIMAE_HOST=0.0.0.0 expõe na rede local; nesse caso o debug é SEMPRE desligado,
      pois o debugger do Werkzeug permite executar código remotamente.
    """
    host = env.get("CLIMAE_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(env.get("CLIMAE_PORT", "5000"))
    debug = env.get("FLASK_DEBUG", "1").strip().lower() in ("1", "true", "yes", "on")

    if debug and host not in LOOPBACK_HOSTS:
        if env.get("WERKZEUG_RUN_MAIN") != "true":
            print(f"[SEGURANÇA] Debug desativado: o servidor está exposto em {host}. "
                  "O debugger só é permitido em 127.0.0.1/localhost.")
        debug = False
    return host, port, debug


GENERATE_KEY_HINT = 'python -c "import secrets; print(secrets.token_hex(32))"'


def check_secret_key(secret_key, host: str) -> tuple[bool, str | None]:
    """
    Retorna (pode_subir, mensagem). Chave vazia ou pública (INSECURE_SECRET_KEYS):
    exposto na rede, recusa subir; só na própria máquina, sobe com aviso.
    """
    if str(secret_key or "").strip() not in INSECURE_SECRET_KEYS:
        return True, None
    if host in LOOPBACK_HOSTS:
        return True, ("[SEGURANÇA] SECRET_KEY vazia ou pública (padrão). Aceita só em 127.0.0.1; "
                      f"antes de expor o servidor, defina uma no .env: {GENERATE_KEY_HINT}")
    return False, (f"[SEGURANÇA] Servidor NÃO iniciado: exposto em {host} com SECRET_KEY vazia ou pública "
                   "(o anonimato dos relatos de energia depende dela). "
                   f"Gere uma chave e defina SECRET_KEY no .env: {GENERATE_KEY_HINT}")


if __name__ == "__main__":
    host, port, debug = resolve_server_settings()

    can_start, key_message = check_secret_key(app.config.get("SECRET_KEY"), host)
    if key_message and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        print(key_message)
    if not can_start:
        sys.exit(1)

    # Executa a validação de APIs e Banco apenas no processo principal antes de subir
    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        # Aplica as migrations pendentes (e marca bancos antigos) antes de subir.
        with app.app_context():
            ensure_schema()
        run_preflight(strict=False)

    # Define o modo debug ANTES de iniciar o agendador: com o reloader ativo, ele só
    # deve subir no processo filho (WERKZEUG_RUN_MAIN=true), nunca no processo pai.
    app.debug = debug
    init_scheduler(app)

    # Inicia o servidor web
    app.run(host=host, port=port, debug=debug)
