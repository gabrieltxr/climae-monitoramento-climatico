"""
CLIMAE - Serviço de Monitoramento de Energia Elétrica (Crowdsourcing)
---------------------------------------------------------------------
Gerencia o registro de relatos colaborativos de falta de energia, proteção anti-spam
via hash anônimo de visitante (cooldown de 30 min) e cálculo do status de abastecimento
elétrico por localidade.
"""

import hashlib
import math
import socket
import time
from datetime import datetime, timedelta, timezone
from flask import Request
from app.models import (
    db,
    Region,
    PowerOutageReport,
    PowerRestorationReport,
    PowerGridProbe,
    PowerUtilityBulletin,
)


def make_aware_utc(dt: datetime) -> datetime:
    """Garante que o datetime esteja ciente de fuso horário em UTC."""
    if dt is None:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def generate_reporter_hash(request: Request) -> str:
    """
    Gera um hash anônimo (SHA-256) baseado no IP do cliente e no User-Agent.
    Não armazena o IP em texto claro, garantindo privacidade e conformidade com a LGPD.
    """
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()
    else:
        client_ip = request.remote_addr or "127.0.0.1"

    user_agent = request.headers.get("User-Agent", "Unknown-Agent")
    raw_str = f"climae-salt-power:{client_ip}:{user_agent}"
    return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()


def check_report_cooldown(region_id: int, reporter_hash: str, cooldown_minutes: int = 30) -> tuple[bool, int]:
    """
    Verifica se o visitante já enviou um relato para esta região dentro da janela de cooldown.
    Retorna (can_report: bool, remaining_minutes: int).
    """
    now_utc = datetime.now(timezone.utc)
    threshold = now_utc - timedelta(minutes=cooldown_minutes)

    last_report = (
        PowerOutageReport.query
        .filter(
            PowerOutageReport.region_id == region_id,
            PowerOutageReport.reporter_hash == reporter_hash,
            PowerOutageReport.reported_at >= threshold,
        )
        .order_by(PowerOutageReport.reported_at.desc())
        .first()
    )

    if not last_report:
        return True, 0

    reported_dt = make_aware_utc(last_report.reported_at)
    elapsed = (now_utc - reported_dt).total_seconds() / 60.0
    remaining = max(1, int(math.ceil(cooldown_minutes - elapsed)))
    return False, remaining


def check_restoration_cooldown(region_id: int, reporter_hash: str, cooldown_minutes: int = 15) -> tuple[bool, int]:
    """
    Verifica se o visitante já enviou confirmação de retorno da luz nesta localidade recentemente.
    Retorna (can_restore: bool, remaining_minutes: int).
    """
    now_utc = datetime.now(timezone.utc)
    threshold = now_utc - timedelta(minutes=cooldown_minutes)

    last_restore = (
        PowerRestorationReport.query
        .filter(
            PowerRestorationReport.region_id == region_id,
            PowerRestorationReport.reporter_hash == reporter_hash,
            PowerRestorationReport.restored_at >= threshold,
        )
        .order_by(PowerRestorationReport.restored_at.desc())
        .first()
    )

    if not last_restore:
        return True, 0

    restored_dt = make_aware_utc(last_restore.restored_at)
    elapsed = (now_utc - restored_dt).total_seconds() / 60.0
    remaining = max(1, int(math.ceil(cooldown_minutes - elapsed)))
    return False, remaining


def record_power_outage_report(region_id: int, reporter_hash: str, comment: str = None) -> dict:
    """
    Registra um relato de falta de energia com validação de cooldown.
    """
    region = db.session.get(Region, region_id)
    if not region:
        return {"success": False, "error": "Região não encontrada."}

    can_report, remaining = check_report_cooldown(region_id, reporter_hash, cooldown_minutes=30)
    if not can_report:
        return {
            "success": False,
            "error": f"Você já registrou um relato recente para esta localidade. Aguarde mais {remaining} minuto(s).",
            "cooldown_remaining": remaining,
        }

    clean_comment = comment.strip()[:140] if comment else None
    now_utc = datetime.now(timezone.utc)

    report = PowerOutageReport(
        region_id=region_id,
        reporter_hash=reporter_hash,
        comment=clean_comment,
        reported_at=now_utc,
        is_active=True,
    )
    db.session.add(report)
    db.session.commit()

    # Retorna o status atualizado da região após o relato
    status_data = get_power_status(region_id, reporter_hash)
    status_data["success"] = True
    status_data["message"] = f"Relato registrado com sucesso para {region.name}! Obrigado por colaborar."
    return status_data


