"""
Avisos meteorológicos oficiais do INMET (alertas do Sistema de Avisos, os mesmos de
avisos.inmet.gov.br). API pública, sem token; derruba conexões sem User-Agent de navegador.

A resposta traz os avisos de todo o país em {"hoje": [...], "futuro": [...]} (~750 KB, dois
terços só da lista de municípios em texto). O servidor não comprime nem informa ETag/
Last-Modified, mas responde HEAD com o Content-Length exato: a cada ciclo o coletor faz só o
HEAD (~2 KB) e baixa tudo quando o tamanho muda ou quando passa
INMET_AVISOS_FULL_REFRESH_MINUTES (padrão 60) desde o último download completo, para pegar
mudanças que não alteram o tamanho. Uma região está sob aviso quando o geocode IBGE do seu município
consta na lista oficial do aviso (o polígono é só um contorno aproximado e deixa de fora
municípios que o INMET lista).

Horários publicados em horário de Brasília (o CAP do mesmo aviso confirma -03:00); aqui são
guardados em UTC.
"""
import logging
import os
from datetime import datetime, timedelta, timezone

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.models import db, Region, OfficialWarning
from app.regions_catalog import MUNICIPALITY_GEOCODES, GEOCODE_MUNICIPALITIES
from app.services.alert_engine import send_email_notification

logger = logging.getLogger(__name__)

WARNINGS_URL = "https://apiprevmet3.inmet.gov.br/avisos/ativos"
REQUEST_TIMEOUT = (10, 30)
USER_AGENT = "Mozilla/5.0 (compatible; CLIMAE/1.0)"
BRT = timezone(timedelta(hours=-3))
MONITORED_GEOCODES = frozenset(MUNICIPALITY_GEOCODES.values())
DEFAULT_FULL_REFRESH_MINUTES = 60.0

# Último download completo processado (só no processo do agendador; ao reiniciar, baixa tudo).
_last_download = {"length": None, "at": None}


def _create_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=2, backoff_factor=1.0, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET", "HEAD"])
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return session


http_session = _create_session()


def reset_download_state() -> None:
    _last_download.update(length=None, at=None)


def get_full_refresh_minutes() -> float:
    try:
        value = float(os.getenv("INMET_AVISOS_FULL_REFRESH_MINUTES", DEFAULT_FULL_REFRESH_MINUTES))
    except ValueError:
        return DEFAULT_FULL_REFRESH_MINUTES
    return value if value > 0 else DEFAULT_FULL_REFRESH_MINUTES


def fetch_content_length() -> int | None:
    """Tamanho atual da resposta pelo HEAD; None se o HEAD falhar ou vier sem tamanho."""
    try:
        res = http_session.head(WARNINGS_URL, timeout=REQUEST_TIMEOUT, allow_redirects=True)
    except requests.RequestException as exc:
        logger.info("Avisos INMET: HEAD falhou (%s); baixando completo.", type(exc).__name__)
        return None
    length = str(res.headers.get("Content-Length", "")).strip()
    if res.status_code != 200 or not length.isdigit():
        return None
    return int(length)


def needs_full_download(now_naive: datetime) -> bool:
    """Baixa tudo se não houver download anterior, se o HEAD falhar, se o tamanho mudou ou se venceu o prazo."""
    last_length, last_at = _last_download["length"], _last_download["at"]
    if last_length is None or last_at is None:
        return True
    if now_naive - last_at >= timedelta(minutes=get_full_refresh_minutes()):
        return True
    length = fetch_content_length()
    return length is None or length != last_length


