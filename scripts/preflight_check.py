import os
import sys
import time
import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Garante que o pacote 'app' seja encontrado quando executado via terminal
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from app.models import db, Region

# Coordenada de referência para o teste (Rio Grande - Centro)
TEST_LAT = -32.0350
TEST_LON = -52.0986

ENDPOINTS_TO_CHECK = [
    {
        "name": "Open-Meteo (Consenso Multi-Modelo: ECMWF + GFS + ICON)",
        "url": "https://api.open-meteo.com/v1/forecast",
        "params": {
            "latitude": TEST_LAT,
            "longitude": TEST_LON,
            "models": "best_match,ecmwf_ifs025,gfs_seamless",
            "current": "temperature_2m,precipitation",
            "forecast_days": 1,
        },
        "critical": True,  # Essencial para o Módulo 01, 04, 05 e 06
        "expected_key": "current",
    },
    {
        "name": "Open-Meteo Archive (Histórico ERA5)",
        "url": "https://archive-api.open-meteo.com/v1/archive",
        "params": {
            "latitude": TEST_LAT,
            "longitude": TEST_LON,
            "start_date": "2026-01-01",
            "end_date": "2026-01-01",
            "daily": "precipitation_sum",
        },
        "critical": False,  # Usado apenas nos scripts históricos
        "expected_key": "daily",
    },
    {
        "name": "GeoIP (ipapi.co — Localização Automática)",
        "url": "https://ipapi.co/json/",
        "params": {},
        "critical": False,  # Possui fallback nativo no navegador
        "expected_key": "latitude",
    },
]


def check_database() -> bool:
    """Verifica se o banco está acessível e com as regiões populadas."""
    try:
        app = create_app()
        with app.app_context():
            count = Region.query.count()
            if count > 0:
                print(f"  [OK] Banco de Dados Local ({count} regiões cadastradas)")
                return True
            else:
                print("  [AVISO] Banco conectado, mas nenhuma região encontrada (rode seed_regions.py)")
                return False
    except Exception as exc:
        print(f"  [ERRO] Falha ao acessar o Banco de Dados: {exc}")
        return False


def check_external_apis(timeout_sec: int = 8) -> bool:
    """Testa conectividade, código HTTP, formato JSON e latência de cada API."""
    all_critical_ok = True
    headers = {"User-Agent": "CLIMAE-PreflightCheck/1.0"}

    for item in ENDPOINTS_TO_CHECK:
        start_t = time.perf_counter()
        try:
            res = requests.get(
                item["url"],
                params=item["params"],
                headers=headers,
                timeout=timeout_sec,
            )
            elapsed_ms = int((time.perf_counter() - start_t) * 1000)
            res.raise_for_status()
            data = res.json()

            if item["expected_key"] not in data:
                raise ValueError(f"Chave '{item['expected_key']}' ausente na resposta JSON")

            # Aviso se a resposta estiver demorando mais de 3 segundos
            speed_tag = f"{elapsed_ms} ms" if elapsed_ms < 3000 else f"{elapsed_ms} ms (LENTO)"
            print(f"  [OK] {item['name']} -> HTTP {res.status_code} ({speed_tag})")

        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - start_t) * 1000)
            tag = "[FALHA CRÍTICA]" if item["critical"] else "[AVISO - SECUNDÁRIO]"
            print(f"  {tag} {item['name']} ({elapsed_ms} ms) -> {exc}")
            if item["critical"]:
                all_critical_ok = False

    return all_critical_ok


def run_preflight(strict: bool = False) -> bool:
    """
    Executa a bateria completa de validação pré-inicialização.
    Se strict=True, interrompe a execução com código de erro caso uma API crítica falhe.
    """
    print("=" * 72)
    print("🔍 CLIMAE — DIAGNÓSTICO PRÉ-INICIALIZAÇÃO (PRE-FLIGHT CHECK)")
    print("=" * 72)

    db_ok = check_database()
    apis_ok = check_external_apis()

    print("-" * 72)
    if db_ok and apis_ok:
        print("✅ Tudo pronto! Comunicações e banco de dados operacionais.")
        print("=" * 72)
        return True
    else:
        print("⚠️  Atenção: Uma ou mais verificações apresentaram falhas.")
        print("   O sistema continuará operando com os últimos dados em cache no banco.")
        print("=" * 72)
        if strict:
            sys.exit(1)
        return False


if __name__ == "__main__":
    # Permite rodar direto no terminal: python scripts/preflight_check.py
    strict_mode = "--strict" in sys.argv
    run_preflight(strict=strict_mode)