def record_power_restoration(region_id: int, reporter_hash: str, comment: str = None) -> dict:
    """
    Registra confirmação de restabelecimento da energia elétrica ("A Luz Voltou"),
    desativando os relatos de queda pendentes para a região.
    """
    region = db.session.get(Region, region_id)
    if not region:
        return {"success": False, "error": "Região não encontrada."}

    can_restore, remaining = check_restoration_cooldown(region_id, reporter_hash, cooldown_minutes=15)
    if not can_restore:
        return {
            "success": False,
            "error": f"Você já confirmou o retorno da energia recentemente. Aguarde mais {remaining} minuto(s).",
            "cooldown_remaining": remaining,
        }

    clean_comment = comment.strip()[:140] if comment else None
    now_utc = datetime.now(timezone.utc)

    # 1. Cria o registro de restabelecimento
    restoration = PowerRestorationReport(
        region_id=region_id,
        reporter_hash=reporter_hash,
        comment=clean_comment,
        restored_at=now_utc,
    )
    db.session.add(restoration)

    # 2. Desativa relatos anteriores de falta de luz para esta região
    PowerOutageReport.query.filter(
        PowerOutageReport.region_id == region_id,
        PowerOutageReport.is_active.is_(True),
    ).update({"is_active": False}, synchronize_session=False)

    db.session.commit()

    status_data = get_power_status(region_id, reporter_hash)
    status_data["success"] = True
    status_data["message"] = f"Restabelecimento de energia confirmado para {region.name}! Obrigado por colaborar."
    return status_data


def probe_host(host: str, port: int = 53, timeout: float = 1.0) -> tuple[bool, int | None]:
    """
    Testa a conectividade de um nó de rede via conexão TCP de baixa latência e não intrusiva.
    Retorna (is_up: bool, latency_ms: int | None).
    """
    start = time.perf_counter()
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        latency = int((time.perf_counter() - start) * 1000)
        return True, max(1, latency)
    except Exception:
        return False, None
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass


def evaluate_region_probes(region_id: int) -> dict:
    """
    Avalia o consenso multi-operadora das sondas da região:
    - Se todas as operadoras caírem simultaneamente: "blackout_suspected" (queda geral de infraestrutura).
    - Se apenas uma operadora falhar e outras continuarem ativas: "telecom_instability" (falso positivo filtrado!).
    - Se todas responderem normalmente: "healthy".
    - Se não houver sondas cadastradas: "unconfigured".
    """
    probes = PowerGridProbe.query.filter_by(region_id=region_id).all()
    if not probes:
        return {
            "status": "unconfigured",
            "total_probes": 0,
            "up_probes": 0,
            "providers_down": [],
            "providers_up": [],
            "probes": [],
        }

    total_probes = len(probes)
    up_probes = sum(1 for p in probes if p.is_up)

    # Agrupamento por operadora para detecção de falha seletiva de telecomunicações
    providers: dict[str, list[bool]] = {}
    for p in probes:
        providers.setdefault(p.provider_name, []).append(p.is_up)

    providers_down = []
    providers_up = []
    for prov_name, statuses in providers.items():
        if any(statuses):
            providers_up.append(prov_name)
        else:
            providers_down.append(prov_name)

    if not providers_up and providers_down:
        telemetry_status = "blackout_suspected"
    elif providers_down and providers_up:
        telemetry_status = "telecom_instability"
    else:
        telemetry_status = "healthy"

    return {
        "status": telemetry_status,
        "total_probes": total_probes,
        "up_probes": up_probes,
        "providers_down": providers_down,
        "providers_up": providers_up,
        "probes": [p.to_dict() for p in probes],
    }


