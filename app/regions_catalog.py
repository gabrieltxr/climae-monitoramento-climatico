"""
Catálogo único das 56 regiões monitoradas (8 municípios x 7 regiões).

Fonte de verdade para scripts/seed_regions.py, scripts/cleanup_all_regions.py,
scripts/seed_occurrences.py e a lista de bairros do README (um teste confere o README).
"""

REGIONS = [
    # 1. RIO GRANDE (7 Regiões)
    {"name": "Rio Grande (Centro)", "city": "Rio Grande", "latitude": -32.0350, "longitude": -52.0986},
    {"name": "Rio Grande (Cidade Nova)", "city": "Rio Grande", "latitude": -32.0465, "longitude": -52.1158},
    {"name": "Rio Grande (Vila Junção)", "city": "Rio Grande", "latitude": -32.0620, "longitude": -52.1540},
    {"name": "Rio Grande (Parque Marinha)", "city": "Rio Grande", "latitude": -32.0785, "longitude": -52.1645},
    {"name": "Rio Grande (Parque São Pedro)", "city": "Rio Grande", "latitude": -32.0890, "longitude": -52.1780},
    {"name": "Rio Grande (Cassino)", "city": "Rio Grande", "latitude": -32.1844, "longitude": -52.1611},
    {"name": "Rio Grande (Ilha dos Marinheiros)", "city": "Rio Grande", "latitude": -31.9961, "longitude": -52.1558},

    # 2. PELOTAS (7 Regiões)
    {"name": "Pelotas (Centro)", "city": "Pelotas", "latitude": -31.7654, "longitude": -52.3376},
    {"name": "Pelotas (Laranjal)", "city": "Pelotas", "latitude": -31.7519, "longitude": -52.2289},
    {"name": "Pelotas (Areal)", "city": "Pelotas", "latitude": -31.7542, "longitude": -52.3115},
    {"name": "Pelotas (Três Vendas)", "city": "Pelotas", "latitude": -31.7225, "longitude": -52.3340},
    {"name": "Pelotas (Fragata)", "city": "Pelotas", "latitude": -31.7580, "longitude": -52.3850},
    {"name": "Pelotas (São Gonçalo)", "city": "Pelotas", "latitude": -31.7820, "longitude": -52.3310},
    {"name": "Pelotas (Colônia / Cascata)", "city": "Pelotas", "latitude": -31.6210, "longitude": -52.5050},

    # 3. SÃO LOURENÇO DO SUL (7 Regiões)
    {"name": "São Lourenço (Centro)", "city": "São Lourenço do Sul", "latitude": -31.3653, "longitude": -51.9783},
    {"name": "São Lourenço (Praia / Barrinha)", "city": "São Lourenço do Sul", "latitude": -31.3785, "longitude": -51.9610},
    {"name": "São Lourenço (Lomba / BR-116)", "city": "São Lourenço do Sul", "latitude": -31.3520, "longitude": -51.9950},
    {"name": "São Lourenço (Navegantes)", "city": "São Lourenço do Sul", "latitude": -31.3690, "longitude": -51.9680},
    {"name": "São Lourenço (Boa Vista)", "city": "São Lourenço do Sul", "latitude": -31.2650, "longitude": -52.1480},
    {"name": "São Lourenço (Boqueirão)", "city": "São Lourenço do Sul", "latitude": -31.3120, "longitude": -52.2350},
    {"name": "São Lourenço (Harmonia)", "city": "São Lourenço do Sul", "latitude": -31.4250, "longitude": -52.0850},

    # 4. SÃO JOSÉ DO NORTE (7 Regiões)
    {"name": "SJN (Centro)", "city": "São José do Norte", "latitude": -32.0150, "longitude": -52.0419},
    {"name": "SJN (Quinta Secção / Barra)", "city": "São José do Norte", "latitude": -32.1480, "longitude": -52.0780},
    {"name": "SJN (Guarida / Tamandaré)", "city": "São José do Norte", "latitude": -32.0020, "longitude": -52.0350},
    {"name": "SJN (São Caetano)", "city": "São José do Norte", "latitude": -31.9450, "longitude": -51.9920},
    {"name": "SJN (Mar Grosso)", "city": "São José do Norte", "latitude": -32.0410, "longitude": -51.9850},
    {"name": "SJN (Estreito)", "city": "São José do Norte", "latitude": -31.8420, "longitude": -51.8650},
    {"name": "SJN (Bojuru)", "city": "São José do Norte", "latitude": -31.6350, "longitude": -51.4950},

    # 5. CANGUÇU (7 Regiões)
    {"name": "Canguçu (Centro)", "city": "Canguçu", "latitude": -31.3950, "longitude": -52.6756},
    {"name": "Canguçu (Vila Nova / ERS-265)", "city": "Canguçu", "latitude": -31.4080, "longitude": -52.6920},
    {"name": "Canguçu (Canguçu Velho)", "city": "Canguçu", "latitude": -31.3450, "longitude": -52.5650},
    {"name": "Canguçu (Iguatemi)", "city": "Canguçu", "latitude": -31.2250, "longitude": -52.7850},
    {"name": "Canguçu (Herval - 3º Dist.)", "city": "Canguçu", "latitude": -31.0850, "longitude": -52.6950},
    {"name": "Canguçu (Armada)", "city": "Canguçu", "latitude": -31.1850, "longitude": -52.5250},
    {"name": "Canguçu (Coxilha dos Campos)", "city": "Canguçu", "latitude": -31.4650, "longitude": -52.7950},

    # 6. JAGUARÃO (7 Regiões)
    {"name": "Jaguarão (Centro)", "city": "Jaguarão", "latitude": -32.5661, "longitude": -53.3758},
    {"name": "Jaguarão (Cerro da Pólvora)", "city": "Jaguarão", "latitude": -32.5580, "longitude": -53.3850},
    {"name": "Jaguarão (Carvalho / BR-116)", "city": "Jaguarão", "latitude": -32.5420, "longitude": -53.3790},
    {"name": "Jaguarão (Vila Kennedy)", "city": "Jaguarão", "latitude": -32.5590, "longitude": -53.3620},
    {"name": "Jaguarão (Ponta Alegre / Mirim)", "city": "Jaguarão", "latitude": -32.5350, "longitude": -53.1550},
    {"name": "Jaguarão (Cerrito / BR-116)", "city": "Jaguarão", "latitude": -32.3550, "longitude": -53.2550},
    {"name": "Jaguarão (Passo do Centurião)", "city": "Jaguarão", "latitude": -32.4650, "longitude": -53.5450},

    # 7. SANTA VITÓRIA DO PALMAR (7 Regiões)
    {"name": "SVP (Centro)", "city": "Santa Vitória do Palmar", "latitude": -33.5189, "longitude": -53.3681},
    {"name": "SVP (Porto / Lagoa Mirim)", "city": "Santa Vitória do Palmar", "latitude": -33.5050, "longitude": -53.4280},
    {"name": "SVP (Coxilha / BR-471)", "city": "Santa Vitória do Palmar", "latitude": -33.5120, "longitude": -53.3480},
    {"name": "SVP (Praia do Hermenegildo)", "city": "Santa Vitória do Palmar", "latitude": -33.6650, "longitude": -53.2580},
    {"name": "SVP (Barra do Chuí)", "city": "Santa Vitória do Palmar", "latitude": -33.7420, "longitude": -53.3710},
    {"name": "SVP (Curral Alto)", "city": "Santa Vitória do Palmar", "latitude": -33.2250, "longitude": -53.1250},
    {"name": "SVP (Taim / Capilha)", "city": "Santa Vitória do Palmar", "latitude": -32.7850, "longitude": -52.5850},

    # 8. CHUÍ (7 Regiões)
    {"name": "Chuí (Centro)", "city": "Chuí", "latitude": -33.6911, "longitude": -53.4561},
    {"name": "Chuí (Acesso Norte / BR-471)", "city": "Chuí", "latitude": -33.6750, "longitude": -53.4480},
    {"name": "Chuí (Vila América)", "city": "Chuí", "latitude": -33.7020, "longitude": -53.4520},
    {"name": "Chuí (Bairro Juventude)", "city": "Chuí", "latitude": -33.6880, "longitude": -53.4380},
    {"name": "Chuí (Várzea do Arroio Chuí)", "city": "Chuí", "latitude": -33.7150, "longitude": -53.4250},
    {"name": "Chuí (Estrada da Barra)", "city": "Chuí", "latitude": -33.7180, "longitude": -53.4020},
    {"name": "Chuí (Colônia São João)", "city": "Chuí", "latitude": -33.6620, "longitude": -53.4850},
]


