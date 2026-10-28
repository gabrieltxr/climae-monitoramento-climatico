from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Region(db.Model):
    __tablename__ = 'regions'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)          # Ex: "Centro", "Cassino", "Zona Rural"
    municipality = db.Column(db.String(100), nullable=False)  # Ex: "Pelotas", "Rio Grande"
    city = db.synonym('municipality')
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    geometry = db.Column(db.Text, nullable=True)              # GeoJSON ou WKT (para uso futuro com PostGIS)

    def __init__(self, **kwargs):
        if "city" in kwargs and "municipality" not in kwargs:
            kwargs["municipality"] = kwargs.pop("city")
        super().__init__(**kwargs)

    @property
    def city(self):
        return self.municipality

    @city.setter
    def city(self, value):
        self.municipality = value

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "municipality": self.municipality,
            "city": self.municipality,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "geometry": self.geometry,
        }

class WeatherObservation(db.Model):
    __tablename__ = 'weather_observations'
    id = db.Column(db.Integer, primary_key=True)
    region_id = db.Column(db.Integer, db.ForeignKey('regions.id'), nullable=False)
    observed_at = db.Column(db.DateTime(timezone=True), nullable=False) # Sempre em UTC
    collected_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    is_forecast = db.Column(db.Boolean, default=False, nullable=False)  # Diferencia observado vs previsto
    temperature_c = db.Column(db.Float, nullable=True)
    humidity_percent = db.Column(db.Float, nullable=True)
    precipitation_mm = db.Column(db.Float, nullable=True)
    rain_probability = db.Column(db.Float, nullable=True)
    wind_speed = db.Column(db.Float, nullable=True)
    wind_direction = db.Column(db.Float, nullable=True)  # <-- NOVO (v0.2): Direção em graus (0-360)
    source = db.Column(db.String(50), nullable=False)                   # Ex: "Open-Meteo"

    __table_args__ = (
        db.Index('ix_weather_obs_lookup', 'region_id', 'is_forecast', 'observed_at'),
        db.Index('ix_weather_obs_collected_at', 'collected_at'),
    )

class Occurrence(db.Model):
    __tablename__ = 'occurrences'
    id = db.Column(db.Integer, primary_key=True)
    region_id = db.Column(db.Integer, db.ForeignKey('regions.id'), nullable=False)
    event_type = db.Column(db.String(50), nullable=False)               # Ex: "Alagamento", "Vendaval"
    occurred_at = db.Column(db.DateTime(timezone=True), nullable=False)
    severity = db.Column(db.String(30), nullable=True)
    description = db.Column(db.Text, nullable=True)
    source = db.Column(db.String(100), nullable=False)                  # Ex: "Defesa Civil", "S2iD"
    reliability = db.Column(db.String(30), default='confirmado')

    __table_args__ = (
        db.Index('ix_occurrences_region_occurred', 'region_id', 'occurred_at'),
    )

class TrafficEvent(db.Model):
    __tablename__ = 'traffic_events'
    id = db.Column(db.Integer, primary_key=True)
    highway = db.Column(db.String(120), nullable=True, index=True)      # Ex: "BR-116", "BR-392"
    status = db.Column(db.String(50), nullable=False)                   # Normal, Atenção, Alerta, etc.
    description = db.Column(db.Text, nullable=True)                     # Detalhamento da pista / tráfego
    source = db.Column(db.String(100), nullable=False)                  # Ex: "PRF / Ecosul + Telemetria CLIMAE"
    reported_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Campos de compatibilidade para consultas legadas
    region_id = db.Column(db.Integer, db.ForeignKey('regions.id'), nullable=True)
    event_type = db.Column(db.String(100), nullable=True)
    occurred_at = db.Column(db.DateTime(timezone=True), nullable=True)
    source_updated_at = db.Column(db.DateTime(timezone=True), nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "highway": self.highway,
            "status": self.status,
            "description": self.description,
            "source": self.source,
            "reported_at": self.reported_at.isoformat() if self.reported_at else None,
            "region_id": self.region_id,
            "event_type": self.event_type or self.highway,
        }