def seed_default_probes_if_empty():
    """
    Popula sondas de conectividade padrão para as regiões caso a tabela esteja vazia.
    Configura nós de alta confiabilidade em 3 provedores distintos (Claro, Vivo, RNP/Regional).
    """
    if PowerGridProbe.query.first():
        return

    regions = Region.query.all()
    if not regions:
        return

    default_probe_templates = [
        {"provider": "Claro", "suffix": "Gateway Claro", "host": "1.1.1.1", "port": 53},
        {"provider": "Vivo", "suffix": "Gateway Vivo", "host": "8.8.8.8", "port": 53},
        {"provider": "RNP / Regional", "suffix": "Backbone Acadêmico / Regional", "host": "9.9.9.9", "port": 53},
    ]

    probes_to_add = []
    now_utc = datetime.now(timezone.utc)
    for reg in regions:
        for tpl in default_probe_templates:
            probe = PowerGridProbe(
                region_id=reg.id,
                name=f"{reg.name} ({tpl['suffix']})",
                target_host=tpl["host"],
                target_port=tpl["port"],
                provider_name=tpl["provider"],
                is_up=True,
                latency_ms=15,
                last_checked_at=now_utc,
            )
            probes_to_add.append(probe)

    db.session.bulk_save_objects(probes_to_add)
    db.session.commit()


def run_probe_checks_for_region(region_id: int, min_interval_seconds: int = 120, force: bool = False) -> dict:
    """
    Executa a checagem das sondas associadas à região especificada, aplicando
    janela de proteção (TTL/Debounce de 120s) para evitar sobrecarga (overload)
    de conexões na rede e bloqueio por firewall/rate-limit dos nós externos.
    """
    probes = PowerGridProbe.query.filter_by(region_id=region_id).all()
    if not probes:
        return evaluate_region_probes(region_id)

    now_utc = datetime.now(timezone.utc)

    # Se as sondas foram verificadas recentemente (dentro da janela de 2 min),
    # reutiliza o estado recente sem disparar novos sockets na internet
    if not force:
        recently_checked = all(
            p.last_checked_at and (now_utc - make_aware_utc(p.last_checked_at)).total_seconds() < min_interval_seconds
            for p in probes
        )
        if recently_checked:
            return evaluate_region_probes(region_id)

    for p in probes:
        is_up, latency = probe_host(p.target_host, p.target_port, timeout=1.0)
        p.is_up = is_up
        p.latency_ms = latency
        p.last_checked_at = now_utc
    db.session.commit()
    return evaluate_region_probes(region_id)


def record_utility_bulletin(data: dict) -> dict:
    """
    Registra um boletim ou comunicado oficial da concessionária ou Defesa Civil.
    """
    municipality = (data.get("municipality") or "").strip()
    headline = (data.get("headline") or "").strip()
    body = (data.get("body") or "").strip()

    if not municipality or not headline or not body:
        return {"success": False, "error": "Município, título e conteúdo do boletim são obrigatórios."}

    pub_at = data.get("published_at")
    published_dt = make_aware_utc(pub_at) if isinstance(pub_at, datetime) else datetime.now(timezone.utc)

    bulletin = PowerUtilityBulletin(
        municipality=municipality,
        region_id=data.get("region_id"),
        source=data.get("source", "CEEE Grupo Equatorial"),
        headline=headline,
        body=body,
        affected_customers=data.get("affected_customers"),
        severity=data.get("severity", "alerta"),
        status=data.get("status", "ativo"),
        published_at=published_dt,
    )
    db.session.add(bulletin)
    db.session.commit()
    return {"success": True, "bulletin": bulletin.to_dict()}


def get_utility_bulletins(municipality: str = None, status: str = None, limit: int = 10) -> list[dict]:
    """
    Retorna os comunicados e boletins oficiais emitidos, com filtros opcionais.
    """
    query = PowerUtilityBulletin.query
    if municipality:
        query = query.filter(
            db.or_(
                PowerUtilityBulletin.municipality.ilike(f"%{municipality}%"),
                PowerUtilityBulletin.municipality.in_(["Zona Sul", "Geral", "Rio Grande do Sul"])
            )
        )
    if status:
        query = query.filter_by(status=status)

    bulletins = query.order_by(PowerUtilityBulletin.published_at.desc()).limit(limit).all()
    return [b.to_dict() for b in bulletins]


