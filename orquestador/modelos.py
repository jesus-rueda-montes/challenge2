"""Tipos de dominio: evento de entrada, clasificación, órdenes y decisión.

El evento ya viene validado contra `esquemas/evento.schema.json`; estos modelos Pydantic solo
dan acceso tipado a los campos que el orquestador usa (el resto se ignora).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- catálogos cerrados

Etiqueta = Literal[
    "visita_reservada",
    "documentacion_enviada",
    "callback",
    "sin_respuesta",
    "ocupado",
    "buzon",
    "cortada",
    "visita_sin_confirmar",
    "persona_equivocada",
    "no_contactar",
    "rechazada",
    "documentacion_pendiente",
    "descartado",
    "otro",
    "no_aplica",
]

# Estado de cola que lleva `cerrar_llamada`. Fijado por la etiqueta (tabla de casos.md).
STATUS_POR_ETIQUETA: dict[str, str] = {
    "visita_reservada": "successful",
    "documentacion_enviada": "completed",
    "documentacion_pendiente": "completed",
    "callback": "callback_requested",
    "sin_respuesta": "no_answer",
    "ocupado": "no_answer",
    "buzon": "no_answer",
    "cortada": "needs_review",
    "visita_sin_confirmar": "needs_review",
    "otro": "needs_review",
    "persona_equivocada": "failed",
    "no_contactar": "dnc",
    "rechazada": "refused",
    "descartado": "skipped",
}

# Etiquetas cuya acción natural es volver a llamar por voz. Si los intentos están agotados,
# todas pasan al canal de respaldo (N3).
ETIQUETAS_REINTENTO_VOZ = frozenset(
    {"sin_respuesta", "ocupado", "buzon", "cortada", "visita_sin_confirmar", "callback"}
)

# Las dos formas de «llamada cortada» que cuentan para N4.
ETIQUETAS_CORTADA = frozenset({"cortada", "visita_sin_confirmar"})

# Operaciones que generan contacto con el lead (directo, o una tarea para que un humano le
# contacte). N2 las bloquea para un lead dado de baja; cerrar_llamada y cancelar_recordatorio no
# contactan a nadie y se mantienen.
OPERACIONES_BLOQUEADAS_POR_BAJA = frozenset(
    {"programar_llamada", "enviar_plantilla_whatsapp", "programar_recordatorio", "crear_tarea"}
)


# --------------------------------------------------------------------------- evento de entrada

class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Campaign(_Base):
    system_key: str
    entry_id: str


class Lead(_Base):
    contact_id: str
    phone: str
    full_name: str | None = None
    property_ref: str | None = None
    property_address: str | None = None
    language: str = "es"


class Amd(_Base):
    result: str = "not_run"
    greeting_transcript: str | None = None
    source: str = "none"


class Telephony(_Base):
    call_id: str
    answered_at: datetime | None = None
    ended_at: datetime
    sip_status_code: int
    sip_status: str | None = None
    disconnect_reason: str
    hung_up_by: str | None = None
    duration_seconds: int
    amd: Amd = Field(default_factory=Amd)


class Turno(_Base):
    role: Literal["agent", "user"]
    message: str
    time_in_call_secs: int


class Appointment(_Base):
    appointment_id: str | None = None
    start_time: datetime


class AgentOutcome(_Base):
    appointment: Appointment | None = None
    slots_snapshot: dict[str, Any] = Field(default_factory=dict)


class Mensaje(_Base):
    channel: str
    text: str


class Evento(_Base):
    event_id: str
    type: Literal["call.ended", "message.received"]
    occurred_at: datetime
    organization_id: str
    idempotency_key: str
    delivery_attempt: int = 1
    campaign: Campaign
    lead: Lead
    telephony: Telephony | None = None
    transcript: list[Turno] = Field(default_factory=list)
    agent_outcome: AgentOutcome = Field(default_factory=AgentOutcome)
    message: Mensaje | None = None


# --------------------------------------------------------------------------- clasificación

class Clasificacion(BaseModel):
    """Resultado de clasificar una llamada, venga de la señalización o de la conversación."""

    etiqueta: Etiqueta
    motivo: str
    confianza: float = Field(ge=0, le=1)
    fuente: Literal["senalizacion", "llm", "simulado", "reglas", "fallback"]
    # Datos extraídos de la conversación que necesitan las órdenes.
    callback_solicitado: datetime | None = None
    callback_texto: str | None = None
    whatsapp_rechazado: bool = False
    email: str | None = None
    nota_contexto: str | None = None


# --------------------------------------------------------------------------- salida

class Orden(BaseModel):
    """Una línea de `salida/ordenes.jsonl`: la petición que se habría hecho al CRM."""

    orden_id: str
    event_id: str
    operacion: str
    idempotency_key: str
    cuerpo: dict[str, Any]


class Decision(BaseModel):
    """Una línea de `salida/decisiones.jsonl`."""

    event_id: str
    call_id: str | None
    etiqueta: Etiqueta
    motivo: str
    confianza: float = Field(ge=0, le=1)
    ordenes: list[str]
