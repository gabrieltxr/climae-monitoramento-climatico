import os
from app import create_app
from app.scheduler import init_scheduler
from scripts.preflight_check import run_preflight

app = create_app()

if __name__ == "__main__":
    debug = True

    # Executa a validação de APIs e Banco apenas no processo principal antes de subir
    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        run_preflight(strict=False)

    # Define o modo debug ANTES de iniciar o agendador: com o reloader ativo, ele só
    # deve subir no processo filho (WERKZEUG_RUN_MAIN=true), nunca no processo pai.
    app.debug = debug
    init_scheduler(app)

    # Inicia o servidor web
    app.run(host="0.0.0.0", port=5000, debug=debug)
