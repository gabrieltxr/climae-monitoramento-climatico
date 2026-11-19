# CLIMAE — Sistema de Monitoramento Climático da Zona Sul do RS

[![Python Version](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![Framework](https://img.shields.io/badge/framework-Flask-lightgrey.svg)](https://flask.palletsprojects.com/)
[![Database](https://img.shields.io/badge/database-SQLite%20%2F%20SQLAlchemy-green.svg)](https://www.sqlite.org/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Aplicação web modular em Python (Flask) desenvolvida para monitoramento meteorológico baseado em modelos numéricos, avaliação contínua de risco geoespacial, telemetria rodoviária e análise climatológica histórica para **56 regiões e bairros distribuídos em 8 municípios da Zona Sul do Rio Grande do Sul**.

> **Sobre os dados meteorológicos:** as condições "atuais" e das últimas 12 horas do
> painel são **estimativas dos modelos** (ECMWF, GFS e ICON, via API de previsão do
> Open-Meteo), não leituras de pluviômetro ou anemômetro. Com um token do INMET
> configurado, o painel também mostra a medição da estação automática mais próxima
> (até 30 km) e os alertas das últimas 12h passam a usar o dado medido (ver seção 3).

---

## 1. Módulos do Sistema (7 Painéis Integrados)

### 01. Dashboard Meteorológico (Modelos Numéricos)
* **Consenso Multi-Modelo:** Coleta sincronizada dos modelos **Best Match**, **ECMWF IFS 0.25°** e **GFS Seamless** via Open-Meteo API.
* **Métricas Principais:** Temperatura, sensação térmica, umidade relativa, pressão, ponto de orvalho, velocidade e direção do vento (10m), precipitação acumulada e probabilidade de chuva.
* **Tendência Horária:** Gráficos interativos para projeção das próximas 24 horas.
* **Atualização Automática:** Motor em segundo plano (APScheduler) a cada 15 minutos com tolerância a falhas.

### 02. Mapa Geoespacial Interativo & Despoluído
* **Visualização Cartográfica:** Leaflet com base de alta visibilidade CartoDB Dark Matter.
* **3 Camadas Dinâmicas:**
  1. **Riscos & Condições:** Severidade e classificação por cor de cada localidade.
  2. **Vetores de Vento:** Setas orientadas reais com velocidade em km/h e cálculo vetorial cartesiano ($u, v$).
  3. **Massas de Ar (Térmica):** Gradiente térmico contínuo com sensação e umidade.
* **Arquitetura de Zoom Responsivo (Padrão Windy / Progressive Disclosure):**
  * **Visão Regional (Zoom < 11):** Agrupa os 56 bairros em **8 macro-clusters municipais**, exibindo valores médios sem poluição visual ou sobreposição de etiquetas.
  * **Visão Municipal (Zoom 11):** Transição suave para marcadores compactos onde apenas a região selecionada tem destaque completo; os demais exibem métricas via tooltip translúcido no `hover`.
  * **Revelação Progressiva (Zoom 12 a 14+):** Conforme o zoom se aproxima, bairros vizinhos (raio de 8 km a 22 km) revelam automaticamente seus nomes e rótulos de forma harmoniosa e sem colisão.
* **Persistência de Região:** Sincronização em dupla camada (`localStorage` + cookie HTTP) para manter a seleção do usuário mesmo após recarregar a página.

### 03. Índice de Risco Climático Dinâmico (`v0.2-dinâmico`)
* Pontuação explicável de 0 a 100 pontos calculada por motor probabilístico ponderado.
* **Pesos Analíticos:**
  * **Precipitação e Saturação (40% / máx. 40 pts):** Volume acumulado/previsto em 24h e probabilidade de tempestade.
  * **Histórico e Vulnerabilidade Local (35% / máx. 35 pts):** Densidade de ocorrências históricas na região com baseline calibrado.
  * **Vento e Tráfego Rodoviário (25% / máx. 25 pts):** Pico de rajadas de vento, umidade relativa crítica e retenções/bloqueios rodoviários ativos.
* **Faixas de Severidade:** `0–24` Baixo | `25–49` Moderado | `50–74` Alto | `75–100` Muito Alto.

### 04. Situação das Rodovias e Corredores de Tráfego
* Monitoramento dos 7 eixos rodoviários estratégicos da região: **BR-116**, **BR-392**, **BR-471** e **ERS-265**.
* Condições de pista, interdições ativas, pontos de alagamento e integração direta no cálculo do índice de risco local.

### 05. Central de Alertas e Normalizações
* Geração automatizada de alertas operacionais para:
  * Chuva Intensa ($\ge$ 25 mm/24h)
  * Vendaval e Rajadas Fortes ($\ge$ 55 km/h)
  * Índice de Risco Crítico ($\ge$ 50 pts)
* **Paginação Server-Side:** Navegação fluida e eficiente na tabela de eventos.
* **Sem alertas repetidos:** a mesma regra não dispara de novo na mesma região antes de 12 horas (`ALERT_REPEAT_HOURS` no `.env`); um evento longo é relembrado no máximo a cada 12h.

### 06. Histórico Climático Mensal & Normais Climatológicas (ERA5)
* **100% de Cobertura das 56 Regiões:** Mais de **18.700 registros consolidados**.
* **Série Histórica Contínua:** Dados mensais de **2000 a 2026** obtidos via reanálise global ERA5 (Open-Meteo Archive).
* **Normais Climatológicas:** Médias históricas dos 12 meses do ano para comparação de desvios e anomalias de temperatura (°C) e precipitação (mm).
* **Projeção Sazonal:** Diagnóstico automático comparando o mês corrente com o padrão histórico da localidade.

### 07. Histórico de Eventos e Ocorrências Documentadas
* Registro georreferenciado de eventos adversos (alagamentos, destelhamentos, quedas de árvores, ressacas).
* Fontes integradas: Defesa Civil RS, S2iD, ANA, Praticagem da Barra, SANEP e PRF/Ecosul.
* Filtros dinâmicos por município, bairro, nível de severidade e período.

---

## 2. Municípios e Bairros Monitorados (56 Regiões)

O sistema cobre 8 municípios do extremo sul gaúcho (lista mantida em `app/regions_catalog.py`):
1. **Rio Grande:** Centro, Cidade Nova, Vila Junção, Parque Marinha, Parque São Pedro, Cassino, Ilha dos Marinheiros.
2. **Pelotas:** Centro, Laranjal, Areal, Três Vendas, Fragata, São Gonçalo, Colônia / Cascata.
3. **São José do Norte:** Centro, Quinta Secção / Barra, Guarida / Tamandaré, São Caetano, Mar Grosso, Estreito, Bojuru.
4. **São Lourenço do Sul:** Centro, Praia / Barrinha, Lomba / BR-116, Navegantes, Boa Vista, Boqueirão, Harmonia.
5. **Canguçu:** Centro, Vila Nova / ERS-265, Canguçu Velho, Iguatemi, Herval - 3º Dist., Armada, Coxilha dos Campos.
6. **Jaguarão:** Centro, Cerro da Pólvora, Carvalho / BR-116, Vila Kennedy, Ponta Alegre / Mirim, Cerrito / BR-116, Passo do Centurião.
7. **Santa Vitória do Palmar:** Centro, Porto / Lagoa Mirim, Coxilha / BR-471, Praia do Hermenegildo, Barra do Chuí, Curral Alto, Taim / Capilha.
8. **Chuí:** Centro, Acesso Norte / BR-471, Vila América, Bairro Juventude, Várzea do Arroio Chuí, Estrada da Barra, Colônia São João.

---

## 3. Como Executar o Projeto Localmente

### Pré-requisitos
* Python 3.11, 3.12 ou 3.13
* Gerenciador de pacotes `pip` e ambiente virtual `venv`

### Passo a Passo

```bash
# 1. Clonar o repositório
git clone https://github.com/gabrieltxr/climae-monitoramento-climatico.git
cd climae-monitoramento-climatico

# 2. Criar e ativar o ambiente virtual
python -m venv venv
venv\Scripts\activate      # Windows (PowerShell / CMD)
# source venv/bin/activate # Linux / macOS

# 3. Instalar as dependências
pip install -r requirements.txt

# 4. Inicializar o banco de dados e popular as 56 regiões
python scripts/seed_regions.py
python scripts/seed_occurrences.py

# 5. Coletar os dados meteorológicos e de tráfego iniciais
python scripts/collect_weather.py

# 6. Sincronizar o histórico climático mensal (ERA5 2000-2026)
python scripts/sync_climate_history.py

# 7. Executar a suíte de testes unitários automatizados
pytest -v

# 8. Iniciar o servidor de desenvolvimento
python run.py
```

Acesse a aplicação no navegador em: **`http://127.0.0.1:5000`**

> **Servindo via WSGI (gunicorn, waitress etc.):** o agendador de coleta só sobe junto
> com a web em `python run.py`. Ao servir `run:app` por um servidor WSGI, rode o
> agendador em **um único** processo separado com `python scripts/run_scheduler.py`
> (e não rode `run.py` ao mesmo tempo, para não duplicar as coletas).

> **SECRET_KEY:** é o sal do hash anônimo dos relatos de energia. Defina uma chave
> própria no `.env` (`python -c "import secrets; print(secrets.token_hex(32))"`).
> Com `CLIMAE_HOST` fora de 127.0.0.1, o `run.py` recusa subir com chave vazia ou
> pública. Via WSGI essa verificação não roda: defina a chave antes de publicar.

> **Estações do INMET (opcional):** peça um token por e-mail a `cadastro.act@inmet.gov.br`
> e coloque-o em `INMET_TOKEN` no `.env`. A cada ciclo o agendador lê ontem e hoje das 10
> estações automáticas da região (Rio Grande, Capão do Leão/Pelotas, Jaguarão, Canguçu,
> São Lourenço do Sul, Barra do Chuí, Santa Vitória do Palmar, Taim, Herval e Pinheiro
> Machado). Cada bairro usa a estação mais próxima que esteja publicando, dentro de
> `INMET_MAX_DISTANCE_KM` (padrão 30 km); fora do raio — hoje, parte de São José do Norte
> e de Santa Vitória do Palmar — segue só com os modelos. São dados brutos (o INMET não os
> valida) e ficam guardados por 7 dias.

---

## 4. Scripts e Utilitários de Manutenção

* `scripts/seed_regions.py`: Cadastra ou atualiza as 56 regiões no banco de dados SQLite.
* `scripts/seed_occurrences.py`: Insere a base histórica de ocorrências documentadas.
* `scripts/collect_weather.py`: Executa um ciclo manual de coleta meteorológica e avaliação de risco.
* `scripts/run_scheduler.py`: Executa só o agendador (coleta, purga e consolidação mensal) em processo próprio, para uso com servidor WSGI.
* `scripts/sync_climate_history.py`: Audita a integridade do banco e sincroniza a climatologia ERA5 das 56 regiões.
* `scripts/seed_monthly_climate.py`: Rotina avançada de carga direta da API ERA5 Open-Meteo.
* `scripts/cleanup_all_regions.py`: Limpeza e reestruturação atômica das regiões.

### Publicar boletins oficiais de energia

Ainda não há tela para isso (prevista para a reconstrução do frontend); a publicação é
feita direto pela API, com um token de administrador.

1. Defina no `.env` um token longo e aleatório e reinicie o servidor
   (vazio = publicação desativada):
   `ADMIN_API_TOKEN=` + o resultado de `python -c "import secrets; print(secrets.token_hex(32))"`
2. Envie o boletim com o token no header `X-Admin-Token`:

```bash
curl -X POST http://127.0.0.1:5000/api/power/bulletins \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: SEU_TOKEN" \
  -d '{"municipality": "Pelotas", "headline": "Interrupção no Centro", "body": "Equipes em campo; previsão de retorno às 18h.", "affected_customers": 1200, "severity": "critico"}'
```

| Campo | Obrigatório | Observação |
| --- | --- | --- |
| `municipality` | sim | Município como cadastrado nas regiões; `Zona Sul`, `Geral` ou `Rio Grande do Sul` valem para todas as regiões. |
| `headline`, `body` | sim | Título e texto do comunicado. |
| `affected_customers` | não | Acima de **500**, o painel da região passa a "Interrupção de Energia Confirmada pela Concessionária". |
| `severity` | não | `informativo`, `alerta` (padrão) ou `critico`. |
| `status` | não | `ativo` (padrão). Só boletins ativos das últimas **48 h** aparecem no painel. |
| `source` | não | Padrão: `CEEE Grupo Equatorial`. |
| `region_id` | não | Região específica (opcional; o painel filtra pelo município). |

A data de publicação é sempre o momento do envio. Resposta `201` = publicado;
`403` = token ausente ou errado; `400` = campo obrigatório faltando.

---

## 5. Licença e Aviso Legal

Distribuído sob a licença MIT. Consulte `LICENSE` para mais informações.

> ⚠️ **Aviso:** O CLIMAE é uma ferramenta acadêmica e experimental de apoio e visualização situacional. Seus índices de risco não substituem os alertas e diretrizes oficiais emitidos pela **Defesa Civil**, pelo **INMET** ou pelas autoridades públicas competentes.
