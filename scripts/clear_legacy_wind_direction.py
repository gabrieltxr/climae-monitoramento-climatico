"""
Apaga (grava NULL) a direção do vento das observações coletadas antes da correção
da média circular (commit 97c557a, 18/11/2026 00:50 UTC).

Até essa correção a direção era a média aritmética simples entre os modelos, que
erra perto do norte (350° e 10° davam 180°). O banco guarda só a média final, sem
o valor de cada modelo, então não há como recalcular: a direção desses registros
é descartada por não ser confiável. Só observações reais (is_forecast=False) são
afetadas; previsões são regravadas a cada coleta.

Por padrão apenas SIMULA e mostra quantos registros seriam alterados:

    python scripts/clear_legacy_wind_direction.py
    python scripts/clear_legacy_wind_direction.py --apply      # grava no banco

Com --apply, o script primeiro copia o banco SQLite para
instance/climae_zonasul.backup-AAAAMMDD-HHMMSS.db; se a cópia falhar, nada é alterado.
Em bancos que não são SQLite, faça o backup manualmente e use --skip-backup.
"""
import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy import or_
from app import create_app
from app.models import db, WeatherObservation

# Momento em que a média circular entrou no código (commit 97c557a).
FIX_CUTOFF_UTC = datetime(2026, 11, 18, 0, 50, tzinfo=timezone.utc)


def _legacy_query(cutoff_utc: datetime):
    cutoff_naive = cutoff_utc.astimezone(timezone.utc).replace(tzinfo=None)
    return WeatherObservation.query.filter(
        WeatherObservation.is_forecast == False,  # noqa: E712
        WeatherObservation.wind_direction.isnot(None),
        or_(WeatherObservation.collected_at.is_(None), WeatherObservation.collected_at < cutoff_naive),
    )


def backup_sqlite_database(db_path: str, now: datetime | None = None) -> str:
    """Copia o banco SQLite (via API de backup, consistente mesmo em modo WAL) para um
    arquivo ao lado do original com data e hora no nome. Retorna o caminho da cópia."""
    if not db_path or db_path == ":memory:" or not os.path.isfile(db_path):
        raise FileNotFoundError(f"Banco SQLite não encontrado: {db_path!r}")
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    root, ext = os.path.splitext(db_path)
    backup_path = f"{root}.backup-{stamp}{ext or '.db'}"
    if os.path.exists(backup_path):
        raise FileExistsError(f"A cópia já existe: {backup_path}")
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(backup_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return backup_path


def clear_legacy_wind_direction(cutoff_utc: datetime = FIX_CUTOFF_UTC, apply: bool = False) -> int:
    """Retorna quantos registros têm (ou tinham, com apply=True) direção anterior ao corte."""
    query = _legacy_query(cutoff_utc)
    count = query.count()
    if apply and count:
        query.update({WeatherObservation.wind_direction: None}, synchronize_session=False)
        db.session.commit()
    return count


def main(argv=None, app=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="grava a alteração no banco (sem isso, só simula)")
    parser.add_argument("--skip-backup", action="store_true",
                        help="não faz a cópia automática (só para bancos não-SQLite, com backup manual feito)")
    args = parser.parse_args(argv)

    app = app or create_app()
    with app.app_context():
        print(f"Banco: {app.config['SQLALCHEMY_DATABASE_URI']}")
        print(f"Corte: observações coletadas antes de {FIX_CUTOFF_UTC.isoformat()}")

        if args.apply and not args.skip_backup:
            url = db.engine.url
            if url.get_backend_name() != "sqlite":
                print("[ERRO] Cópia automática só funciona com SQLite. Faça o backup manualmente "
                      "e rode de novo com --skip-backup. Nada foi alterado.")
                return 1
            try:
                backup_path = backup_sqlite_database(url.database)
            except Exception as exc:
                print(f"[ERRO] Falha ao copiar o banco: {exc}. Nada foi alterado.")
                return 1
            print(f"Cópia de segurança: {backup_path}")

        count = clear_legacy_wind_direction(apply=args.apply)
        if args.apply:
            print(f"Direção do vento apagada em {count} observações.")
        else:
            print(f"[SIMULAÇÃO] {count} observações teriam a direção do vento apagada. "
                  "Use --apply para gravar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
