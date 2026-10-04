"""El grafo de LangGraph que procesa UN evento de principio a fin.

                        ┌──────────────────┐
                 START →│  cargar_evento   │ lee + valida contra evento.schema.json
                        └────────┬─────────┘
                        ┌────────▼─────────┐
                        │ consultar_estado │ lee SQLite: ¿hecho ya visto? ¿qué sabemos del lead?
                        └────────┬─────────┘
          ┌──────────────────────┼───────────────────────┬────────────────────────┐
   otra_organizacion         reentrega                 mensaje                  llamada
          │                      │                       │                        │
 decidir_otra_organizacion  decidir_reentrega   planificar_mensaje (R7)   clasificar_senalizacion
          │                      │                       │                 │             │
          │                      │                       │          ¿hay conversación?    │ resuelto
          │                      │                       │                 ▼              │
          │                      │                       │      clasificar_conversacion   │
          │                      │                       │       (LLM o simulado)         │
          │                      │                       │                 ▼              │
          │                      │                       │      consolidar_clasificacion  │
          │                      │                       │                 ▼              ▼
          │                      │                       │              aplicar_reglas (N1–N5, R3, R4)
          └──────────────────────┴───────────┬───────────┴──────────────────┘
                                   materializar_ordenes   ids, idempotency_key, R5
                                             ▼
                                   persistir_y_emitir     una transacción: SQLite + .jsonl
                                             ▼
                                            END

Decisiones de diseño:
- Las dependencias (config, repositorio, clasificador, salida) no viajan en el estado: se
  inyectan como `context` (context_schema + Runtime). El estado solo lleva datos del evento,
  serializables y fáciles de inspeccionar.
- Los nodos son funciones pequeñas que devuelven actualizaciones parciales del estado. La única
  clave con reducer es `traza` (operator.add): cada nodo añade líneas sin pisar las anteriores.
- Ningún nodo escribe estado hasta `persistir_y_emitir`. Si algo falla antes, no queda nada a
  medias: el evento se puede reprocesar limpio (R8).
- No se usa checkpointer de LangGraph: cada evento es un proceso nuevo y lo que hay que recordar
  entre eventos es estado de *negocio* por lead (intentos, recordatorios, bajas), no el estado
  de una ejecución del grafo. Eso vive en nuestro propio SQLite, con un esquema que se puede
  consultar.
"""
from __future__ import annotations

import json
import operator
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, TypedDict

import jsonschema
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from .clasificacion.conversacion import ClasificadorConversacion, a_clasificacion
from .clasificacion.senalizacion import clasificar_por_senalizacion
from .config import Config
from .modelos import Clasificacion, Decision, Evento, Orden
from .ordenes import materializar
from .persistencia import ContextoLead, EventoPrevio, Repositorio
from .reglas import Plan, planificar_llamada, planificar_mensaje
from .salida import Salida


# --------------------------------------------------------------------------- contexto y estado

@dataclass
class Dependencias:
    """Lo que los nodos necesitan y no forma parte del estado: se inyecta con `context=`."""

    cfg: Config
    repo: Repositorio
    clasificador: ClasificadorConversacion
    salida: Salida
    esquema_evento: dict


Via = Literal["otra_organizacion", "reentrega", "mensaje", "llamada"]
Resultado = Literal["procesado", "reentrega", "otra_organizacion"]


class Estado(TypedDict, total=False):
    ruta_evento: str                                  # entrada
    evento: Evento
    previo: EventoPrevio | None                       # decisión anterior del mismo hecho
    lead: ContextoLead                                # foto del lead antes de este evento
    via: Via                                          # rama elegida tras consultar el estado
    resultado: Resultado                              # cómo se registra la entrega
    clasificacion: Clasificacion | None
    plan: Plan
    ordenes: list[Orden]
    decision: Decision
    traza: Annotated[list[str], operator.add]         # log legible de lo que hizo cada nodo


# --------------------------------------------------------------------------- nodos

def cargar_evento(state: Estado, runtime: Runtime[Dependencias]) -> dict:
    datos = json.loads(Path(state["ruta_evento"]).read_text(encoding="utf-8"))
    jsonschema.validate(datos, runtime.context.esquema_evento)
    evento = Evento.model_validate(datos)
    return {
        "evento": evento,
        "traza": [f"evento {evento.event_id} · {evento.type} · {evento.occurred_at.isoformat()}"],
    }


