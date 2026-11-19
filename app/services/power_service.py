"""
CLIMAE - Serviço de Monitoramento de Energia Elétrica (Crowdsourcing)
---------------------------------------------------------------------
Gerencia o registro de relatos colaborativos de falta de energia, proteção anti-spam
via hash anônimo de visitante (cooldown de 30 min) e cálculo do status de abastecimento
elétrico por localidade.
"""

import hashlib
import hmac
import math
import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from flask import Request, current_app
from app.models import (
    db,
    Region,
    PowerOutageReport,
    PowerRestorationReport,
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
    Gera um hash anônimo (HMAC-SHA-256) baseado no IP do cliente e no User-Agent.
    Não armazena o IP em texto claro, garantindo privacidade e conformidade com a LGPD.

    O IP vem de request.remote_addr (a conexão real). O header X-Forwarded-For não é
    lido aqui, pois qualquer cliente pode forjá-lo para burlar o cooldown. Atrás de
    um proxy reverso confiável, configure TRUSTED_PROXY_COUNT (ProxyFix em create_app).
    """
    client_ip = request.remote_addr or "127.0.0.1"
    user_agent = request.headers.get("User-Agent", "Unknown-Agent")
    secret = str(current_app.config.get("SECRET_KEY") or "climae-salt-power").encode("utf-8")
    raw_str = f"power:{client_ip}:{user_agent}".encode("utf-8")
    return hmac.new(secret, raw_str, hashlib.sha256).hexdigest()


# Teto de relatos de falta de luz por IP e região. O hash do visitante inclui o
# User-Agent para que moradores atrás do mesmo IP (CGNAT) relatem cada um o seu, mas
# trocar o User-Agent é trivial. O teto fica abaixo dos 3 relatos que exibem
# "Falta de Luz Reportada", então um único IP não consegue disparar o aviso sozinho.
IP_REPORT_LIMIT = 2
IP_REPORT_WINDOW_MINUTES = 30

# "A Luz Voltou" exige consenso: os relatos de queda anteriores só deixam de contar com
# RESTORE_CONSENSUS confirmações na janela de RESTORE_WINDOW_MINUTES. Cada IP confirma no
# máximo uma vez por região nessa janela, então o consenso vem de IPs diferentes.
RESTORE_CONSENSUS = 2
RESTORE_WINDOW_MINUTES = 45

# Limite geral de POSTs em /api/power/* por IP (cobre qualquer abuso que os
# cooldowns e tetos por região não peguem).
POWER_POST_LIMIT = 30
POWER_POST_WINDOW_MINUTES = 10


def generate_ip_key(request: Request) -> str:
    """HMAC só do IP da conexão (sem User-Agent), para o teto por IP. Nunca é gravado no banco."""
    client_ip = request.remote_addr or "127.0.0.1"
    secret = str(current_app.config.get("SECRET_KEY") or "climae-salt-power").encode("utf-8")
    return hmac.new(secret, f"power-ip:{client_ip}".encode("utf-8"), hashlib.sha256).hexdigest()


class IpReportLimiter:
    """
    Janela deslizante em memória: no máximo `limit` ações por (IP, escopo) em `window`
    (o escopo é a região, ou um nome para limites gerais).
    Fica em memória de propósito (sem mudança no banco); reiniciar o servidor zera a
    contagem. Depende de um único processo servindo o app, como garante o run.py.
    """

    MAX_KEYS = 10_000

    def __init__(self, limit: int = IP_REPORT_LIMIT, window_minutes: int = IP_REPORT_WINDOW_MINUTES):
        self.limit = limit
        self.window = timedelta(minutes=window_minutes)
        self._hits: dict[tuple[str, object], deque] = {}
        self._lock = threading.Lock()

    def try_acquire(self, ip_key: str, scope, now: datetime = None) -> tuple[bool, int]:
        """Reserva uma vaga se houver. Retorna (permitido, minutos até liberar uma vaga)."""
        now = now or datetime.now(timezone.utc)
        threshold = now - self.window
        with self._lock:
            if len(self._hits) > self.MAX_KEYS:
                self._purge(threshold)
            hits = self._hits.setdefault((ip_key, scope), deque())
            while hits and hits[0] <= threshold:
                hits.popleft()
            if len(hits) >= self.limit:
                remaining = (hits[0] + self.window - now).total_seconds() / 60
                return False, max(1, int(math.ceil(remaining)))
            hits.append(now)
            return True, 0

    def _purge(self, threshold: datetime):
        for key in [k for k, hits in self._hits.items() if not hits or hits[-1] <= threshold]:
            del self._hits[key]


def get_ip_report_limiter() -> IpReportLimiter:
    """Um limitador por instância do app (os testes criam um app novo a cada caso)."""
    return current_app.extensions.setdefault("power_ip_report_limiter", IpReportLimiter())


def get_ip_restore_limiter() -> IpReportLimiter:
    """Uma confirmação de "A Luz Voltou" por IP e região na janela do consenso."""
    return current_app.extensions.setdefault(
        "power_ip_restore_limiter", IpReportLimiter(limit=1, window_minutes=RESTORE_WINDOW_MINUTES)
    )


def get_power_post_limiter() -> IpReportLimiter:
    """Limite geral de POSTs em /api/power/* por IP."""
    return current_app.extensions.setdefault(
        "power_post_limiter", IpReportLimiter(limit=POWER_POST_LIMIT, window_minutes=POWER_POST_WINDOW_MINUTES)
    )


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


def record_power_outage_report(region_id: int, reporter_hash: str, comment: str = None, ip_key: str = None) -> dict:
    """
    Registra um relato de falta de energia com validação de cooldown.
    Com `ip_key`, aplica também o teto de relatos por IP e região (IpReportLimiter).
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

    if ip_key is not None:
        allowed, remaining = get_ip_report_limiter().try_acquire(ip_key, region_id)
        if not allowed:
            return {
                "success": False,
                "error": (
                    "Já recebemos relatos suficientes da sua rede para esta localidade. "
                    f"Aguarde mais {remaining} minuto(s)."
                ),
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


def record_power_restoration(region_id: int, reporter_hash: str, comment: str = None, ip_key: str = None) -> dict:
    """
    Registra uma confirmação de restabelecimento da energia elétrica ("A Luz Voltou").
    É um voto: os relatos de queda não são alterados no banco, e só deixam de contar no
    status quando houver RESTORE_CONSENSUS confirmações (ver get_power_status).
    Com `ip_key`, cada IP confirma no máximo uma vez por região na janela do consenso.
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

    if ip_key is not None:
        allowed, remaining = get_ip_restore_limiter().try_acquire(ip_key, region_id)
        if not allowed:
            return {
                "success": False,
                "error": (
                    "Sua rede já confirmou o retorno da energia para esta localidade. "
                    f"Aguarde mais {remaining} minuto(s)."
                ),
                "cooldown_remaining": remaining,
            }

    clean_comment = comment.strip()[:140] if comment else None
    now_utc = datetime.now(timezone.utc)

    restoration = PowerRestorationReport(
        region_id=region_id,
        reporter_hash=reporter_hash,
        comment=clean_comment,
        restored_at=now_utc,
    )
    db.session.add(restoration)
    db.session.commit()

    status_data = get_power_status(region_id, reporter_hash)
    status_data["success"] = True
    if status_data.get("restoration_confirmed"):
        status_data["message"] = f"Restabelecimento de energia confirmado para {region.name}! Obrigado por colaborar."
    else:
        status_data["message"] = (
            f"Confirmação registrada para {region.name}. A energia aparece como restabelecida "
            "quando outro morador também confirmar."
        )
    return status_data


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
    das últimas horas, confirmações de restabelecimento e eventuais comunicados oficiais.

    Não usa mais as "sondas de conectividade": elas testavam DNS públicos a partir do
    servidor (iguais para todas as regiões), ou seja, mediam a internet do servidor e não
    a energia do bairro.
    """
    region = db.session.get(Region, region_id)
    if not region:
        return {"status": "unknown", "label": "Desconhecido", "reports_30m": 0, "reports_60m": 0}

    now_utc = datetime.now(timezone.utc)
    cutoff_30m = now_utc - timedelta(minutes=30)
    cutoff_60m = now_utc - timedelta(minutes=60)
    cutoff_restoration = now_utc - timedelta(minutes=RESTORE_WINDOW_MINUTES)

    # Consenso do "A Luz Voltou": com RESTORE_CONSENSUS confirmações recentes, o retorno vale
    # a partir da RESTORE_CONSENSUS-ésima mais recente. Usar a mais recente deixaria uma
    # confirmação isolada, chegando depois do consenso, esconder relatos novos.
    recent_restorations = (
        PowerRestorationReport.query
        .filter(
            PowerRestorationReport.region_id == region_id,
            PowerRestorationReport.restored_at >= cutoff_restoration,
        )
        .order_by(PowerRestorationReport.restored_at.desc())
        .all()
    )
    latest_restoration = (
        recent_restorations[RESTORE_CONSENSUS - 1] if len(recent_restorations) >= RESTORE_CONSENSUS else None
    )

    # Contagem de relatos ativos: com restabelecimento confirmado, só contam relatos posteriores a ele
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

    # Matriz de confiança (só fontes reais: concessionária e moradores):
    # 1. Oficial: boletim crítico recente citando o município
    if official_bulletin and (official_bulletin.affected_customers or 0) > 500:
        status_key = "official_outage"
        status_label = "Interrupção de Energia Confirmada pela Concessionária"
        severity = "danger"

    # 2. Confirmação popular: 3+ relatos recentes
    elif reports_30m >= 3:
        status_key = "outage_reported"
        status_label = f"Falta de Luz Reportada por Moradores ({reports_30m} relatos recentes)"
        severity = "danger"

    # 3. Investigando / Suspeita: 1 ou 2 relatos ativos isolados
    elif reports_30m >= 1 or reports_60m >= 2:
        status_key = "investigating"
        status_label = f"Relato Isolado de Falta de Energia ({reports_60m} na última hora)"
        severity = "warning"

    # 4. Restabelecida: moradores confirmaram retorno da luz recentemente (e sem novas quedas)
    elif latest_restoration:
        status_key = "restored"
        status_label = f"Energia Restabelecida (confirmado por moradores às {restored_time_str})"
        severity = "success"

    # 5. Estável
    else:
        status_key = "stable"
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
        "restoration_confirmed": latest_restoration is not None,
        "restore_confirmations": len(recent_restorations),
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
