"""Escenarios de verificación: el lote oficial y un lote sintético con lo que el oficial no cubre.

Cada escenario fija lo que espero *antes* de ejecutar (calculado a mano desde casos.md y
campana.yaml): etiqueta, multiconjunto de operaciones y algunos campos clave. El lote sintético
se genera como ficheros JSON reales que pasan por run.py igual que los oficiales.

Calendario de referencia (septiembre 2026): mar 15 · mié 16 · jue 17 · vie 18 · sáb 19 ·
dom 20 · lun 21. Ventana: L-V 10:00–20:00, sáb 10:00–14:00, dom cerrado.
"""
from __future__ import annotations

import json
from pathlib import Path

# --------------------------------------------------------------------------- lote oficial

ESPERADO_OFICIAL = {
    "01-call-ended-nuria.json": ("sin_respuesta", ["cerrar_llamada", "programar_llamada"],
                                 {"programar_llamada.no_antes_de": "2026-09-15T12:12:00+02:00"}),
    "02-call-ended-tomas.json": ("ocupado", ["cerrar_llamada", "programar_llamada"],
                                 {"programar_llamada.no_antes_de": "2026-09-15T11:31:00+02:00"}),
    "03-call-ended-elena.json": ("persona_equivocada", ["cerrar_llamada", "crear_tarea"],
                                 {"crear_tarea.tipo": "verificar_telefono",
                                  "crear_tarea.vence_el": "2026-09-17T11:04:00+02:00"}),
    "04-call-ended-rosa.json": ("cortada", ["cerrar_llamada", "programar_llamada"],
                                {"programar_llamada.no_antes_de": "2026-09-15T12:17:00+02:00"}),
    "05-call-ended-nuria.json": ("sin_respuesta", ["cerrar_llamada", "programar_llamada"],
                                 {"programar_llamada.no_antes_de": "2026-09-15T14:20:00+02:00"}),
    "06-call-ended-pedro.json": ("no_contactar", ["cerrar_llamada", "marcar_no_contactar"],
                                 {"marcar_no_contactar.canal": "todos", "cerrar_llamada.status": "dnc"}),
    "07-call-ended-laura.json": ("visita_reservada", ["cerrar_llamada", "crear_tarea"],
                                 {"crear_tarea.tipo": "confirmar_visita_direccion",
                                  "crear_tarea.vence_el": "2026-09-17T09:00:00+02:00"}),
    "08-call-ended-marcos.json": ("documentacion_enviada",
                                  ["cerrar_llamada", "programar_recordatorio", "programar_recordatorio"],
                                  {"programar_recordatorio[0].cuando": "2026-09-17T16:42:00+02:00",
                                   "programar_recordatorio[1].cuando": "2026-09-18T16:42:00+02:00"}),
    "09-call-ended-javier.json": ("callback", ["cerrar_llamada", "programar_llamada"],
                                  {"programar_llamada.no_antes_de": "2026-09-16T18:00:00+02:00"}),
    "10-call-ended-sonia.json": ("buzon", ["cerrar_llamada", "programar_llamada"],
                                 {"programar_llamada.no_antes_de": "2026-09-15T19:30:00+02:00"}),
    "11-call-ended-carla.json": ("visita_sin_confirmar", ["cerrar_llamada", "programar_llamada"],
                                 {"programar_llamada.no_antes_de": "2026-09-15T18:20:00+02:00"}),
    "12-call-ended-nuria.json": ("buzon", ["cerrar_llamada", "enviar_plantilla_whatsapp"],
                                 {"enviar_plantilla_whatsapp.plantilla": "primer_toque_respaldo"}),
    "13-call-ended-ivan.json": ("documentacion_pendiente", ["cerrar_llamada", "crear_tarea"],
                                {"crear_tarea.tipo": "enviar_documentacion_email"}),
    "14-message-received-marcos.json": ("no_aplica", ["cancelar_recordatorio", "cancelar_recordatorio"], {}),
    "15-call-ended-javier-reentrega.json": ("callback", [], {}),
    "16-call-ended-alberto.json": ("no_aplica", [], {}),
}

# --------------------------------------------------------------------------- lote sintético

_SECUENCIA = {"n": 0}


