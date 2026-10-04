"""Clasificador de conversación SIN modelo: reglas sobre el texto.

Existe para poder ejecutar y verificar el sistema entero sin clave de OpenAI. Implementa la misma
interfaz y devuelve el mismo esquema que el LLM, así que el resto del grafo no sabe cuál se usó.

No pretende sustituir al modelo: es frágil ante paráfrasis («no me volváis a molestar»). Las
reglas siguen el mismo orden de prioridad que el prompt (la baja manda sobre todo).
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta

from ..config import DIAS_SEMANA, Config
from ..modelos import Evento
from .conversacion import SalidaClasificador


def _normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in sin_tildes if unicodedata.category(c) != "Mn")


_BAJA = re.compile(
    r"no me llam(eis|en|es) mas|no (me )?volvais a llamar|no me vuelvan a llamar|dadme de baja|"
    r"darme de baja|de baja|no quiero que me llam|borradme|quitadme de|no me contacte|dejad de llamar"
)
_EQUIVOCADO = re.compile(
    r"se ha equivocado|te has equivocado|numero equivocado|aqui no vive|no conozco a ningun|no es aqui"
)
_DESCARTADO = re.compile(
    r"ya (he |hemos )?(comprado|alquilado|encontrado|firmado)|ya no (lo )?(busco|estoy buscando|me interesa)|"
    r"ya tengo (piso|casa)"
)
_PIDE_DOCS = re.compile(r"documentacion|nota simple|certificado|enlace|planos|dossier")
_RECHAZA_WHATSAPP = re.compile(r"(por )?whatsapp no|no (uso|tengo) (el )?whatsapp|el whatsapp no")
_DOCS_ENVIADOS = re.compile(r"te lo (acabo de|he) envia|te la (acabo de|he) envia|ya te lo he mandado|enviado")
_PIDE_CALLBACK = re.compile(
    r"(me )?(puedes|podeis|puede) llamar|me llamas|me llamais|llamame|llamadme|llameme|vuelve a llamar|volved a llamar|"
    r"mejor (luego|mas tarde|manana)"
)
_DESPEDIDA = re.compile(r"adios|hasta luego|un saludo|gracias|buen dia|buenas tardes|hasta pronto|chao")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

_NUMEROS = {
    "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7,
    "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12,
}
_HORA = re.compile(
    r"a las (\d{1,2}|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce)"
    r"(?:[:.](\d{2}))?( y media| y cuarto)?( de la (manana|tarde|noche))?"
)


def _resolver_momento(texto: str, ahora: datetime) -> datetime | None:
    """Interpreta expresiones como «mañana a las seis» o «el jueves a las 11:30»."""
    t = _normalizar(texto)
    hora = _HORA.search(t)
    if not hora:
        return None
    h = int(hora.group(1)) if hora.group(1).isdigit() else _NUMEROS[hora.group(1)]
    minutos = int(hora.group(2) or 0) + (30 if hora.group(3) == " y media" else 15 if hora.group(3) else 0)
    franja = hora.group(5)
    if franja in ("tarde", "noche") and h < 12:
        h += 12
    elif franja is None and 1 <= h <= 7:
        h += 12  # «a las seis» en contexto comercial son las 18:00 (misma regla que el prompt)

    dia = ahora.date()
    sin_franjas = re.sub(r"(de|por) la manana", "", t)  # «la mañana» como franja, no como día
    if "pasado manana" in sin_franjas:
        dia += timedelta(days=2)
    elif re.search(r"\bmanana\b", sin_franjas):
        dia += timedelta(days=1)
    else:
        for indice, nombre in enumerate(DIAS_SEMANA):
            if re.search(rf"\b{nombre}\b", sin_franjas):
                dia += timedelta(days=(indice - ahora.weekday()) % 7 or 7)
                break
    return datetime(dia.year, dia.month, dia.day, h % 24, minutos % 60, tzinfo=ahora.tzinfo)


def _nota_contexto(evento: Evento) -> str:
    slots = evento.agent_outcome.slots_snapshot
    partes = [
        f"{clave}: {', '.join(valor) if isinstance(valor, list) else valor}"
        for clave, valor in slots.items()
        if valor not in (None, [], "")
    ]
    ultimo_lead = next((t.message for t in reversed(evento.transcript) if t.role == "user"), None)
    if ultimo_lead:
        partes.append(f"lo último que dijo el lead: «{ultimo_lead}»")
    return "; ".join(partes) or "sin datos recogidos"


class ClasificadorSimulado:
    nombre = "simulado (reglas, sin modelo)"
    fuente = "simulado"

    def clasificar(self, evento: Evento, cfg: Config) -> SalidaClasificador:
        ahora = evento.occurred_at.astimezone(cfg.zona)
        lead = _normalizar(" ".join(t.message for t in evento.transcript if t.role == "user"))
        agente = _normalizar(" ".join(t.message for t in evento.transcript if t.role == "agent"))
        ultimo = evento.transcript[-1] if evento.transcript else None
        slots = evento.agent_outcome.slots_snapshot
        texto_lead_original = " ".join(t.message for t in evento.transcript if t.role == "user")
        email = (m.group(0) if (m := _EMAIL.search(texto_lead_original)) else None) or slots.get("email_declarado")
        whatsapp_rechazado = bool(_RECHAZA_WHATSAPP.search(lead))

        def salida(etiqueta: str, motivo: str, confianza: float, **extra) -> SalidaClasificador:
            return SalidaClasificador(
                razonamiento="clasificación por reglas de texto (modo simulado)",
                etiqueta=etiqueta,
                motivo=motivo,
                confianza=confianza,
                callback_solicitado=extra.get("callback_solicitado"),
                callback_texto=extra.get("callback_texto"),
                whatsapp_rechazado=whatsapp_rechazado,
                email=email,
                nota_contexto=_nota_contexto(evento),
            )

        if _BAJA.search(lead):
            return salida("no_contactar", "el lead pide explícitamente que no se le llame más", 0.8)
        if _EQUIVOCADO.search(lead):
            return salida("persona_equivocada", "quien contesta no es el lead: número equivocado", 0.8)
        if _DESCARTADO.search(lead):
            return salida("descartado", "el lead ya no busca inmueble", 0.7)
        if evento.agent_outcome.appointment:
            return salida("visita_reservada", "el agente creó la cita durante la llamada", 0.9)
        if _PIDE_DOCS.search(lead):
            if whatsapp_rechazado:
                return salida("documentacion_pendiente", "pide documentación pero rechaza WhatsApp", 0.75)
            if _DOCS_ENVIADOS.search(agente) and "whatsapp" in lead + agente:
                return salida("documentacion_enviada", "se le envió la documentación por WhatsApp durante la llamada", 0.75)
        if _PIDE_CALLBACK.search(lead):
            texto_momento = slots.get("callback_when_raw") or texto_lead_original
            momento = _resolver_momento(texto_momento, ahora)
            cuando = slots.get("callback_when_raw") or (momento.strftime("%d/%m %H:%M") if momento else "sin hora concreta")
            return salida(
                "callback",
                f"el lead pide que se le llame en otro momento ({cuando})",
                0.75,
                callback_solicitado=momento.strftime("%Y-%m-%dT%H:%M") if momento else None,
                callback_texto=slots.get("callback_when_raw"),
            )
        sin_despedida = ultimo is not None and (
            ultimo.message.rstrip().endswith(("…", "...")) or not _DESPEDIDA.search(_normalizar(ultimo.message))
        )
        if sin_despedida:
            if slots.get("visita_acordada_verbal"):
                return salida("visita_sin_confirmar", "se acordó visita de palabra y la llamada cayó antes de crearla", 0.7)
            return salida("cortada", "la llamada se corta a mitad de la conversación, sin despedida", 0.65)
        return salida("otro", "la conversación no encaja con claridad en ningún caso del catálogo", 0.4)
