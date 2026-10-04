"""Reglas de negocio: de una etiqueta (+ estado del lead) a la lista de órdenes que proceden.

Es código puro y determinista: no lee ni escribe estado, no llama al modelo. Recibe la foto del
lead (`ContextoLead`) y devuelve un `Plan` con las acciones y los efectos de estado que tendrán
que persistirse. Así cada regla se puede razonar (y probar) aislada.

Reglas aplicadas aquí (numeración del enunciado):
  N1 sin WhatsApp a quien lo rechazó · N2 baja → nada saliente · N3 intentos agotados → respaldo
  N4 `otro` o 2ª cortada → revisar_llamada · N5 visita no reservada → se vuelve a llamar
  R3 fechas en Europe/Madrid dentro de la ventana · R4 recuento de intentos · R7 cancelaciones
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import tiempo
from .config import Config
from .modelos import (
    ETIQUETAS_CORTADA,
    ETIQUETAS_REINTENTO_VOZ,
    OPERACIONES_BLOQUEADAS_POR_BAJA,
    STATUS_POR_ETIQUETA,
    Clasificacion,
    Evento,
)
from .persistencia import ContextoLead, Efectos


@dataclass
class Accion:
    """Una orden al CRM antes de tener identificadores.

    `distintivo` solo se añade a la idempotency_key si el evento emite más de una orden de la
    misma operación (recomendación del OpenAPI). `recordatorio` lleva los datos que hay que
    persistir para poder cancelarlo otro día.
    """

    operacion: str
    cuerpo: dict
    distintivo: str | None = None
    recordatorio: dict | None = None


@dataclass
class Plan:
    acciones: list[Accion] = field(default_factory=list)
    efectos: Efectos = field(default_factory=Efectos)
    notas: list[str] = field(default_factory=list)  # qué reglas se aplicaron, para la traza


# --------------------------------------------------------------------------- llamada

_MOTIVO_REINTENTO = {
    "sin_respuesta": "sin respuesta, nuevo intento respetando la separación mínima",
    "buzon": "saltó el buzón de voz, nuevo intento respetando la separación mínima",
    "ocupado": "línea comunicando, reintento corto",
    "cortada": "llamada cortada a mitad, recuperar la conversación",
    "visita_sin_confirmar": "visita acordada de palabra sin reservar: volver a llamar para cerrarla",
    "callback": "callback solicitado",
}


def planificar_llamada(evento: Evento, c: Clasificacion, lead: ContextoLead, cfg: Config) -> Plan:
    plan = Plan()
    ahora = tiempo.a_zona(evento.occurred_at, cfg)
    etiqueta = c.etiqueta
    intento = lead.intentos_previos + 1
    agotados = intento >= cfg.max_intentos

    plan.efectos.intento = (evento.telephony.call_id, etiqueta)
    plan.notas.append(f"R4: intento {intento} de {cfg.max_intentos} para {evento.lead.contact_id}")

    # Cerrar la llamada: siempre, en todo call.ended procesado por primera vez.
    plan.acciones.append(Accion("cerrar_llamada", {
        "entry_id": evento.campaign.entry_id,
        "status": STATUS_POR_ETIQUETA[etiqueta],
        "etiqueta": etiqueta,
        "motivo": c.motivo,
        "confianza": round(c.confianza, 2),  # el mismo valor que la línea de decisiones.jsonl
        "duration_seconds": evento.telephony.duration_seconds,
    }))

    # N2: una baja manda sobre todo. Se registra y no sale nada más.
    if etiqueta == "no_contactar":
        plan.acciones.append(Accion("marcar_no_contactar", {
            "telefono": evento.lead.phone,
            "contact_id": evento.lead.contact_id,
            "canal": "todos",
            "motivo": c.motivo,
            "origen": f"llamada {evento.telephony.call_id}",
        }))
        plan.efectos.baja = {"telefono": evento.lead.phone, "canal": "todos", "motivo": c.motivo}
        plan.notas.append("N2: baja en canal todos, ninguna otra orden")
        return plan

    if c.whatsapp_rechazado:
        plan.efectos.canales_rechazados.append("whatsapp")
    whatsapp_bloqueado = lead.whatsapp_rechazado or c.whatsapp_rechazado

    # Acción propia de la etiqueta.
    if etiqueta == "visita_reservada":
        plan.acciones.append(_tarea_confirmar_visita(evento, c, cfg))
    elif etiqueta == "documentacion_enviada":
        plan.acciones.extend(_recordatorios_documentacion(evento, cfg, whatsapp_bloqueado, plan.notas))
    elif etiqueta == "documentacion_pendiente":
        destino = f" a {c.email}" if c.email else " (pedir email al lead, no lo dio)"
        plan.acciones.append(_tarea(
            evento, cfg, "enviar_documentacion_email",
            f"Enviar documentación por email a {_nombre(evento)}",
            f"Pidió la documentación del inmueble por email{destino}. Rechaza WhatsApp: no usar ese canal.",
        ))
    elif etiqueta == "persona_equivocada":
        plan.acciones.append(_tarea(
            evento, cfg, "verificar_telefono",
            f"Verificar el teléfono de {_nombre(evento)}",
            f"{c.motivo}. No se reintenta por voz hasta verificar el número.",
        ))
    elif etiqueta == "rechazada":
        plan.notas.append("rechazo activo: sin reintento por voz, canal de respaldo")
        plan.acciones.extend(_respaldo(evento, cfg, whatsapp_bloqueado, plan.notas))
    elif etiqueta in ETIQUETAS_REINTENTO_VOZ:
        if agotados:
            plan.notas.append(f"N3: {intento} intentos de {cfg.max_intentos} consumidos, canal de respaldo")
            plan.acciones.extend(_respaldo(evento, cfg, whatsapp_bloqueado, plan.notas))
        else:
            plan.acciones.extend(_reintento_voz(evento, c, ahora, cfg, whatsapp_bloqueado, plan.notas))
    # descartado y otro: nada propio más allá de cerrar la llamada.

    # N4: `otro`, o segunda llamada cortada con el mismo lead → revisión humana, además de lo anterior.
    cortadas = lead.cortadas_previas + (etiqueta in ETIQUETAS_CORTADA)
    if etiqueta == "otro" or (etiqueta in ETIQUETAS_CORTADA and cortadas >= 2):
        razon = "etiqueta otro" if etiqueta == "otro" else f"{cortadas}ª llamada cortada con el mismo lead"
        plan.notas.append(f"N4: {razon} → revisar_llamada")
        plan.acciones.append(_tarea(
            evento, cfg, "revisar_llamada",
            f"Revisar la llamada {evento.telephony.call_id} con {_nombre(evento)}",
            f"{razon}: {c.motivo}",
            asignada_a="cualquiera",
        ))

    # N2 aplicado a un lead que ya estaba dado de baja de antes.
    if lead.dado_de_baja:
        bloqueadas = [a.operacion for a in plan.acciones if a.operacion in OPERACIONES_BLOQUEADAS_POR_BAJA]
        plan.acciones = [a for a in plan.acciones if a.operacion not in OPERACIONES_BLOQUEADAS_POR_BAJA]
        if bloqueadas:
            plan.notas.append(f"N2: lead dado de baja, se descartan {bloqueadas}")
    return plan


def _reintento_voz(evento, c, ahora, cfg, whatsapp_bloqueado, notas) -> list[Accion]:
    """Calcula `no_antes_de` según la etiqueta. Gana siempre el plazo específico sobre el general."""
    etiqueta = c.etiqueta
    acciones: list[Accion] = []

    if etiqueta == "ocupado":
        # Rango [min, max]; se prefiere el punto medio (coincide con el ejemplo resuelto) y, si cae
        # fuera de la ventana, el instante válido del rango más cercano a él. Si ninguno del rango
        # es válido, el primer hueco a partir del mínimo.
        desde = tiempo.sumar_duracion(ahora, timedelta(minutes=cfg.ocupado_minutos_min), cfg)
        hasta = tiempo.sumar_duracion(ahora, timedelta(minutes=cfg.ocupado_minutos_max), cfg)
        medio = (cfg.ocupado_minutos_min + cfg.ocupado_minutos_max) / 2
        preferido = tiempo.sumar_duracion(ahora, timedelta(minutes=medio), cfg)
        cuando = tiempo.hueco_mas_cercano(preferido, desde, hasta, cfg)
        if cuando is None:
            cuando = tiempo.siguiente_hueco_valido(desde, cfg)
            notas.append("ocupado: ningún instante de [min, max] cae en la ventana, primer hueco válido")
    elif etiqueta in ETIQUETAS_CORTADA:
        # «Lo antes posible dentro de la ventana». Si el hueco supera cortada_horas_max (p. ej. se
        # cortó a las 19:50), se acepta igualmente el primer hueco válido: no hay otro mejor.
        cuando = tiempo.siguiente_hueco_valido(
            tiempo.sumar_duracion(ahora, timedelta(minutes=cfg.cortada_minutos_min), cfg), cfg
        )
        if cuando > tiempo.sumar_duracion(ahora, timedelta(hours=cfg.cortada_horas_max), cfg):
            notas.append(f"cortada: el primer hueco válido supera las {cfg.cortada_horas_max} h")
        if etiqueta == "visita_sin_confirmar":
            notas.append("N5: la visita no se reserva, se vuelve a llamar")
    elif etiqueta == "callback":
        pedido = c.callback_solicitado
        if pedido is not None and pedido > ahora:
            cuando = tiempo.siguiente_hueco_valido(pedido, cfg)
            if cuando != tiempo.a_zona(pedido, cfg):
                # Caso 12: la hora pedida cae fuera de la ventana → primera franja válida + aviso.
                notas.append(f"callback: {tiempo.formatear(pedido, cfg)} fuera de ventana → {tiempo.formatear(cuando, cfg)}")
                if whatsapp_bloqueado:
                    notas.append("N1: aviso_cambio_hora no se envía, el lead rechazó WhatsApp")
                else:
                    acciones.append(_whatsapp(evento, "aviso_cambio_hora", {
                        "nombre": _nombre(evento),
                        "hora_pedida": tiempo.formatear(pedido, cfg),
                        "hora_nueva": tiempo.formatear(cuando, cfg),
                    }))
        else:
            # Pidió que le llamen, pero sin momento concreto (o uno ya pasado): regla general.
            cuando = tiempo.siguiente_hueco_valido(
                tiempo.sumar_duracion(ahora, timedelta(hours=cfg.separacion_minima_horas), cfg), cfg
            )
            notas.append("callback sin hora concreta: se aplica la separación mínima")
    else:  # sin_respuesta, buzon: regla general
        cuando = tiempo.siguiente_hueco_valido(
            tiempo.sumar_duracion(ahora, timedelta(hours=cfg.separacion_minima_horas), cfg), cfg
        )

    motivo = _MOTIVO_REINTENTO[etiqueta]
    if etiqueta == "callback" and c.callback_texto:
        motivo = f"callback solicitado: «{c.callback_texto}»"
    llamada = Accion("programar_llamada", {
        "entry_id": evento.campaign.entry_id,
        "telefono": evento.lead.phone,
        "no_antes_de": tiempo.formatear(cuando, cfg),
        "motivo": motivo,
        "nota_contexto": c.nota_contexto or "no se llegó a hablar con el lead",
    })
    return [llamada, *acciones]


def _respaldo(evento, cfg, whatsapp_bloqueado, notas) -> list[Accion]:
    """N3: canal de respaldo declarado en la configuración."""
    if cfg.canal_respaldo != "whatsapp":
        notas.append(f"canal de respaldo «{cfg.canal_respaldo}» sin operación en el CRM: no se emite nada")
        return []
    if whatsapp_bloqueado:
        notas.append("N1: el canal de respaldo es WhatsApp y el lead lo rechazó: no se emite")
        return []
    return [_whatsapp(evento, "primer_toque_respaldo", {"nombre": _nombre(evento)})]


def _recordatorios_documentacion(evento, cfg, whatsapp_bloqueado, notas) -> list[Accion]:
    ahora = evento.occurred_at
    acciones = []
    if whatsapp_bloqueado:
        notas.append("N1: sin recordatorio por WhatsApp, el lead rechazó ese canal")
    else:
        cuando = tiempo.sumar_duracion(ahora, timedelta(hours=cfg.documentacion_lead_horas), cfg)
        acciones.append(_recordatorio(evento, cfg, "lead", cuando, {
            "canal": "whatsapp_lead", "plantilla": "recordatorio_documentacion",
        }))
    cuando = tiempo.sumar_dias_habiles(ahora, cfg.seguimiento_comercial_dias_habiles, cfg)
    acciones.append(_recordatorio(evento, cfg, "comercial", cuando, {
        "canal": "tarea_comercial", "tipo_tarea": "llamar_a_mano",
    }))
    return acciones


def _recordatorio(evento, cfg, distintivo: str, cuando: datetime, campos: dict) -> Accion:
    cuerpo = {
        "contact_id": evento.lead.contact_id,
        **campos,
        "cuando": tiempo.formatear(cuando, cfg),
        "cancelar_si": "lead_responde",
    }
    return Accion("programar_recordatorio", cuerpo, distintivo=distintivo, recordatorio={
        "canal": cuerpo["canal"], "cuando": cuerpo["cuando"], "cancelar_si": cuerpo["cancelar_si"],
    })


def _tarea_confirmar_visita(evento, c, cfg) -> Accion:
    cita = evento.agent_outcome.appointment
    inicio = tiempo.a_zona(cita.start_time, cfg)
    vence = tiempo.sumar_duracion(inicio, -timedelta(hours=cfg.confirmar_visita_margen_horas), cfg)
    # Si la visita es tan pronto que el margen ya ha pasado, la tarea vence ya.
    vence = max(vence, tiempo.a_zona(evento.occurred_at, cfg))
    direccion = evento.lead.property_address or "dirección no disponible en el lead: obtenerla y confirmarla"
    return _tarea(
        evento, cfg, "confirmar_visita_direccion",
        f"Confirmar dirección de la visita con {_nombre(evento)}",
        f"Visita {cita.appointment_id or ''} el {tiempo.formatear(inicio, cfg)} · inmueble "
        f"{evento.lead.property_ref or '?'} · {direccion}",
        vence_el=vence,
    )


def _tarea(evento, cfg, tipo, titulo, detalle, vence_el=None, asignada_a="comercial_asignado") -> Accion:
    if vence_el is None:
        vence_el = tiempo.sumar_dias_naturales(evento.occurred_at, cfg.vencimiento_por_defecto_dias, cfg)
    return Accion("crear_tarea", {
        "contact_id": evento.lead.contact_id,
        "call_id": evento.telephony.call_id if evento.telephony else None,
        "tipo": tipo,
        "titulo": titulo,
        "detalle": detalle,
        "vence_el": tiempo.formatear(vence_el, cfg),
        "asignada_a": asignada_a,
    }, distintivo=tipo)


def _whatsapp(evento, plantilla: str, parametros: dict) -> Accion:
    return Accion("enviar_plantilla_whatsapp", {
        "organization_id": evento.organization_id,
        "telefono": evento.lead.phone,
        "plantilla": plantilla,
        "parametros": parametros,
        "idioma": evento.lead.language or "es",
    }, distintivo=plantilla)


def _nombre(evento: Evento) -> str:
    return evento.lead.full_name or evento.lead.contact_id


# --------------------------------------------------------------------------- mensaje entrante

def planificar_mensaje(evento: Evento, lead: ContextoLead) -> Plan:
    """R7: el lead escribe → cancelar cada recordatorio pendiente que le programamos."""
    plan = Plan()
    for r in lead.recordatorios_pendientes:
        plan.acciones.append(Accion("cancelar_recordatorio", {
            "reminder_id": r.reminder_id,
            "motivo": "el lead ha respondido por WhatsApp",
        }, distintivo=r.reminder_id))
        plan.efectos.recordatorios_cancelados.append(r.reminder_id)
    plan.notas.append(f"R7: {len(lead.recordatorios_pendientes)} recordatorios pendientes a cancelar")
    return plan