def _llamada(contacto: str, cuando: str, *, sip=200, sip_texto="OK", desconexion="CLIENT_INITIATED",
             colgo="callee", amd="human", amd_fuente="livekit_amd", saludo=None, turnos=(),
             cita=None, slots=None, org="org_demo_a", idem=None, entrega=1, nombre="Lead Sintético"):
    _SECUENCIA["n"] += 1
    n = _SECUENCIA["n"]
    call_id = idem or f"lk-sint-{n:03d}"
    contestada = sip == 200
    transcript, t = [], 2
    for rol, texto in turnos:
        transcript.append({"role": rol, "message": texto, "time_in_call_secs": t})
        t += 6
    return {
        "event_id": f"evt_s{n:02d}",
        "type": "call.ended",
        "occurred_at": cuando,
        "organization_id": org,
        "idempotency_key": call_id,
        "delivery_attempt": entrega,
        "campaign": {"system_key": "first_touch_voice", "entry_id": f"ce_{contacto}"},
        "lead": {"contact_id": contacto, "phone": f"+3460099{contacto[-4:]:0>4}", "full_name": nombre,
                 "lead_source": "idealista", "property_ref": "RIB-9000",
                 "property_address": "calle Falsa 1, Madrid", "language": "es"},
        "telephony": {
            "provider": "livekit_sip", "call_id": call_id, "provider_call_id": None,
            "dialed_at": cuando, "ringing_at": None, "answered_at": cuando if contestada else None,
            "ended_at": cuando, "sip_status_code": sip, "sip_status": sip_texto,
            "disconnect_reason": desconexion, "hung_up_by": colgo if contestada else None,
            "duration_seconds": t if contestada else 0,
            "amd": {"result": amd if contestada else "not_run", "greeting_transcript": saludo,
                    "detected_at_secs": None, "source": amd_fuente if contestada else "none"},
        },
        "transcript": transcript,
        "agent_outcome": {"appointment": cita, "slots_snapshot": slots or {}},
        "recording": None,
        "metrics": None,
    }


def _mensaje(contacto: str, cuando: str, texto: str, *, idem: str, org="org_demo_a"):
    _SECUENCIA["n"] += 1
    return {
        "event_id": f"evt_s{_SECUENCIA['n']:02d}", "type": "message.received", "occurred_at": cuando,
        "organization_id": org, "idempotency_key": idem, "delivery_attempt": 1,
        "campaign": {"system_key": "first_touch_voice", "entry_id": f"ce_{contacto}"},
        "lead": {"contact_id": contacto, "phone": f"+3460099{contacto[-4:]:0>4}", "language": "es"},
        "message": {"channel": "whatsapp", "text": texto},
    }


def _sin_respuesta(contacto, cuando):
    return _llamada(contacto, cuando, sip=480, sip_texto="Temporarily Unavailable",
                    desconexion="USER_UNAVAILABLE")


SALUDO = [("agent", "Hola, muy buenas. ¿Hablo con el titular?"), ("user", "Sí, soy yo.")]