# Nomes usados em versões anteriores -> nome atual no catálogo, por município.
# Usado pela limpeza (fundir regiões antigas) e pela carga de ocorrências históricas.
LEGACY_REGION_NAMES = {
    ("Rio Grande", "Centro"): "Rio Grande (Centro)",
    ("Rio Grande", "Centro / Zona Portuária"): "Rio Grande (Centro)",
    ("Rio Grande", "Cassino"): "Rio Grande (Cassino)",
    ("Rio Grande", "Balneário Cassino"): "Rio Grande (Cassino)",
    ("Rio Grande", "Ilha dos Marinheiros"): "Rio Grande (Ilha dos Marinheiros)",
    ("Pelotas", "Centro"): "Pelotas (Centro)",
    ("Pelotas", "Laranjal"): "Pelotas (Laranjal)",
    ("São Lourenço do Sul", "Centro"): "São Lourenço (Centro)",
    ("São Lourenço do Sul", "Sede Urbana / Orla"): "São Lourenço (Centro)",
    ("São José do Norte", "Centro"): "SJN (Centro)",
    ("São José do Norte", "Sede Urbana"): "SJN (Centro)",
    ("Canguçu", "Centro"): "Canguçu (Centro)",
    ("Canguçu", "Sede Urbana"): "Canguçu (Centro)",
    ("Jaguarão", "Centro"): "Jaguarão (Centro)",
    ("Jaguarão", "Sede Urbana"): "Jaguarão (Centro)",
    ("Santa Vitória do Palmar", "Centro"): "SVP (Centro)",
    ("Santa Vitória do Palmar", "Sede Urbana"): "SVP (Centro)",
    ("Chuí", "Centro"): "Chuí (Centro)",
    ("Chuí", "Sede Urbana"): "Chuí (Centro)",
}

REGION_NAMES = {(r["city"], r["name"]) for r in REGIONS}

# Código IBGE de cada município (usado para casar os avisos oficiais do INMET, que
# listam os municípios atingidos por geocode).
MUNICIPALITY_GEOCODES = {
    "Rio Grande": "4315602",
    "Pelotas": "4314407",
    "São Lourenço do Sul": "4318804",
    "São José do Norte": "4318507",
    "Canguçu": "4304507",
    "Jaguarão": "4311007",
    "Santa Vitória do Palmar": "4317301",
    "Chuí": "4305439",
}
GEOCODE_MUNICIPALITIES = {code: city for city, code in MUNICIPALITY_GEOCODES.items()}


def resolve_region_name(municipality: str, name: str) -> str | None:
    """Nome atual da região (aceita nomes antigos); None se não houver correspondente."""
    if (municipality, name) in REGION_NAMES:
        return name
    return LEGACY_REGION_NAMES.get((municipality, name))
