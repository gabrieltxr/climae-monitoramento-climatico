from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Region(db.Model):
    __tablename__ = 'regions'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)          # Ex: "Centro", "Cassino", "Zona Rural"
    municipality = db.Column(db.String(100), nullable=False)  # Ex: "Pelotas", "Rio Grande"
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    geometry = db.Column(db.Text, nullable=True)              # GeoJSON ou WKT (para uso futuro com PostGIS)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "municipality": self.municipality,
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
    source = db.Column(db.String(50), nullable=False)                   # Ex: "Open-Meteo"

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

class TrafficEvent(db.Model):
    __tablename__ = 'traffic_events'
    id = db.Column(db.Integer, primary_key=True)
    region_id = db.Column(db.Integer, db.ForeignKey('regions.id'), nullable=False)
    event_type = db.Column(db.String(50), nullable=False)               # Ex: "Interdição", "Lentidão"
    status = db.Column(db.String(30), nullable=False)                   # Ex: "Ativo", "Desatualizado"
    occurred_at = db.Column(db.DateTime(timezone=True), nullable=False)
    source = db.Column(db.String(50), nullable=False)
    source_updated_at = db.Column(db.DateTime(timezone=True), nullable=True)

class RiskAssessment(db.Model):
    __tablename__ = 'risk_assessments'
    id = db.Column(db.Integer, primary_key=True)
    region_id = db.Column(db.Integer, db.ForeignKey('regions.id'), nullable=False)
    assessed_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    risk_level = db.Column(db.String(30), nullable=False)               # Baixo, Moderado, Alto, Muito alto, Inconclusivo
    score = db.Column(db.Float, nullable=True)                          # 0 a 100
    factors = db.Column(db.JSON, nullable=True)                         # Pesos e justificativas em JSON
    method_version = db.Column(db.String(20), nullable=False)           # Ex: "v0.1-mvp"

class Alert(db.Model):
    __tablename__ = 'alerts'
    id = db.Column(db.Integer, primary_key=True)
    region_id = db.Column(db.Integer, db.ForeignKey('regions.id'), nullable=False)
    triggered_rule = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    channel = db.Column(db.String(30), nullable=False)                  # Ex: "email", "dashboard"
    status = db.Column(db.String(30), nullable=False)                   # Ex: "enviado", "pendente", "erro"
    dedup_key = db.Column(db.String(120), unique=True, nullable=False)  # Evita alertas duplicados