def escenarios_sinteticos() -> list[tuple[str, dict | str, tuple]]:
    """(nombre de fichero, evento o texto crudo, (etiqueta, operaciones, campos, código esperado))."""
    _SECUENCIA["n"] = 0
    e = []

    def add(nombre, evento, etiqueta, ops, campos=None, codigo=0):
        e.append((nombre, evento, (etiqueta, ops, campos or {}, codigo)))

    # Caso 11 ⚠ rechazada: 603 → respaldo, sin reintento por voz.
    add("s01-rechazada.json", _llamada("c_9001", "2026-09-18T11:00:00+02:00", sip=603,
        sip_texto="Decline D21", desconexion="USER_REJECTED"),
        "rechazada", ["cerrar_llamada", "enviar_plantilla_whatsapp"],
        {"cerrar_llamada.status": "refused", "enviar_plantilla_whatsapp.plantilla": "primer_toque_respaldo"})

    # Caso 12 ⚠ callback fuera de ventana: viernes pide «mañana a las cinco de la tarde» (sábado 17:00,
    # ventana del sábado hasta 14:00) → primer hueco válido: lunes 10:00 + aviso_cambio_hora.
    add("s02-callback-fuera-ventana.json", _llamada("c_9002", "2026-09-18T12:00:00+02:00", turnos=[
        *SALUDO, ("agent", "Te llamo por el piso de calle Falsa. ¿Tienes un momento?"),
        ("user", "Ahora no puedo. ¿Me llamas mañana a las cinco de la tarde?"),
        ("agent", "Claro, te llamamos entonces. Un saludo.")],
        slots={"callback_when_raw": "mañana a las cinco de la tarde"}),
        "callback", ["cerrar_llamada", "programar_llamada", "enviar_plantilla_whatsapp"],
        {"programar_llamada.no_antes_de": "2026-09-21T10:00:00+02:00",
         "enviar_plantilla_whatsapp.plantilla": "aviso_cambio_hora"})

    # Caso 15 ⚠ descartado: ya compró; que cuelgue seco no lo hace `cortada`.
    add("s03-descartado.json", _llamada("c_9003", "2026-09-18T11:30:00+02:00", turnos=[
        *SALUDO, ("agent", "Te llamo por la vivienda de calle Falsa. ¿Sigues buscando?"),
        ("user", "No, ya hemos comprado uno hace dos semanas. Ya no busco nada.")]),
        "descartado", ["cerrar_llamada"], {"cerrar_llamada.status": "skipped"})

    # 5xx → otro + N4.
    add("s04-trunk-5xx.json", _llamada("c_9004", "2026-09-18T11:40:00+02:00", sip=503,
        sip_texto="Service Unavailable", desconexion="SIP_TRUNK_FAILURE"),
        "otro", ["cerrar_llamada", "crear_tarea"],
        {"crear_tarea.tipo": "revisar_llamada", "cerrar_llamada.status": "needs_review"})

    # IVR → otro + N4.
    add("s05-ivr.json", _llamada("c_9005", "2026-09-18T11:50:00+02:00", amd="machine-ivr",
        colgo="agent", desconexion="ROOM_DELETED", saludo="Pulse uno para ventas, dos para..."),
        "otro", ["cerrar_llamada", "crear_tarea"], {"crear_tarea.tipo": "revisar_llamada"})

    # Ocupado a las 19:45: [20:15, 21:15] fuera de ventana → sábado 10:00.
    add("s06-ocupado-1945.json", _llamada("c_9006", "2026-09-18T19:45:00+02:00", sip=486,
        sip_texto="Busy Here", desconexion="USER_REJECTED"),
        "ocupado", ["cerrar_llamada", "programar_llamada"],
        {"programar_llamada.no_antes_de": "2026-09-19T10:00:00+02:00"})

    # Ocupado a las 19:10: el +60 (20:10) cae fuera; el punto del rango más cercano válido es 20:00.
    add("s07-ocupado-1910.json", _llamada("c_9007", "2026-09-18T19:10:00+02:00", sip=486,
        sip_texto="Busy Here", desconexion="USER_REJECTED"),
        "ocupado", ["cerrar_llamada", "programar_llamada"],
        {"programar_llamada.no_antes_de": "2026-09-18T20:00:00+02:00"})

    # Cortada el sábado 13:50: +30 = 14:20 > cierre 14:00, domingo cerrado → lunes 10:00.
    add("s08-cortada-sabado.json", _llamada("c_9008", "2026-09-19T13:50:00+02:00", colgo=None, turnos=[
        *SALUDO, ("agent", "¿Buscas para comprar o alquilar?"), ("user", "Para alquilar, en la zona de…")],
        slots={"operacion": "alquiler"}),
        "cortada", ["cerrar_llamada", "programar_llamada"],
        {"programar_llamada.no_antes_de": "2026-09-21T10:00:00+02:00"})

    # Segunda cortada con el mismo lead → reintento + revisar_llamada (N4).
    add("s09-segunda-cortada.json", _llamada("c_9008", "2026-09-21T10:05:00+02:00", colgo=None, turnos=[
        *SALUDO, ("agent", "Seguimos donde lo dejamos. ¿Qué presupuesto manejas?"), ("user", "Pues unos mil cien, más o…")],
        slots={"operacion": "alquiler"}),
        "cortada", ["cerrar_llamada", "programar_llamada", "crear_tarea"],
        {"programar_llamada.no_antes_de": "2026-09-21T10:35:00+02:00", "crear_tarea.tipo": "revisar_llamada"})

    # «Ya encontré piso y no me llaméis más» → no_contactar, no descartado.
    add("s10-baja-y-descartado.json", _llamada("c_9010", "2026-09-18T12:10:00+02:00", turnos=[
        *SALUDO, ("agent", "Te llamo por el piso de calle Falsa."),
        ("user", "Ya encontré piso y no me llaméis más, por favor."),
        ("agent", "Entendido, disculpa la molestia.")]),
        "no_contactar", ["cerrar_llamada", "marcar_no_contactar"], {"marcar_no_contactar.canal": "todos"})

    # Callback con los intentos agotados (3.º intento) → N3: respaldo, sin llamada.
    add("s11a-agotar-1.json", _sin_respuesta("c_9011", "2026-09-18T10:00:00+02:00"),
        "sin_respuesta", ["cerrar_llamada", "programar_llamada"])
    add("s11b-agotar-2.json", _sin_respuesta("c_9011", "2026-09-18T12:00:00+02:00"),
        "sin_respuesta", ["cerrar_llamada", "programar_llamada"])
    add("s11c-callback-agotado.json", _llamada("c_9011", "2026-09-18T14:00:00+02:00", turnos=[
        *SALUDO, ("user", "Estoy conduciendo, ¿me puedes llamar el lunes a las once?"),
        ("agent", "Claro, hasta el lunes.")]),
        "callback", ["cerrar_llamada", "enviar_plantilla_whatsapp"],
        {"enviar_plantilla_whatsapp.plantilla": "primer_toque_respaldo"})

    # Lead que rechaza WhatsApp y luego agota intentos → N1 impide el respaldo por WhatsApp.
    add("s12a-rechaza-whatsapp.json", _llamada("c_9012", "2026-09-18T10:30:00+02:00", turnos=[
        *SALUDO, ("user", "Quiero la nota simple del piso."),
        ("agent", "Te la mando por WhatsApp ahora mismo."),
        ("user", "No, por WhatsApp no. Mándamela al correo, lead9012@example.com. Gracias."),
        ("agent", "Perfecto, te llega por correo. Un saludo.")]),
        "documentacion_pendiente", ["cerrar_llamada", "crear_tarea"], {"crear_tarea.tipo": "enviar_documentacion_email"})
    add("s12b-sin-respuesta.json", _sin_respuesta("c_9012", "2026-09-18T13:00:00+02:00"),
        "sin_respuesta", ["cerrar_llamada", "programar_llamada"])
    add("s12c-agotado-sin-whatsapp.json", _sin_respuesta("c_9012", "2026-09-18T16:00:00+02:00"),
        "sin_respuesta", ["cerrar_llamada"])

    # Lead dado de baja que vuelve a aparecer en una llamada → N2: solo cerrar_llamada.
    add("s13a-baja.json", _llamada("c_9013", "2026-09-18T10:40:00+02:00", turnos=[
        *SALUDO, ("user", "Dadme de baja, no quiero que me llaméis."), ("agent", "Hecho. Disculpa.")]),
        "no_contactar", ["cerrar_llamada", "marcar_no_contactar"])
    add("s13b-llamada-tras-baja.json", _sin_respuesta("c_9013", "2026-09-18T15:00:00+02:00"),
        "sin_respuesta", ["cerrar_llamada"])

    # Mensaje de un lead sin recordatorios pendientes → no_aplica, cero órdenes; y su reentrega.
    add("s14-mensaje-sin-recordatorios.json", _mensaje("c_9003", "2026-09-18T18:00:00+02:00",
        "Hola, ¿al final qué?", idem="wa-s14"), "no_aplica", [])
    add("s15-mensaje-reentregado.json", _mensaje("c_9003", "2026-09-18T18:05:00+02:00",
        "Hola, ¿al final qué?", idem="wa-s14"), "no_aplica", [])

    # AMD `uncertain` se trata como persona: se lee la conversación.
    add("s16-amd-uncertain-callback.json", _llamada("c_9016", "2026-09-18T18:00:00+02:00",
        idem="lk-sint-uncertain", amd="uncertain", turnos=[*SALUDO, ("user", "Ahora no me pilla bien, llámame el lunes a las 11."),
                                 ("agent", "Perfecto, el lunes a las 11. Un saludo.")]),
        "callback", ["cerrar_llamada", "programar_llamada"],
        {"programar_llamada.no_antes_de": "2026-09-21T11:00:00+02:00"})

    # Visita reservada para dentro de 1 h: el margen de 2 h ya pasó → la tarea vence ya.
    add("s17-visita-inminente.json", _llamada("c_9017", "2026-09-18T16:00:00+02:00", turnos=[
        *SALUDO, ("agent", "¿Te va bien verlo hoy a las cinco?"), ("user", "Sí, perfecto."),
        ("agent", "Reservado. Hasta luego.")],
        cita={"appointment_id": "apt_9017", "start_time": "2026-09-18T17:00:00+02:00"}),
        "visita_reservada", ["cerrar_llamada", "crear_tarea"],
        {"crear_tarea.vence_el": "2026-09-18T16:00:00+02:00"})

    # Sin respuesta el sábado 13:00: +2 h = 15:00 fuera → lunes 10:00.
    add("s18-sin-respuesta-sabado.json", _sin_respuesta("c_9018", "2026-09-19T13:00:00+02:00"),
        "sin_respuesta", ["cerrar_llamada", "programar_llamada"],
        {"programar_llamada.no_antes_de": "2026-09-21T10:00:00+02:00"})

    # Documentación el viernes 18:00: lead +48 h naturales (domingo, no se ajusta a ventana);
    # comercial +3 días hábiles (el sábado no cuenta) → miércoles.
    add("s19-documentacion-viernes.json", _llamada("c_9019", "2026-09-18T18:00:00+02:00", turnos=[
        *SALUDO, ("user", "Necesito el certificado energético."),
        ("agent", "Te mando el enlace por WhatsApp a este número, ¿vale?"),
        ("user", "Sí, por WhatsApp perfecto."), ("agent", "Hecho, te lo acabo de enviar. Un saludo.")]),
        "documentacion_enviada", ["cerrar_llamada", "programar_recordatorio", "programar_recordatorio"],
        {"programar_recordatorio[0].cuando": "2026-09-20T18:00:00+02:00",
         "programar_recordatorio[1].cuando": "2026-09-23T18:00:00+02:00"})

    # Evento inválido (JSON roto): código != 0, sin línea de decisión; el lote sigue (R8).
    add("s20-invalido.json", '{"event_id": "evt_roto", "type": "call.ended", ', None, [], codigo=2)

    # Mensaje de otra organización → no_aplica, cero órdenes.
    add("s21-mensaje-otra-org.json", _mensaje("c_9019", "2026-09-18T19:00:00+02:00", "Hola",
        idem="wa-s21", org="org_demo_b"), "no_aplica", [])

    # Buzón por machine-unavailable (detector fiable), primer intento → reintento +2 h.
    add("s22-buzon-unavailable.json", _llamada("c_9022", "2026-09-18T10:00:00+02:00",
        amd="machine-unavailable", colgo="agent", desconexion="ROOM_DELETED",
        saludo="El número marcado no está disponible."),
        "buzon", ["cerrar_llamada", "programar_llamada"],
        {"programar_llamada.no_antes_de": "2026-09-18T12:00:00+02:00"})

    # Reentrega del callback de s16 → misma etiqueta, cero órdenes.
    reentrega = _llamada("c_9016", "2026-09-18T18:30:00+02:00", idem="lk-sint-uncertain", entrega=2,
                         amd="uncertain", turnos=[*SALUDO, ("user", "Llámame el lunes a las 11.")])
    add("s23-reentrega-callback.json", reentrega, "callback", [])
    return e


def escribir_lote_sintetico(carpeta: Path) -> dict:
    """Escribe los eventos y su orden.txt. Devuelve {fichero: esperado}."""
    carpeta.mkdir(parents=True, exist_ok=True)
    esperado, orden = {}, []
    for nombre, evento, expectativa in escenarios_sinteticos():
        contenido = evento if isinstance(evento, str) else json.dumps(evento, ensure_ascii=False, indent=2)
        (carpeta / nombre).write_text(contenido, encoding="utf-8")
        orden.append(nombre)
        esperado[nombre] = expectativa
    (carpeta / "orden.txt").write_text("\n".join(orden) + "\n", encoding="utf-8")
    return esperado