class RiskAssessment(db.Model):
    __tablename__ = 'risk_assessments'
    id = db.Column(db.Integer, primary_key=True)
    region_id = db.Column(db.Integer, db.ForeignKey('regions.id'), nullable=False)
    assessed_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    risk_level = db.Column(db.String(30), nullable=False)               # Baixo, Moderado, Alto, Muito alto, Inconclusivo
    score = db.Column(db.Float, nullable=True)                          # 0 a 100
    factors = db.Column(db.JSON, nullable=True)                         # Pesos e justificativas em JSON
    method_version = db.Column(db.String(20), nullable=False)           # Ex: "v0.1-mvp"

    __table_args__ = (
        db.Index('ix_risk_assessments_region_assessed', 'region_id', 'assessed_at'),
    )

class Alert(db.Model):
    __tablename__ = 'alerts'
    id = db.Column(db.Integer, primary_key=True)
    region_id = db.Column(db.Integer, db.ForeignKey('regions.id'), nullable=False)
    triggered_rule = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    channel = db.Column(db.String(30), nullable=False)                  # Ex: "email", "dashboard"
    status = db.Column(db.String(30), nullable=False)                   # Ex: "enviado", "pendente", "erro"
    dedup_key = db.Column(db.String(120), unique=True, nullable=False)  # Evita alertas duplicados

    region = db.relationship('Region', backref='alerts', lazy=True)

    __table_args__ = (
        db.Index('ix_alerts_region_created', 'region_id', 'created_at'),
    )

    @property
    def timestamp(self):
        return self.created_at

    @property
    def rule_triggered(self):
        return self.triggered_rule


class MonthlyClimateSummary(db.Model):
    """
    Armazena estatísticas mensais consolidadas (2000 até o presente) e Normais Climatológicas.
    - Quando year > 0 (ex: 2024, month=5): representa o histórico real consolidado daquele mês/ano.
    - Quando year == 0 (month=1..12): representa a Média Histórica (Normal Climatológica) daquele mês.
    """
    __tablename__ = 'monthly_climate_summaries'

    id = db.Column(db.Integer, primary_key=True)
    region_id = db.Column(db.Integer, db.ForeignKey('regions.id'), nullable=False, index=True)
    year = db.Column(db.Integer, nullable=False, index=True)   # 0 = Média Histórica (Climatologia) | 2000..2026 = Ano específico
    month = db.Column(db.Integer, nullable=False, index=True)  # 1 (Janeiro) a 12 (Dezembro)

    avg_temp_c = db.Column(db.Float, nullable=True)            # Temperatura média do mês (°C)
    max_temp_c = db.Column(db.Float, nullable=True)            # Temperatura máxima registrada/média (°C)
    min_temp_c = db.Column(db.Float, nullable=True)            # Temperatura mínima registrada/média (°C)
    total_rain_mm = db.Column(db.Float, nullable=True)         # Chuva acumulada no mês (mm)
    rainy_days = db.Column(db.Integer, nullable=True)          # Dias com precipitação >= 1.0 mm
    max_wind_kmh = db.Column(db.Float, nullable=True)          # Pico de vento no mês (km/h)
    source = db.Column(db.String(80), default='Open-Meteo ERA5 Reanalysis')
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    __table_args__ = (
        db.UniqueConstraint('region_id', 'year', 'month', name='uq_region_year_month'),
    )


class PowerOutageReport(db.Model):
    """
    Relatos colaborativos de falta de energia registrados pelos moradores (Crowdsourcing).
    Utiliza hash anônimo de visitante para proteção anti-spam com cooldown de 30 minutos.
    """
    __tablename__ = 'power_outage_reports'

    id = db.Column(db.Integer, primary_key=True)
    region_id = db.Column(db.Integer, db.ForeignKey('regions.id'), nullable=False, index=True)
    reported_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    reporter_hash = db.Column(db.String(64), nullable=False, index=True)
    comment = db.Column(db.String(140), nullable=True)
    is_active = db.Column(db.Boolean, default=True, index=True)

    __table_args__ = (
        db.Index('ix_power_outage_region_time', 'region_id', 'reported_at'),
    )


