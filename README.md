# CLIMAE — Monitoramento de Eventos Climáticos da Zona Sul do RS (MVP)

Aplicação web modular em Python (Flask) para monitoramento meteorológico em tempo real, catálogo histórico de ocorrências, situação rodoviária e avaliação experimental de risco climático para municípios da Zona Sul do Rio Grande do Sul.

---

## 1. Escopo Funcional (6 Módulos do MVP)
1. **01. Dashboard Meteorológico:** Temperatura, umidade, vento (10m), precipitação atual, probabilidade de chuva e gráfico de tendência para as próximas 24h (Open-Meteo API), com atualização automática em segundo plano a cada **15 minutos**.
2. **02. Mapa Geoespacial Interativo:** Visualização via Leaflet + OpenStreetMap/CartoDB Dark Matter com camada de atenção regional e pinos de ocorrências históricas.
3. **03. Histórico de Ocorrências:** Registro de eventos documentados (Defesa Civil, S2iD, ANA, Praticagem da Barra, SANEP, PRF/Ecosul) e anomalias históricas (Open-Meteo Archive 2000–2026), com filtros por localidade e período.
4. **04. Índice Inicial de Risco (`v0.1-mvp`):** Pontuação explicável de 0 a 100 pontos.
5. **05. Situação das Rodovias:** Monitoramento de boletins oficiais (PRF e Ecosul) nos eixos da BR-116, BR-392 e BR-471.
6. **06. Alertas Automáticos:** Geração de alertas para chuva intensa ($\ge$ 25mm/24h), vendaval ($\ge$ 55km/h) e risco elevado ($\ge$ 50 pts), com controle de deduplicação diária (`dedup_key`) e suporte a envio por e-mail (SMTP).

---

## 2. Regras do Índice de Risco Experimental (`v0.1-mvp`)
O cálculo utiliza pesos explícitos (0 a 100 pontos):
* **Precipitação prevista e acumulada (40% / máx. 40 pts):** Combina o volume esperado em 24h (até 30 pts) e a probabilidade máxima de chuva (até 10 pts).
* **Vulnerabilidade e histórico local (35% / máx. 35 pts):** Considera o histórico documentado na região. Regiões sem histórico recebem piso cautelar provisório (15 pts) e marcação explícita.
* **Condições complementares (25% / máx. 25 pts):** Considera pico de vento previsto/observado (até 18 pts), saturação de umidade (até 4 pts) e retenções/interdições rodoviárias ativas (3 pts).

**Classificação:** `0–24` Baixo | `25–49` Moderado | `50–74` Alto | `75–100` Muito alto.
> ⚠️ *Nota: Ferramenta experimental de apoio à decisão. Não substitui alertas oficiais da Defesa Civil ou do INMET.*

---

## 3. Como Executar Localmente

### Pré-requisitos
* Python 3.11+
* SQLite (padrão imediato) ou PostgreSQL

### Passo a passo
```bash
# 1. Criar e ativar o ambiente virtual
python -m venv venv
venv\Scripts\activate      # Windows
# source venv/bin/activate # Linux / WSL

# 2. Instalar dependências
pip install -r requirements.txt

# 3. Popular regiões, ocorrências documentadas e coletar dados atuais
python scripts/seed_regions.py
python scripts/seed_occurrences.py
python scripts/collect_weather.py

# 4. (Opcional) Buscar anomalias históricas no Open-Meteo Archive
python scripts/find_historical_extremes.py --start 2023-01-01 --end 2026-10-10 --save

# 5. Executar suíte de testes automatizados
pytest -v

# 6. Iniciar o servidor
python run.py