def fetch_active_warnings() -> tuple[list[dict] | None, int | None]:
    """(avisos de hoje e futuros numa lista, tamanho em bytes); (None, None) em erro ou formato inesperado."""
    try:
        res = http_session.get(WARNINGS_URL, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        logger.warning("Avisos INMET: falha de conexão (%s)", type(exc).__name__)
        return None, None
    if res.status_code != 200:
        logger.warning("Avisos INMET: HTTP %s", res.status_code)
        return None, None
    try:
        data = res.json()
    except ValueError:
        logger.warning("Avisos INMET: resposta não é JSON")
        return None, None
    # Sem as duas listas não dá para saber o que saiu do ar: trata como falha.
    if not isinstance(data, dict) or not isinstance(data.get("hoje"), list) or not isinstance(data.get("futuro"), list):
        logger.warning("Avisos INMET: formato inesperado")
        return None, None
    return data["hoje"] + data["futuro"], len(res.content)


def _parse_brt(value) -> datetime | None:
    """'2026-11-20 09:06' (Brasília) -> datetime UTC sem fuso (padrão do banco)."""
    try:
        local = datetime.strptime(str(value or "").strip()[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return local.replace(tzinfo=BRT).astimezone(timezone.utc).replace(tzinfo=None)


def _join_text(value) -> str | None:
    if isinstance(value, list):
        parts = [str(v).strip() for v in value if str(v).strip()]
        return "\n".join(parts) or None
    text = str(value or "").strip()
    return text or None


def _to_int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_warning(item: dict) -> dict | None:
    """Aviso que atinge algum município monitorado; None se não atinge, se está encerrado ou se falta dado."""
    if not isinstance(item, dict) or str(item.get("encerrado", "")).strip().lower() == "true":
        return None
    geocodes = {g.strip() for g in str(item.get("geocodes") or "").split(",")} & MONITORED_GEOCODES
    if not geocodes:
        return None
    inmet_id = str(item.get("id") or "").strip()
    starts_at, ends_at = _parse_brt(item.get("inicio")), _parse_brt(item.get("fim"))
    event = str(item.get("descricao") or "").strip()
    severity = str(item.get("severidade") or "").strip()
    # Vai para o link do aviso: só aceita código alfanumérico.
    if not inmet_id.isalnum() or not starts_at or not ends_at or not event or not severity:
        return None
    return {
        "inmet_id": inmet_id[:20],
        "warning_number": str(item.get("id_aviso") or inmet_id).strip()[:20],
        "sequence": _to_int(item.get("id_sequencia")),
        "event": event[:100],
        "severity": severity[:50],
        "severity_level": _to_int(item.get("id_severidade")),
        "color": (str(item.get("aviso_cor") or "").strip()[:10] or None),
        "starts_at": starts_at,
        "ends_at": ends_at,
        # Ordem do catálogo, para o e-mail e a API listarem os municípios sempre igual.
        "geocodes": ",".join(code for code in MUNICIPALITY_GEOCODES.values() if code in geocodes),
        "risks": _join_text(item.get("riscos")),
        "instructions": _join_text(item.get("instrucoes")),
    }


def warning_municipalities(warning: OfficialWarning) -> list[str]:
    return [GEOCODE_MUNICIPALITIES[c] for c in warning.geocodes.split(",") if c in GEOCODE_MUNICIPALITIES]


def _brt_str(dt: datetime) -> str:
    return dt.replace(tzinfo=timezone.utc).astimezone(BRT).strftime("%d/%m/%Y %H:%M")


def _notify(warning: OfficialWarning) -> str:
    municipalities = ", ".join(warning_municipalities(warning))
    version = f" (atualização, versão {warning.sequence})" if (warning.sequence or 1) > 1 else ""
    subject = f"[CLIMAE] Aviso oficial INMET: {warning.event} — {warning.severity}{version}"
    body = "\n".join(filter(None, [
        f"O INMET publicou aviso de {warning.event} (grau: {warning.severity}){version}.",
        f"Vigência: {_brt_str(warning.starts_at)} a {_brt_str(warning.ends_at)} (horário de Brasília).",
        f"Municípios monitorados atingidos: {municipalities}.",
        f"\nRiscos:\n{warning.risks}" if warning.risks else None,
        f"\nInstruções:\n{warning.instructions}" if warning.instructions else None,
        f"\nAviso completo: {warning.link}",
        "Fonte: INMET (aviso oficial). Este e-mail é um repasse automático do CLIMAE.",
    ]))
    return send_email_notification(subject, body)


def _notify_pending(now: datetime) -> int:
    """1 e-mail por versão ainda não notificada; falha de SMTP tenta de novo no próximo ciclo."""
    notified = 0
    pending = OfficialWarning.query.filter(
        OfficialWarning.withdrawn_at.is_(None),
        OfficialWarning.notified_at.is_(None),
        OfficialWarning.ends_at > now,
    ).order_by(OfficialWarning.starts_at.asc()).all()
    for row in pending:
        status = _notify(row)
        row.notification_status = status
        if status != "erro_envio":
            row.notified_at = now
            notified += 1
    if pending:
        db.session.commit()
    return notified


def sync_official_warnings(now: datetime | None = None) -> dict:
    """
    Atualiza a tabela com os avisos ativos que atingem os municípios monitorados, marca como
    retirados os que saíram da lista antes do fim e envia 1 e-mail por versão nova.
    Só baixa a lista quando needs_full_download() manda; em falha de rede/formato não mexe em nada.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(tzinfo=None)
    result = {"status": "ok", "downloaded": False, "active": 0, "new": 0, "withdrawn": 0, "notified": 0}

    if not needs_full_download(now):
        result["status"] = "sem_mudanca"
        result["notified"] = _notify_pending(now)
        logger.debug("Avisos INMET: tamanho igual ao último download; nada a baixar.")
        return result

    items, length = fetch_active_warnings()
    if items is None:
        result["status"] = "falha"
        return result
    result["downloaded"] = True

    parsed = {}
    for item in items:
        warning = parse_warning(item)
        if warning:
            parsed[warning["inmet_id"]] = warning

    existing = {w.inmet_id: w for w in OfficialWarning.query.filter(OfficialWarning.inmet_id.in_(parsed)).all()} if parsed else {}
    for inmet_id, fields in parsed.items():
        row = existing.get(inmet_id)
        if row is None:
            row = OfficialWarning(inmet_id=inmet_id, first_seen_at=now)
            db.session.add(row)
            result["new"] += 1
        for field, value in fields.items():
            setattr(row, field, value)
        row.last_seen_at = now
        row.withdrawn_at = None

    stale_query = OfficialWarning.query.filter(OfficialWarning.withdrawn_at.is_(None), OfficialWarning.ends_at > now)
    if parsed:
        stale_query = stale_query.filter(~OfficialWarning.inmet_id.in_(parsed))
    stale = stale_query.all()
    for row in stale:
        row.withdrawn_at = now
    db.session.commit()
    # Só depois de gravar: se algo falhar antes, o próximo ciclo baixa de novo.
    _last_download.update(length=length, at=now)

    result.update(active=len(parsed), withdrawn=len(stale), notified=_notify_pending(now))
    logger.info("Avisos INMET: %d ativos na Zona Sul, %d novos, %d retirados, %d notificados.",
                result["active"], result["new"], result["withdrawn"], result["notified"])
    return result


def _active_query(now: datetime):
    now_naive = now.astimezone(timezone.utc).replace(tzinfo=None)
    return (
        OfficialWarning.query
        .filter(OfficialWarning.withdrawn_at.is_(None), OfficialWarning.ends_at > now_naive)
        .order_by(OfficialWarning.starts_at.asc(), OfficialWarning.id.asc())
    ), now_naive


def warning_to_dict(warning: OfficialWarning, now_naive: datetime) -> dict:
    def iso(dt):
        return dt.replace(tzinfo=timezone.utc).isoformat()
    return {
        "id": warning.inmet_id,
        "event": warning.event,
        "severity": warning.severity,
        "severity_level": warning.severity_level,
        "color": warning.color,
        "starts_at": iso(warning.starts_at),
        "ends_at": iso(warning.ends_at),
        "in_effect": warning.starts_at <= now_naive,
        "municipalities": warning_municipalities(warning),
        "risks": warning.risks,
        "instructions": warning.instructions,
        "link": warning.link,
        "source": "INMET (aviso oficial)",
    }


def get_active_warnings(now: datetime | None = None) -> list[dict]:
    """Avisos vigentes ou futuros que atingem algum município monitorado."""
    query, now_naive = _active_query(now or datetime.now(timezone.utc))
    return [warning_to_dict(w, now_naive) for w in query.all()]


def get_region_warnings(region: Region, now: datetime | None = None) -> list[dict]:
    """Avisos vigentes ou futuros para o município da região (pelo geocode IBGE)."""
    geocode = MUNICIPALITY_GEOCODES.get(region.municipality)
    if not geocode:
        return []
    query, now_naive = _active_query(now or datetime.now(timezone.utc))
    return [warning_to_dict(w, now_naive) for w in query.all() if geocode in w.geocodes.split(",")]
