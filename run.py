import os
from app import create_app
from app.scheduler import init_scheduler
from scripts.preflight_check import run_preflight

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


if __name__ == "__main__":
    host, port, debug = resolve_server_settings()

    # Executa a validação de APIs e Banco apenas no processo principal antes de subir
    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        run_preflight(strict=False)

    # Define o modo debug ANTES de iniciar o agendador: com o reloader ativo, ele só
    # deve subir no processo filho (WERKZEUG_RUN_MAIN=true), nunca no processo pai.
    app.debug = debug
    init_scheduler(app)

    # Inicia o servidor web
    app.run(host=host, port=port, debug=debug)
