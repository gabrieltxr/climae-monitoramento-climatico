from app.collectors.institutional_traffic import (
    get_traffic_panel_data,
    update_institutional_traffic,
    get_latest_traffic_timestamp,
)

# Compatibilidade retroativa mantendo a interface esperada pelo projeto
__all__ = [
    "get_traffic_panel_data",
    "update_institutional_traffic",
    "get_latest_traffic_timestamp",
]
