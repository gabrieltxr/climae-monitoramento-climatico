import os
from app import create_app
from app.scheduler import init_scheduler
from scripts.preflight_check import run_preflight

app = create_app()

if __name__ == "__main__":
    # Executa a validação de APIs e Banco apenas no processo principal antes de subir
    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        run_preflight(strict=False)

    # Inicia o agendador de 15 minutos
    init_scheduler(app)

    # Inicia o servidor web
    app.run(host="0.0.0.0", port=5000, debug=True)