def seed_sample_bulletins_if_empty():
    """
    Popula boletins oficiais exemplares da concessionária e Defesa Civil para a Zona Sul,
    caso a tabela esteja vazia.
    """
    if PowerUtilityBulletin.query.first():
        return

    now_utc = datetime.now(timezone.utc)
    sample_bulletins = [
        PowerUtilityBulletin(
            municipality="Rio Grande",
            source="CEEE Grupo Equatorial",
            headline="Plano de Contingência ativado na Zona Sul devido a rajadas de vento",
            body="O Grupo CEEE Equatorial mobilizou equipes extras para atendimento preventivo e emergencial em Rio Grande e Pelotas em decorrência de ventos fortes registrados na costa. Clientes que avistarem cabos rompidos devem manter distância e acionar a concessionária pelo 0800 721 2333.",
            affected_customers=1200,
            severity="alerta",
            status="ativo",
            published_at=now_utc - timedelta(hours=2),
        ),
        PowerUtilityBulletin(
            municipality="Pelotas",
            source="Defesa Civil RS",
            headline="Alerta Integrado de Monitoramento de Redes e Queda de Árvores",
            body="A Defesa Civil do RS mantém monitoramento constante junto às concessionárias na região de Pelotas e entorno. Equipes operacionais sobreavisadas para desobstrução de vias e apoio ao restabelecimento elétrico em caso de vendaval.",
            affected_customers=None,
            severity="informativo",
            status="ativo",
            published_at=now_utc - timedelta(hours=4),
        ),
        PowerUtilityBulletin(
            municipality="Zona Sul",
            source="CEEE Grupo Equatorial",
            headline="Orientações de Segurança em Temporais e Alagamentos",
            body="Em caso de inundações parciais em residências na Zona Sul, desligue o disjuntor geral imediatamente. Nunca toque em fios caídos em vias públicas. Informe emergências pelo canal WhatsApp (51) 3382-5500 ou aplicativo oficial.",
            affected_customers=None,
            severity="informativo",
            status="ativo",
            published_at=now_utc - timedelta(hours=8),
        ),
    ]
    db.session.add_all(sample_bulletins)
    db.session.commit()


