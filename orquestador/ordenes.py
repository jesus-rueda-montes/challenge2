"""De `Accion` (qué pedir) a `Orden` (la línea exacta de ordenes.jsonl), con ids e idempotencia.

- idempotency_key = "<idempotency_key del evento>:<operacion>", más ":<distintivo>" solo si el
  evento emite más de una orden de esa operación (recomendación del OpenAPI).
- orden_id / reminder_id = prefijo + sha1(idempotency_key)[:8]. Mismo hecho → mismo id, en
  cualquier proceso. Es el mismo formato que el ejemplo resuelto (ord_96decc21).
- Una orden cuya idempotency_key ya está en la base no se vuelve a emitir (R5, segunda barrera:
  la primera es detectar la reentrega del evento entero).
"""
from __future__ import annotations

import hashlib
from collections import Counter

from .modelos import Evento, Orden
from .persistencia import Repositorio
from .reglas import Plan


def id_determinista(prefijo: str, clave: str) -> str:
    return f"{prefijo}_{hashlib.sha1(clave.encode('utf-8')).hexdigest()[:8]}"


def materializar(plan: Plan, evento: Evento, repo: Repositorio) -> list[Orden]:
    """Convierte el plan en órdenes y completa los efectos con los reminder_id generados."""
    repeticiones = Counter(accion.operacion for accion in plan.acciones)
    ordenes: list[Orden] = []
    for accion in plan.acciones:
        clave = f"{evento.idempotency_key}:{accion.operacion}"
        if repeticiones[accion.operacion] > 1 and accion.distintivo:
            clave += f":{accion.distintivo}"

        if repo.orden_existente(clave):
            plan.notas.append(f"R5: {clave} ya emitida, no se repite")
            continue

        if accion.recordatorio is not None:
            # El CRM respondería con un reminder_id; como no hay CRM, lo generamos nosotros y lo
            # persistimos para que un message.received de otro día pueda cancelarlo.
            plan.efectos.recordatorios_creados.append(
                {"reminder_id": id_determinista("rem", clave), **accion.recordatorio}
            )

        ordenes.append(Orden(
            orden_id=id_determinista("ord", clave),
            event_id=evento.event_id,
            operacion=accion.operacion,
            idempotency_key=clave,
            cuerpo=accion.cuerpo,
        ))
    return ordenes