class PowerRestorationReport(db.Model):
    """
    Relatos colaborativos de restabelecimento da energia elétrica ("A Luz Voltou").
    Permite aos moradores confirmar o retorno do fornecimento elétrico, desativando
    alertas pendentes de queda e sinalizando a normalização do serviço.
    """
    __tablename__ = 'power_restoration_reports'

    id = db.Column(db.Integer, primary_key=True)
    region_id = db.Column(db.Integer, db.ForeignKey('regions.id'), nullable=False, index=True)
    restored_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    reporter_hash = db.Column(db.String(64), nullable=False, index=True)
    comment = db.Column(db.String(140), nullable=True)

    __table_args__ = (
        db.Index('ix_power_restoration_region_time', 'region_id', 'restored_at'),
    )


class PowerGridProbe(db.Model):
    """
    Sondas de conectividade virtual de rede (Nós multi-operadora / gateways públicos).
    Permite avaliar a disponibilidade da infraestrutura elétrica através do monitoramento de nós de rede,
    distinguindo quedas gerais de energia de simples rompimentos de fibra ótica de uma única operadora.
    """
    __tablename__ = 'power_grid_probes'

    id = db.Column(db.Integer, primary_key=True)
    region_id = db.Column(db.Integer, db.ForeignKey('regions.id'), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)          # Ex: "Nó FURG Carreiros", "Gateway Vivo Centro"
    target_host = db.Column(db.String(120), nullable=False)   # IP ou hostname público
    target_port = db.Column(db.Integer, default=80)           # Porta TCP para checagem não intrusiva (80, 443, 53)
    provider_name = db.Column(db.String(50), nullable=False) # Ex: "Claro", "Vivo", "Oi", "RNP/Acadêmica"
    is_up = db.Column(db.Boolean, default=True, index=True)
    latency_ms = db.Column(db.Integer, nullable=True)
    last_checked_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    region = db.relationship('Region', backref='probes', lazy=True)

    __table_args__ = (
        db.Index('ix_probe_region_provider', 'region_id', 'provider_name'),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "region_id": self.region_id,
            "name": self.name,
            "target_host": self.target_host,
            "target_port": self.target_port,
            "provider_name": self.provider_name,
            "is_up": self.is_up,
            "latency_ms": self.latency_ms,
            "last_checked_at": self.last_checked_at.isoformat() if self.last_checked_at else None,
        }


class PowerUtilityBulletin(db.Model):
    """
    Boletins e comunicados oficiais emitidos pelas concessionárias (CEEE Grupo Equatorial, RGE) e Defesa Civil.
    Permite informar a população sobre contingências, estimativas de restabelecimento e equipes em campo.
    """
    __tablename__ = 'power_utility_bulletins'

    id = db.Column(db.Integer, primary_key=True)
    municipality = db.Column(db.String(60), nullable=False, index=True)
    region_id = db.Column(db.Integer, db.ForeignKey('regions.id'), nullable=True)
    source = db.Column(db.String(80), default="CEEE Grupo Equatorial")
    headline = db.Column(db.String(160), nullable=False)
    body = db.Column(db.Text, nullable=False)
    affected_customers = db.Column(db.Integer, nullable=True)
    severity = db.Column(db.String(30), default="alerta")           # informativo, alerta, critico
    status = db.Column(db.String(30), default="ativo", index=True)  # ativo, normalizado
    published_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "municipality": self.municipality,
            "region_id": self.region_id,
            "source": self.source,
            "headline": self.headline,
            "body": self.body,
            "affected_customers": self.affected_customers,
            "severity": self.severity or "alerta",
            "status": self.status or "ativo",
            "published_at": self.published_at.isoformat() if self.published_at else None,
        }


