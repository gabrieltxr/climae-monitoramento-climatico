"""
Processo dedicado ao agendador (coleta a cada 15 min, purga diária, consolidação mensal).

Use quando a interface web for servida por um servidor WSGI (gunicorn, waitress etc.
apontando para `run:app`): nesse modo o agendador NÃO sobe junto com a web, e com
vários workers subiria um agendador por worker. Rode este script em UM único processo
separado:

    python scripts/run_scheduler.py

Não rode junto com `python run.py`, que já inicia o próprio agendador.
"""
import os
import signal
import sys
import threading

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Garante que o pacote 'app' seja encontrado quando executado via terminal
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from app.scheduler import init_scheduler, scheduler
from app.db_schema import ensure_schema


def start(app) -> bool:
    """Inicia o agendador para o app informado. Retorna True se ele estiver rodando."""
    # Sem reloader neste processo: o debug (ex.: FLASK_DEBUG=1 no .env) faria o
    # init_scheduler esperar pelo processo filho do Werkzeug, que aqui não existe.
    app.debug = False
    init_scheduler(app)
    return scheduler.running


def main(app=None, stop_event=None) -> int:
    app = app or create_app()
    stop_event = stop_event or threading.Event()

    if not start(app):
        print("[SCHEDULER] Agendador não iniciado (TESTING ou DISABLE_SCHEDULER ativo?).")
        return 1

    # Encerra de forma limpa com Ctrl+C ou SIGTERM (systemd, Docker, supervisores).
    def _request_stop(signum, frame):
        stop_event.set()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, _request_stop)
        signal.signal(signal.SIGTERM, _request_stop)
        if hasattr(signal, "SIGBREAK"):  # Windows: Ctrl+Break / encerramento de console
            signal.signal(signal.SIGBREAK, _request_stop)

    print("[SCHEDULER] Agendador em execução. Ctrl+C para encerrar.")
    try:
        # Espera em fatias curtas para que o Ctrl+C seja atendido também no Windows.
        while not stop_event.wait(timeout=1):
            pass
    finally:
        scheduler.shutdown(wait=False)
        print("[SCHEDULER] Agendador encerrado.")
    return 0


if __name__ == "__main__":
    _app = create_app()
    with _app.app_context():
        ensure_schema()
    sys.exit(main(_app))
