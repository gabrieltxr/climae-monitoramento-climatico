import json
import math
import os
import sys
import time
from datetime import datetime
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

INSTANCE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'instance'))
CACHE_FILE = os.path.join(INSTANCE_DIR, 'preflight_cache.json')

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
        "interval_days": 0,  # Validado em todo início de servidor
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
        "critical": False,  # Usado apenas nos scripts de carga histórica
        "expected_key": "daily",
        "interval_days": 30,  # Validado no máximo 1 vez a cada 30 dias (mensal) para evitar sobrecarga de requisições
    },
    {
        "name": "GeoIP (ipapi.co — Localização Automática)",
        "url": "https://ipapi.co/json/",
        "params": {},
        "critical": False,  # Possui fallback nativo no navegador
        "expected_key": "latitude",
        "interval_days": 0,  # Validado em todo início de servidor
    },
]


def load_cache() -> dict:
    """Carrega o cache de validação do preflight em instance/."""
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_cache(data: dict):
    """Salva o cache de validação do preflight em instance/."""
    try:
        os.makedirs(INSTANCE_DIR, exist_ok=True)
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


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


def check_external_apis(timeout_sec: int = 8, force: bool = False) -> bool:
    """Testa conectividade, código HTTP, formato JSON e latência de cada API com suporte a intervalo de cache."""
    all_critical_ok = True
    headers = {"User-Agent": "CLIMAE-PreflightCheck/1.0"}
    cache = load_cache()

    for item in ENDPOINTS_TO_CHECK:
        interval_days = item.get("interval_days", 0)
        cache_key = item["url"]
        last_check_ts = cache.get(cache_key)

        # Se houver intervalo configurado e não for forçado, checa se está dentro do período de validade
        if interval_days > 0 and last_check_ts and not force:
            elapsed_days = (time.time() - last_check_ts) / 86400.0
            if elapsed_days < interval_days:
                days_left = max(1, int(math.ceil(interval_days - elapsed_days)))
                dt_str = datetime.fromtimestamp(last_check_ts).strftime("%d/%m às %H:%M")
                print(f"  [OK] {item['name']} -> Verificado recentemente ({dt_str} — próxima checagem em {days_left}d)")
                continue

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

            # Salva timestamp de validação bem-sucedida no cache
            cache[cache_key] = time.time()
            save_cache(cache)

            speed_tag = f"{elapsed_ms} ms" if elapsed_ms < 3000 else f"{elapsed_ms} ms (LENTO)"
            print(f"  [OK] {item['name']} -> HTTP {res.status_code} ({speed_tag})")

        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - start_t) * 1000)
            tag = "[FALHA CRÍTICA]" if item["critical"] else "[AVISO - SECUNDÁRIO]"
            print(f"  {tag} {item['name']} ({elapsed_ms} ms) -> {exc}")
            if item["critical"]:
                all_critical_ok = False

    return all_critical_ok


def run_preflight(strict: bool = False, force: bool = False) -> bool:
    """
    Executa a bateria completa de validação pré-inicialização.
    Se strict=True, interrompe a execução com código de erro caso uma API crítica falhe.
    Se force=True, ignora o cache e revalida todos os endpoints.
    """
    print("=" * 72)
    print("🔍 CLIMAE — DIAGNÓSTICO PRÉ-INICIALIZAÇÃO (PRE-FLIGHT CHECK)")
    print("=" * 72)

    db_ok = check_database()
    apis_ok = check_external_apis(force=force)

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
    # Permite rodar direto no terminal: python scripts/preflight_check.py [--strict] [--force]
    strict_mode = "--strict" in sys.argv
    force_mode = "--force" in sys.argv
    run_preflight(strict=strict_mode, force=force_mode)
