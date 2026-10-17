import sys
import os
from datetime import datetime, timezone

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from app.models import db, Region, Occurrence

HISTORICAL_EVENTS = [
    # --- ENCHENTE HISTÓRICA LAGOA DOS PATOS (MAIO/2024) ---
    {
        "municipality": "Rio Grande",
        "region_name": "Centro / Zona Portuária",
        "event_type": "Inundação Costeira / Lagunar",
        "occurred_at": datetime(2024, 5, 16, 12, 0, tzinfo=timezone.utc),
        "severity": "Alta",
        "description": "Elevação histórica do nível da Lagoa dos Patos atingindo a zona central, mercado público e áreas portuárias.",
        "source": "Defesa Civil / S2iD",
        "reliability": "confirmado"
    },
    {
        "municipality": "Rio Grande",
        "region_name": "Ilha dos Marinheiros",
        "event_type": "Inundação",
        "occurred_at": datetime(2024, 5, 13, 15, 0, tzinfo=timezone.utc),
        "severity": "Alta",
        "description": "Submersão de áreas habitadas e agrícolas devido à vazão extrema na foz da Lagoa dos Patos.",
        "source": "Defesa Civil Municipal",
        "reliability": "confirmado"
    },
    {
        "municipality": "Pelotas",
        "region_name": "Laranjal",
        "event_type": "Inundação Lagunar",
        "occurred_at": datetime(2024, 5, 15, 10, 0, tzinfo=timezone.utc),
        "severity": "Alta",
        "description": "Avanço das águas da Lagoa dos Patos e Canal São Gonçalo sobre a orla e vias do balneário Laranjal.",
        "source": "Prefeitura de Pelotas / SANEP",
        "reliability": "confirmado"
    },
    {
        "municipality": "São José do Norte",
        "region_name": "Sede Urbana",
        "event_type": "Inundação Lagunar",
        "occurred_at": datetime(2024, 5, 14, 9, 0, tzinfo=timezone.utc),
        "severity": "Alta",
        "description": "Alagamento da zona baixa costeira e interrupção temporária de atracações da travessia hidroviária.",
        "source": "Defesa Civil / Hidroviária",
        "reliability": "confirmado"
    },
    {
        "municipality": "São Lourenço do Sul",
        "region_name": "Sede Urbana / Orla",
        "event_type": "Inundação Lagunar",
        "occurred_at": datetime(2024, 5, 15, 14, 0, tzinfo=timezone.utc),
        "severity": "Alta",
        "description": "Represamento da Lagoa dos Patos por vento Sul/Nordeste invadindo quadras da orla urbana.",
        "source": "Defesa Civil / S2iD",
        "reliability": "confirmado"
    },

    # --- CICLONES EXTRATROPICAIS E EVENTOS DE 2023 ---
    {
        "municipality": "Pelotas",
        "region_name": "Centro",
        "event_type": "Chuva Intensa / Alagamento Urbano",
        "occurred_at": datetime(2023, 9, 4, 14, 0, tzinfo=timezone.utc),
        "severity": "Média",
        "description": "Volume elevado de precipitação em curto período exigindo operação máxima das casas de bombas e gerando pontos de alagamento em vias centrais.",
        "source": "SANEP / Imprensa Local",
        "reliability": "noticiado / imprensa"
    },
    {
        "municipality": "Jaguarão",
        "region_name": "Sede Urbana",
        "event_type": "Inundação Ribereinha",
        "occurred_at": datetime(2023, 9, 7, 10, 0, tzinfo=timezone.utc),
        "severity": "Alta",
        "description": "Transbordamento do Rio Jaguarão após acumulado severo de chuvas na bacia fronteiriça, removendo famílias ribeirinhas.",
        "source": "ANA HidroWeb / Defesa Civil",
        "reliability": "confirmado"
    },
    {
        "municipality": "Rio Grande",
        "region_name": "Balneário Cassino",
        "event_type": "Vendaval / Ciclone Extratropical",
        "occurred_at": datetime(2023, 7, 13, 3, 0, tzinfo=timezone.utc),
        "severity": "Alta",
        "description": "Passagem de ciclone extratropical com rajadas superiores a 100 km/h, destelhamentos, queda de árvores e interrupção de energia.",
        "source": "INMET / Praticagem da Barra",
        "reliability": "relatório técnico"
    },
    {
        "municipality": "Rio Grande",
        "region_name": "Centro / Zona Portuária",
        "event_type": "Ressaca / Maré Meteorológica",
        "occurred_at": datetime(2023, 7, 13, 8, 0, tzinfo=timezone.utc),
        "severity": "Média",
        "description": "Suspensão de manobras no canal de acesso ao Porto do Rio Grande devido a ventos intensos e agitação marítima severa.",
        "source": "Praticagem da Barra / Marinha",
        "reliability": "relatório técnico"
    },
    {
        "municipality": "Santa Vitória do Palmar",
        "region_name": "Sede Urbana",
        "event_type": "Vendaval Severo",
        "occurred_at": datetime(2023, 7, 12, 23, 0, tzinfo=timezone.utc),
        "severity": "Média",
        "description": "Rajadas intensas associadas a ciclone extratropical no extremo sul causando danos na rede elétrica e arborização.",
        "source": "Defesa Civil / CEEE Equatorial",
        "reliability": "confirmado"
    },
    {
        "municipality": "Canguçu",
        "region_name": "Sede Urbana",
        "event_type": "Chuva Intensa / Danos em Estradas",
        "occurred_at": datetime(2023, 9, 15, 12, 0, tzinfo=timezone.utc),
        "severity": "Alta",
        "description": "Chuvas volumosas causando danos em pontes, bueiros e bloqueios na malha viária rural e acessos.",
        "source": "Prefeitura de Canguçu / S2iD",
        "reliability": "confirmado"
    },

    # --- EVENTOS HISTÓRICOS MARCANTES ANTERIORES (2011 - 2020) ---
    {
        "municipality": "Rio Grande",
        "region_name": "Balneário Cassino",
        "event_type": "Ressaca Marítima / Maré de Tempestade",
        "occurred_at": datetime(2016, 10, 28, 9, 0, tzinfo=timezone.utc),
        "severity": "Alta",
        "description": "Ciclone no Atlântico Sul gerou maré meteorológica extrema, avançando pelas dunas até a Av. Beira-Mar no Cassino.",
        "source": "Boletim IO-FURG / Defesa Civil",
        "reliability": "relatório técnico"
    },
    {
        "municipality": "Pelotas",
        "region_name": "Centro",
        "event_type": "Interdição Rodoviária / Enxurrada",
        "occurred_at": datetime(2015, 10, 15, 16, 0, tzinfo=timezone.utc),
        "severity": "Média",
        "description": "Acúmulo de água na pista e restrições de tráfego nos acessos rodoviários do Polo Pelotas (BR-116 / BR-392) após temporal.",
        "source": "PRF / Ecosul",
        "reliability": "confirmado"
    },
    {
        "municipality": "São Lourenço do Sul",
        "region_name": "Sede Urbana / Orla",
        "event_type": "Enxurrada / Inundação Brusca",
        "occurred_at": datetime(2011, 3, 10, 6, 0, tzinfo=timezone.utc),
        "severity": "Alta",
        "description": "Transbordamento severo do Arroio São Lourenço após precipitação extrema acumulada na serra do Sudeste.",
        "source": "S2iD / Atlas CEPED-UFSC",
        "reliability": "confirmado"
    },
    {
        "municipality": "Chuí",
        "region_name": "Sede Urbana",
        "event_type": "Alagamento Urbano / Fronteira",
        "occurred_at": datetime(2020, 5, 22, 11, 0, tzinfo=timezone.utc),
        "severity": "Baixa",
        "description": "Acumulado expressivo de chuva provocando alagamento de vias urbanas na faixa de fronteira devido à drenagem lenta em relevo plano.",
        "source": "Imprensa Local / Defesa Civil",
        "reliability": "noticiado / imprensa"
    }
]


def seed_occurrences():
    app = create_app()
    with app.app_context():
        inserted = 0
        for ev in HISTORICAL_EVENTS:
            region = Region.query.filter_by(
                municipality=ev["municipality"],
                name=ev["region_name"]
            ).first()
            if not region:
                continue

            exists = Occurrence.query.filter_by(
                region_id=region.id,
                event_type=ev["event_type"],
                occurred_at=ev["occurred_at"]
            ).first()

            if not exists:
                occ = Occurrence(
                    region_id=region.id,
                    event_type=ev["event_type"],
                    occurred_at=ev["occurred_at"],
                    severity=ev["severity"],
                    description=ev["description"],
                    source=ev["source"],
                    reliability=ev["reliability"]
                )
                db.session.add(occ)
                inserted += 1

        db.session.commit()
        total = Occurrence.query.count()
        print(f"Histórico atualizado! {inserted} novos eventos inseridos (Total no banco: {total}).")


if __name__ == "__main__":
    seed_occurrences()
