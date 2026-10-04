"""Clasificación determinista a partir de la señalización de telefonía y del AMD.

Cubre los casos 4, 5, 6, 11 y 13 de casos.md, más los que caen en `otro` (5xx, IVR). Si devuelve
None, la llamada la contestó una persona y hay que leer la conversación.

Llamar al modelo para algo que dice el código SIP sería gastar dinero y latencia, y además
introducir no determinismo donde no hace falta.
"""
from __future__ import annotations

from ..modelos import Clasificacion, Evento

_BUZON = {"machine-vm", "machine-unavailable"}


def _sip(evento: Evento) -> str:
    t = evento.telephony
    return f"{t.sip_status_code} {t.sip_status or ''}".strip()


def clasificar_por_senalizacion(evento: Evento) -> Clasificacion | None:
    t = evento.telephony
    codigo = t.sip_status_code

    def resultado(etiqueta: str, motivo: str, confianza: float) -> Clasificacion:
        return Clasificacion(etiqueta=etiqueta, motivo=motivo, confianza=confianza, fuente="senalizacion")

    # 1. Nadie descolgó: lo dice el código SIP.
    if codigo == 603:
        return resultado("rechazada", f"{_sip(evento)}: rechazo activo antes de descolgar", 0.97)
    if codigo == 486:
        # LiveKit lo reporta como USER_REJECTED, pero un 486 es línea ocupada, no un rechazo.
        return resultado("ocupado", f"{_sip(evento)}: la línea comunica", 0.97)
    if codigo in (408, 480):
        return resultado("sin_respuesta", f"{_sip(evento)}: nadie descolgó", 0.97)
    if 500 <= codigo < 600:
        return resultado("otro", f"{_sip(evento)}: fallo de trunk antes de conectar", 0.95)
    if codigo != 200:
        return resultado("otro", f"{_sip(evento)}: código SIP no contemplado en el catálogo", 0.7)

    # 2. Contestaron (200 OK). Un buzón también contesta con 200: la única señal es el AMD.
    amd = t.amd
    if amd.result in _BUZON:
        if amd.source == "livekit_amd":
            return resultado("buzon", f"AMD {amd.result} ({amd.source}): saltó el buzón de voz", 0.95)
        # Heurística sobre un saludo ambiguo (caso 13): la etiqueta es la misma, la certeza no.
        return resultado(
            "buzon",
            f"AMD {amd.result} por {amd.source} con saludo ambiguo: «{amd.greeting_transcript}»",
            0.7,
        )
    if amd.result == "machine-ivr":
        return resultado("otro", "AMD machine-ivr: contestó una centralita automática", 0.9)

    # 3. Persona (human), `uncertain` (se trata como persona) o AMD no ejecutado.
    if not any(turno.role == "user" for turno in evento.transcript):
        return resultado("otro", "descolgaron pero no hubo conversación que clasificar", 0.6)
    return None
