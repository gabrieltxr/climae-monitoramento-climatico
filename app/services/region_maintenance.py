"""
Fusão e remoção de regiões levando junto TODAS as tabelas que apontam para regions.id.

As tabelas dependentes são descobertas pelas chaves estrangeiras dos modelos: uma tabela
nova com region_id entra sozinha, sem precisar lembrar de atualizar esta lista.
"""
from app.models import db, Region, WeatherObservation, MonthlyClimateSummary


def region_dependent_columns() -> list[tuple[type, object]]:
    """(modelo, coluna) de cada tabela com chave estrangeira para regions.id."""
    found = []
    for mapper in db.Model.registry.mappers:
        for column in mapper.local_table.columns:
            if any(fk.column.table.name == Region.__tablename__ and fk.column.name == "id"
                   for fk in column.foreign_keys):
                found.append((mapper.class_, column))
    return sorted(found, key=lambda item: item[0].__tablename__)


def _drop_overlapping_rows(old_id: int, target_id: int) -> dict:
    """
    Apaga da região antiga o que o destino já tem, para a fusão não duplicar:
    - histórico mensal: unique(region_id, year, month) quebraria o UPDATE;
    - observações do mesmo horário: somariam duas vezes a chuva nos alertas e no risco.
    Em conflito, vale o dado do destino.
    """
    target_months = set(
        db.session.query(MonthlyClimateSummary.year, MonthlyClimateSummary.month)
        .filter(MonthlyClimateSummary.region_id == target_id)
    )
    month_ids = [
        row.id for row in
        db.session.query(MonthlyClimateSummary.id, MonthlyClimateSummary.year, MonthlyClimateSummary.month)
        .filter(MonthlyClimateSummary.region_id == old_id)
        if (row.year, row.month) in target_months
    ]

    target_hours = set(
        db.session.query(WeatherObservation.observed_at, WeatherObservation.is_forecast)
        .filter(WeatherObservation.region_id == target_id)
    )
    obs_ids = [
        row.id for row in
        db.session.query(WeatherObservation.id, WeatherObservation.observed_at, WeatherObservation.is_forecast)
        .filter(WeatherObservation.region_id == old_id)
        if (row.observed_at, row.is_forecast) in target_hours
    ]

    for model, ids in ((MonthlyClimateSummary, month_ids), (WeatherObservation, obs_ids)):
        if ids:
            db.session.query(model).filter(model.id.in_(ids)).delete(synchronize_session=False)
    return {"monthly_climate_summaries": len(month_ids), "weather_observations": len(obs_ids)}


def merge_region_into(old: Region, target: Region) -> dict:
    """Move tudo da região `old` para `target` e apaga `old`. Retorna o que foi movido/descartado."""
    if old.id == target.id:
        raise ValueError("Região de origem e destino são a mesma.")
    old_id, target_id = old.id, target.id

    dropped = _drop_overlapping_rows(old_id, target_id)
    moved = {}
    for model, column in region_dependent_columns():
        count = db.session.query(model).filter(column == old_id).update(
            {column: target_id}, synchronize_session=False
        )
        if count:
            moved[model.__tablename__] = count

    # Objetos já carregados ainda têm o region_id antigo; recarrega antes de apagar.
    db.session.expire_all()
    db.session.delete(db.session.get(Region, old_id))
    db.session.commit()
    return {"moved": moved, "dropped_duplicates": {k: v for k, v in dropped.items() if v}}


def delete_region_with_dependents(region: Region) -> dict:
    """Apaga a região e todas as linhas que apontam para ela."""
    region_id = region.id
    deleted = {}
    for model, column in region_dependent_columns():
        count = db.session.query(model).filter(column == region_id).delete(synchronize_session=False)
        if count:
            deleted[model.__tablename__] = count
    db.session.expire_all()
    db.session.delete(db.session.get(Region, region_id))
    db.session.commit()
    return deleted
