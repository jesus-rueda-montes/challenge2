"""Clasificación de una llamada contestada leyendo la conversación.

Dos implementaciones con la misma interfaz (`ClasificadorConversacion`):
- `ClasificadorLLM`: modelo de OpenAI con salida estructurada (el modo real).
- `ClasificadorSimulado` (en simulado.py): reglas sobre el texto, sin red, para probar sin clave.

Ambas devuelven una `SalidaClasificador`; `a_clasificacion` la convierte al tipo de dominio.
El modelo solo clasifica y extrae: fechas, plazos y reglas de negocio se calculan en código.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from string import Template
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from ..config import DIAS_SEMANA, Config
from ..modelos import Clasificacion, Evento

RAIZ_PROMPTS = Path(__file__).resolve().parents[2] / "prompts"

# Subconjunto del catálogo que se decide leyendo la conversación. Las etiquetas de señalización
# (ocupado, sin_respuesta, buzon, rechazada) nunca llegan aquí.
EtiquetaConversacion = Literal[
    "visita_reservada",
    "visita_sin_confirmar",
    "documentacion_enviada",
    "documentacion_pendiente",
    "callback",
    "cortada",
    "persona_equivocada",
    "no_contactar",
    "descartado",
    "otro",
]


class SalidaClasificador(BaseModel):
    """Esquema de salida estructurada que se pide al modelo. El orden de campos importa:
    `razonamiento` va primero para que el modelo justifique antes de elegir etiqueta."""

    razonamiento: str = Field(description="Una o dos frases citando la frase decisiva.")
    etiqueta: EtiquetaConversacion
    motivo: str = Field(description="Una frase en español que explica la etiqueta.")
    # Sin ge/le a propósito: el modo estricto de OpenAI no admite minimum/maximum en todas las
    # versiones. El rango [0, 1] se garantiza al convertir (a_clasificacion).
    confianza: float = Field(description="Entre 0 y 1.")
    callback_solicitado: str | None = Field(
        description="YYYY-MM-DDTHH:MM local Europe/Madrid, solo si etiqueta=callback y hay momento."
    )
    callback_texto: str | None = Field(description="El momento tal cual lo dijo el lead.")
    whatsapp_rechazado: bool
    email: str | None
    nota_contexto: str = Field(description="Lo ya hablado, para no repetir preguntas.")


class ClasificadorConversacion(Protocol):
    nombre: str   # para trazas y README
    fuente: str   # valor de Clasificacion.fuente: "llm" | "simulado"

    def clasificar(self, evento: Evento, cfg: Config) -> SalidaClasificador: ...


# --------------------------------------------------------------------------- prompts

def cargar_prompt(nombre: str) -> str:
    return (RAIZ_PROMPTS / nombre).read_text(encoding="utf-8")


def mensaje_usuario(evento: Evento, cfg: Config) -> str:
    """Rellena la plantilla `prompts/clasificador_usuario.md` con los datos del evento.

    `string.Template` y no `str.format`: la transcripción puede traer llaves.
    """
    ahora = evento.occurred_at.astimezone(cfg.zona)
    t = evento.telephony
    cita = evento.agent_outcome.appointment
    transcripcion = "\n".join(
        f"[{turno.time_in_call_secs:>4}s] {'AGENTE' if turno.role == 'agent' else 'LEAD'}: {turno.message}"
        for turno in evento.transcript
    )
    return Template(cargar_prompt("clasificador_usuario.md")).substitute(
        ahora=ahora.isoformat(),
        dia_semana=DIAS_SEMANA[ahora.weekday()],
        nombre=evento.lead.full_name or "desconocido",
        inmueble=evento.lead.property_address or evento.lead.property_ref or "desconocido",
        cita=f"sí, {cita.start_time.isoformat()}" if cita else "no",
        colgo=t.hung_up_by or "desconocido (posible caída de línea)",
        slots=json.dumps(evento.agent_outcome.slots_snapshot, ensure_ascii=False) or "{}",
        transcripcion=transcripcion or "(vacía)",
    )


# --------------------------------------------------------------------------- LLM

class ClasificadorLLM:
    fuente = "llm"

    def __init__(self, modelo: str):
        # Import diferido: el modo simulado no necesita langchain-openai ni clave.
        from langchain_openai import ChatOpenAI

        self.nombre = modelo
        # Los modelos de razonamiento (gpt-5*, o*) rechazan `temperature`; el resto, a 0 para
        # que la misma llamada dé la misma etiqueta.
        razonamiento = modelo.startswith(("gpt-5", "o1", "o3", "o4"))
        opciones = {} if razonamiento else {"temperature": 0}
        llm = ChatOpenAI(model=modelo, timeout=30, max_retries=2, **opciones)
        # method="json_schema" usa Structured Outputs de OpenAI: el modelo no puede devolver una
        # etiqueta fuera del enum ni omitir campos.
        self._llm = llm.with_structured_output(SalidaClasificador, method="json_schema", strict=True)
        self._sistema = cargar_prompt("clasificador_sistema.md")

    def clasificar(self, evento: Evento, cfg: Config) -> SalidaClasificador:
        return self._llm.invoke(
            [("system", self._sistema), ("human", mensaje_usuario(evento, cfg))]
        )


# --------------------------------------------------------------------------- conversión

def a_clasificacion(salida: SalidaClasificador, fuente: str, cfg: Config) -> Clasificacion:
    callback = None
    if salida.callback_solicitado:
        try:
            callback = datetime.fromisoformat(salida.callback_solicitado)
            if callback.tzinfo is None:
                callback = callback.replace(tzinfo=cfg.zona)
        except ValueError:
            callback = None  # fecha malformada: se tratará como «callback sin hora concreta»
    return Clasificacion(
        etiqueta=salida.etiqueta,
        motivo=salida.motivo,
        confianza=min(max(salida.confianza, 0.0), 1.0),
        fuente=fuente,
        callback_solicitado=callback,
        callback_texto=salida.callback_texto,
        whatsapp_rechazado=salida.whatsapp_rechazado,
        email=salida.email,
        nota_contexto=salida.nota_contexto,
    )