def get_power_status(region_id: int, reporter_hash: str = None) -> dict:
    """
    Calcula o status atual do abastecimento elétrico da região com base na janela de relatos
    das últimas horas, confirmações de restabelecimento, telemetria de sondas de rede
    e eventuais comunicados oficiais.
    """
    region = db.session.get(Region, region_id)
    if not region:
        return {"status": "unknown", "label": "Desconhecido", "reports_30m": 0, "reports_60m": 0}

    now_utc = datetime.now(timezone.utc)
    cutoff_30m = now_utc - timedelta(minutes=30)
    cutoff_60m = now_utc - timedelta(minutes=60)
    cutoff_restoration = now_utc - timedelta(minutes=45)

    # Avaliação de telemetria multi-operadora de rede
    telemetry = evaluate_region_probes(region.id)
    telemetry_status = telemetry.get("status")

    # Busca última restauração recente nos últimos 45 minutos
    latest_restoration = (
        PowerRestorationReport.query
        .filter(
            PowerRestorationReport.region_id == region_id,
            PowerRestorationReport.restored_at >= cutoff_restoration,
        )
        .order_by(PowerRestorationReport.restored_at.desc())
        .first()
    )

    # Contagem de relatos ativos: se houver restauração recente, considera apenas novos relatos posteriores a ela
    active_filters = [
        PowerOutageReport.region_id == region_id,
        PowerOutageReport.is_active.is_(True),
    ]
    if latest_restoration:
        active_filters.append(PowerOutageReport.reported_at > latest_restoration.restored_at)

    reports_30m = PowerOutageReport.query.filter(
        *active_filters,
        PowerOutageReport.reported_at >= cutoff_30m,
    ).count()

    reports_60m = PowerOutageReport.query.filter(
        *active_filters,
        PowerOutageReport.reported_at >= cutoff_60m,
    ).count()

    # Busca último comunicado oficial ativo para o município ou regional (Zona Sul/Geral) nas últimas 48h
    cutoff_bulletin = now_utc - timedelta(hours=48)
    official_bulletin = (
        PowerUtilityBulletin.query
        .filter(
            db.or_(
                PowerUtilityBulletin.municipality == region.municipality,
                PowerUtilityBulletin.municipality.in_(["Zona Sul", "Geral", "Rio Grande do Sul"])
            ),
            PowerUtilityBulletin.published_at >= cutoff_bulletin,
            PowerUtilityBulletin.status == "ativo",
        )
        .order_by(PowerUtilityBulletin.published_at.desc())
        .first()
    )

    # Verifica se o visitante atual já votou (queda ou restabelecimento)
    user_can_report = True
    cooldown_remaining = 0
    user_can_restore = True
    restore_cooldown_remaining = 0

    if reporter_hash:
        user_can_report, cooldown_remaining = check_report_cooldown(region_id, reporter_hash, cooldown_minutes=30)
        user_can_restore, restore_cooldown_remaining = check_restoration_cooldown(region_id, reporter_hash, cooldown_minutes=15)

    restored_at_iso = None
    restored_time_str = None
    if latest_restoration:
        rest_aware = make_aware_utc(latest_restoration.restored_at)
        restored_at_iso = rest_aware.isoformat()
        # Horário local aproximado de Brasília (UTC-3)
        br_time = rest_aware - timedelta(hours=3)
        restored_time_str = br_time.strftime("%H:%M")

    # Lógica de Matriz de Confiança e Consenso Triplo:
    # 1. Oficial: boletim crítico recente citando o município
    if official_bulletin and (official_bulletin.affected_customers or 0) > 500:
        status_key = "official_outage"
        status_label = "Interrupção de Energia Confirmada pela Concessionária"
        severity = "danger"

    # 2. Confirmação Popular ou Correlação Forte (Sondas Offline + Moradores)
    elif reports_30m >= 3:
        status_key = "outage_reported"
        status_label = f"Falta de Luz Reportada por Moradores ({reports_30m} relatos recentes)"
        severity = "danger"
    elif reports_30m >= 1 and telemetry_status == "blackout_suspected":
        status_key = "outage_reported"
        status_label = f"Falta de Luz Confirmada (Sondas Offline + {reports_30m} relato de morador)"
        severity = "danger"

    # 3. Suspeita Técnica (Sondas de todas as operadoras caíram, aguardando relato de moradores)
    elif telemetry_status == "blackout_suspected" and reports_30m == 0:
        status_key = "blackout_suspected"
        status_label = "Suspeita Técnica de Queda (Todas as operadoras de internet sem sinal)"
        severity = "warning"

    # 4. Investigando / Suspeita: 1 ou 2 relatos ativos isolados
    elif reports_30m >= 1 or reports_60m >= 2:
        status_key = "investigating"
        status_label = f"Relato Isolado de Falta de Energia ({reports_60m} na última hora)"
        severity = "warning"

    # 5. Restabelecida: Moradores confirmaram retorno da luz recentemente (e sem novas quedas)
    elif latest_restoration:
        status_key = "restored"
        status_label = f"Energia Restabelecida (confirmado por moradores às {restored_time_str})"
        severity = "success"

    # 6. Estável / Normal (com ou sem alerta de instabilidade de telecomunicações)
    else:
        status_key = "stable"
        if telemetry_status == "telecom_instability":
            down_provs = ", ".join(telemetry.get("providers_down", []))
            status_label = f"Rede Elétrica Normal (Instabilidade de dados na operadora {down_provs})"
        else:
            status_label = "Rede Elétrica Estável (Sem relatos de interrupção)"
        severity = "success"

    return {
        "region_id": region.id,
        "region_name": region.name,
        "municipality": region.municipality,
        "status": status_key,
        "label": status_label,
        "severity": severity,
        "reports_30m": reports_30m,
        "reports_60m": reports_60m,
        "user_can_report": user_can_report,
        "cooldown_remaining": cooldown_remaining,
        "user_can_restore": user_can_restore,
        "restore_cooldown_remaining": restore_cooldown_remaining,
        "restored_at": restored_at_iso,
        "restored_time": restored_time_str,
        "telemetry": telemetry,
        "official_bulletin": {
            "id": official_bulletin.id,
            "headline": official_bulletin.headline,
            "body": official_bulletin.body,
            "source": official_bulletin.source,
            "severity": official_bulletin.severity or "alerta",
            "status": official_bulletin.status or "ativo",
            "affected_customers": official_bulletin.affected_customers,
            "published_at": make_aware_utc(official_bulletin.published_at).isoformat(),
        } if official_bulletin else None,
        "updated_at": now_utc.isoformat(),
    }
