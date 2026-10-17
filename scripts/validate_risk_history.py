import sys
import os
import requests

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from app.models import Region, Occurrence
from app.services.risk_engine import calculate_region_risk, METHOD_VERSION

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


def fetch_event_day_weather(lat: float, lon: float, date_str: str) -> dict | None:
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": date_str,
        "end_date": date_str,
        "daily": "precipitation_sum,wind_gusts_10m_max",
        "timezone": "UTC",
    }
    try:
        r = requests.get(ARCHIVE_URL, params=params, timeout=15)
        r.raise_for_status()
        return r.json().get("daily", {})
    except requests.RequestException:
        return None


def run_retrospective():
    app = create_app()
    with app.app_context():
        print(f"=== AVALIAÇÃO RETROSPECTIVA DO ÍNDICE DE RISCO ({METHOD_VERSION}) ===")
        
        # 1. Calcula o risco atual para todas as 11 regiões e salva no banco
        regions = Region.query.all()
        for r in regions:
            calculate_region_risk(r)
        print(f"Risco atual calculado e persistido para {len(regions)} regiões.\n")

        # 2. Valida o algoritmo contra eventos históricos documentados (não-reanálise)
        documented_events = (
            Occurrence.query
            .filter(Occurrence.reliability != "anomalia meteorológica")
            .order_by(Occurrence.occurred_at.desc())
            .limit(8)
            .all()
        )

        print("Comparando fórmula de risco com eventos históricos documentados:")
        print("-" * 85)
        for ev in documented_events:
            reg = Region.query.get(ev.region_id)
            d_str = ev.occurred_at.strftime("%Y-%m-%d")
            w = fetch_event_day_weather(reg.latitude, reg.longitude, d_str)

            rain_day = (w.get("precipitation_sum", [0])[0] or 0.0) if w else 0.0
            wind_day = (w.get("wind_gusts_10m_max", [0])[0] or 0.0) if w else 0.0

            # Simula pontuação do algoritmo v0.1-mvp naquele dia histórico
            precip_pts = min(40.0, (rain_day / 50.0) * 30.0 + (10.0 if rain_day > 5 else 0.0))
            hist_pts = 30.0  # Região com vulnerabilidade comprovada
            comp_pts = min(25.0, (wind_day / 70.0) * 18.0 + 4.0)
            sim_score = round(min(100.0, precip_pts + hist_pts + comp_pts), 1)

            status_val = "DETECTADO (Alto/Muito Alto)" if sim_score >= 50 else "SUBESTIMADO (Limitação Hidrológica)"
            print(
                f"[{d_str}] {reg.municipality:<22} | {ev.event_type:<30}\n"
                f"   -> Clima Local no Dia: Chuva={rain_day:.1f}mm, Vento={wind_day:.1f}km/h | "
                f"Score Simulado: {sim_score}/100 -> {status_val}"
            )
        print("-" * 85)
        print(
            "NOTA DE LIMITAÇÃO DOCUMENTADA (Seção 4 e 6 do Manual):\n"
            "Eventos de 'Inundação Lagunar' (como maio/2024 em Rio Grande e Pelotas) são causados pela\n"
            "vazão da bacia hidrográfica da Lagoa dos Patos (chuvas na metade norte do RS) e represamento\n"
            "por vento Sul, podendo ocorrer mesmo em dias com pouca chuva local no município.\n"
            "Por isso, dados de cota/nível da água (ANA/Marinha) são essenciais na categoria complementar."
        )


if __name__ == "__main__":
    run_retrospective()
