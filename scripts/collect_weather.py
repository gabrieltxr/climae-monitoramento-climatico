import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from app.collectors.open_meteo import collect_all_regions

if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        print("Consultando Open-Meteo para as regiões da Zona Sul do RS...")
        resultado = collect_all_regions()
        print(
            f"Coleta finalizada! Regiões com sucesso: {resultado['success']}/{resultado['total_regions']} | "
            f"Novos registros: {resultado['inserted']} | Atualizados: {resultado['updated']}"
        )