def consultar_estado(state: Estado, runtime: Runtime[Dependencias]) -> dict:
    evento, deps = state["evento"], runtime.context
    previo = deps.repo.buscar_evento(evento.idempotency_key)
    lead = deps.repo.contexto_lead(evento.lead.contact_id, evento.occurred_at)

    # El orden importa: un evento ajeno no se mira más (R6), y una reentrega no se reclasifica.
    if evento.organization_id != deps.cfg.organization_id:
        via: Via = "otra_organizacion"
    elif previo is not None:
        via = "reentrega"
    elif evento.type == "message.received":
        via = "mensaje"
    else:
        via = "llamada"
    return {
        "previo": previo,
        "lead": lead,
        "via": via,
        "traza": [f"vía {via} · lead {evento.lead.contact_id}: {lead.intentos_previos} intentos previos, "
                  f"{lead.cortadas_previas} cortadas, baja={lead.dado_de_baja}, "
                  f"{len(lead.recordatorios_pendientes)} recordatorios pendientes"],
    }


def decidir_otra_organizacion(state: Estado, runtime: Runtime[Dependencias]) -> dict:
    evento = state["evento"]
    motivo = (f"evento de la organización {evento.organization_id}, ajena a la campaña "
              f"({runtime.context.cfg.organization_id}): no se emite ninguna orden")
    return {
        "resultado": "otra_organizacion",
        "clasificacion": Clasificacion(etiqueta="no_aplica", motivo=motivo, confianza=1.0, fuente="reglas"),
        "plan": Plan(notas=["R6: otra organización, cero órdenes"]),
    }


def decidir_reentrega(state: Estado) -> dict:
    evento, previo = state["evento"], state["previo"]
    motivo = (f"reentrega de {evento.idempotency_key} (delivery_attempt {evento.delivery_attempt}), "
              f"ya procesado en {previo.event_id}: {previo.motivo}")
    return {
        "resultado": "reentrega",
        # Una reentrega repite la etiqueta que ya tenía el hecho; no se vuelve a clasificar.
        "clasificacion": Clasificacion(etiqueta=previo.etiqueta, motivo=motivo,
                                       confianza=previo.confianza, fuente="reglas"),
        "plan": Plan(notas=["R5: reentrega, cero órdenes nuevas"]),
    }


def planificar_mensaje_entrante(state: Estado) -> dict:
    evento, lead = state["evento"], state["lead"]
    plan = planificar_mensaje(evento, lead)
    n = len(lead.recordatorios_pendientes)
    motivo = f"el lead escribe por WhatsApp: se cancelan {n} recordatorios pendientes" if n else \
        "el lead escribe por WhatsApp: no tenía recordatorios pendientes"
    return {
        "resultado": "procesado",
        "clasificacion": Clasificacion(etiqueta="no_aplica", motivo=motivo, confianza=1.0, fuente="reglas"),
        "plan": plan,
    }


def clasificar_senalizacion(state: Estado) -> dict:
    clasificacion = clasificar_por_senalizacion(state["evento"])
    if clasificacion is None:
        return {"clasificacion": None, "traza": ["señalización: contestó una persona, hay que leer la conversación"]}
    return {"clasificacion": clasificacion,
            "traza": [f"señalización → {clasificacion.etiqueta} ({clasificacion.motivo})"]}


def clasificar_conversacion(state: Estado, runtime: Runtime[Dependencias]) -> dict:
    deps = runtime.context
    try:
        salida = deps.clasificador.clasificar(state["evento"], deps.cfg)
        clasificacion = a_clasificacion(salida, deps.clasificador.fuente, deps.cfg)
        traza = f"{deps.clasificador.nombre} → {clasificacion.etiqueta} ({salida.razonamiento})"
    except Exception as error:  # noqa: BLE001 — cualquier fallo del modelo acaba en revisión humana
        # El modelo no responde o devuelve basura tras los reintentos del cliente: la llamada no
        # se pierde, se etiqueta `otro` con confianza baja y N4 crea una tarea revisar_llamada.
        clasificacion = Clasificacion(
            etiqueta="otro", confianza=0.3, fuente="fallback",
            motivo=f"no se pudo clasificar la conversación automáticamente ({type(error).__name__})",
        )
        traza = f"clasificador falló: {error!r} → otro"
    return {"clasificacion": clasificacion, "traza": [traza]}


def consolidar_clasificacion(state: Estado) -> dict:
    """Guardas deterministas sobre lo que dijo el modelo. Hechos del evento > interpretación.

    - La cita existe o no existe en el CRM: eso lo dice `agent_outcome.appointment`, no el modelo.
      Con cita → visita_reservada (salvo baja, que manda sobre todo). Sin cita, el modelo no puede
      decir visita_reservada → visita_sin_confirmar (N5).
    - documentacion_enviada exige WhatsApp aceptado: si lo rechazó, es documentacion_pendiente.
    """
    evento, c = state["evento"], state["clasificacion"]
    hay_cita = evento.agent_outcome.appointment is not None
    ajuste = None
    if c.etiqueta != "no_contactar" and hay_cita and c.etiqueta != "visita_reservada":
        ajuste = ("visita_reservada", f"cita {evento.agent_outcome.appointment.appointment_id} creada en el CRM")
    elif c.etiqueta == "visita_reservada" and not hay_cita:
        ajuste = ("visita_sin_confirmar", "visita acordada pero sin cita creada en el CRM")
    elif c.etiqueta == "documentacion_enviada" and c.whatsapp_rechazado:
        ajuste = ("documentacion_pendiente", "pide documentación pero rechaza WhatsApp")

    if ajuste is None:
        return {"traza": ["consolidación: sin ajustes"]}
    etiqueta, motivo = ajuste
    return {
        "clasificacion": c.model_copy(update={"etiqueta": etiqueta, "motivo": motivo, "fuente": "reglas"}),
        "traza": [f"consolidación: {c.etiqueta} → {etiqueta} ({motivo})"],
    }


def aplicar_reglas(state: Estado, runtime: Runtime[Dependencias]) -> dict:
    plan = planificar_llamada(state["evento"], state["clasificacion"], state["lead"], runtime.context.cfg)
    return {"resultado": "procesado", "plan": plan}


def materializar_ordenes(state: Estado, runtime: Runtime[Dependencias]) -> dict:
    evento, c, plan = state["evento"], state["clasificacion"], state["plan"]
    ordenes = materializar(plan, evento, runtime.context.repo)
    decision = Decision(
        event_id=evento.event_id,
        call_id=evento.telephony.call_id if evento.telephony else None,
        etiqueta=c.etiqueta,
        motivo=c.motivo,
        confianza=round(c.confianza, 2),
        ordenes=[o.orden_id for o in ordenes],
    )
    return {
        "ordenes": ordenes,
        "decision": decision,
        "traza": [*(f"regla: {n}" for n in plan.notas),
                  f"órdenes: {[o.operacion for o in ordenes] or 'ninguna'}"],
    }


def persistir_y_emitir(state: Estado, runtime: Runtime[Dependencias]) -> dict:
    deps, evento = runtime.context, state["evento"]
    decision, ordenes = state["decision"], state["ordenes"]
    deps.repo.registrar_procesamiento(
        evento_tipo=evento.type,
        contact_id=evento.lead.contact_id,
        idempotency_key=evento.idempotency_key,
        resultado=state["resultado"],
        decision=decision,
        ordenes=ordenes,
        efectos=state["plan"].efectos,
        occurred_at=evento.occurred_at.isoformat(),
        emitir=lambda: deps.salida.escribir(decision, ordenes),
    )
    return {"traza": [f"persistido ({state['resultado']}) y emitido"]}


# --------------------------------------------------------------------------- aristas condicionales

def tras_consultar_estado(state: Estado) -> Via:
    return state["via"]


def tras_senalizacion(state: Estado) -> Literal["conversacion", "resuelto"]:
    return "conversacion" if state["clasificacion"] is None else "resuelto"


# --------------------------------------------------------------------------- construcción

def construir_grafo():
    g = StateGraph(Estado, context_schema=Dependencias)

    g.add_node("cargar_evento", cargar_evento)
    g.add_node("consultar_estado", consultar_estado)
    g.add_node("decidir_otra_organizacion", decidir_otra_organizacion)
    g.add_node("decidir_reentrega", decidir_reentrega)
    g.add_node("planificar_mensaje", planificar_mensaje_entrante)
    g.add_node("clasificar_senalizacion", clasificar_senalizacion)
    g.add_node("clasificar_conversacion", clasificar_conversacion)
    g.add_node("consolidar_clasificacion", consolidar_clasificacion)
    g.add_node("aplicar_reglas", aplicar_reglas)
    g.add_node("materializar_ordenes", materializar_ordenes)
    g.add_node("persistir_y_emitir", persistir_y_emitir)

    g.add_edge(START, "cargar_evento")
    g.add_edge("cargar_evento", "consultar_estado")
    g.add_conditional_edges("consultar_estado", tras_consultar_estado, {
        "otra_organizacion": "decidir_otra_organizacion",
        "reentrega": "decidir_reentrega",
        "mensaje": "planificar_mensaje",
        "llamada": "clasificar_senalizacion",
    })
    g.add_conditional_edges("clasificar_senalizacion", tras_senalizacion, {
        "conversacion": "clasificar_conversacion",
        "resuelto": "aplicar_reglas",
    })
    g.add_edge("clasificar_conversacion", "consolidar_clasificacion")
    g.add_edge("consolidar_clasificacion", "aplicar_reglas")

    for nodo in ("decidir_otra_organizacion", "decidir_reentrega", "planificar_mensaje", "aplicar_reglas"):
        g.add_edge(nodo, "materializar_ordenes")
    g.add_edge("materializar_ordenes", "persistir_y_emitir")
    g.add_edge("persistir_y_emitir", END)

    return g.compile()
