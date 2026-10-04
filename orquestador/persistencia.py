"""Estado que sobrevive entre procesos (R4, R5, R7): un fichero SQLite local.

Cada invocación de `run.py` es un proceso nuevo, así que todo lo que un evento futuro necesite
saber se guarda aquí:

- eventos         hechos ya procesados (por idempotency_key) y su decisión → reentregas (R5)
- entregas        cada event_id recibido, incluidas reentregas y rechazos (auditoría)
- intentos        un registro por call.ended propio procesado → recuento por lead (R4) y N4
- recordatorios   los reminder_id que generamos, para poder cancelarlos otro día (R7)
- no_contactar    bajas registradas (N2)
- canales_rechazados  p. ej. el lead dijo «por WhatsApp no» (N1)
- ordenes         toda orden emitida, con su idempotency_key UNIQUE: segunda barrera de R5

Lectura y escritura están separadas: los nodos del grafo solo leen (`contexto_lead`, `buscar_evento`)
y toda la escritura ocurre en `registrar_procesamiento`, dentro de una única transacción.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .modelos import ETIQUETAS_CORTADA, Decision, Orden

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS eventos (
    idempotency_key TEXT PRIMARY KEY,
    event_id        TEXT NOT NULL,
    tipo            TEXT NOT NULL,
    contact_id      TEXT NOT NULL,
    etiqueta        TEXT NOT NULL,
    motivo          TEXT NOT NULL,
    confianza       REAL NOT NULL,
    procesado_en    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entregas (
    event_id        TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    resultado       TEXT NOT NULL,   -- procesado | reentrega | otra_organizacion
    recibido_en     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS intentos (
    call_id     TEXT PRIMARY KEY,
    contact_id  TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    etiqueta    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_intentos_contacto ON intentos(contact_id);
CREATE TABLE IF NOT EXISTS recordatorios (
    reminder_id   TEXT PRIMARY KEY,
    contact_id    TEXT NOT NULL,
    canal         TEXT NOT NULL,
    cuando        TEXT NOT NULL,
    cancelar_si   TEXT NOT NULL,
    estado        TEXT NOT NULL,     -- scheduled | cancelled
    creado_por    TEXT NOT NULL,     -- event_id
    cancelado_por TEXT
);
CREATE INDEX IF NOT EXISTS ix_recordatorios_contacto ON recordatorios(contact_id);
CREATE TABLE IF NOT EXISTS no_contactar (
    contact_id TEXT NOT NULL,
    telefono   TEXT NOT NULL,
    canal      TEXT NOT NULL,
    motivo     TEXT NOT NULL,
    event_id   TEXT NOT NULL,
    PRIMARY KEY (contact_id, canal)
);
CREATE TABLE IF NOT EXISTS canales_rechazados (
    contact_id TEXT NOT NULL,
    canal      TEXT NOT NULL,
    event_id   TEXT NOT NULL,
    PRIMARY KEY (contact_id, canal)
);
CREATE TABLE IF NOT EXISTS ordenes (
    orden_id        TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    event_id        TEXT NOT NULL,
    operacion       TEXT NOT NULL,
    cuerpo          TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class RecordatorioPendiente:
    reminder_id: str
    canal: str
    cuando: str


@dataclass(frozen=True)
class ContextoLead:
    """Foto de lo que sabemos del lead *antes* de procesar este evento."""

    intentos_previos: int = 0
    cortadas_previas: int = 0
    dado_de_baja: bool = False
    whatsapp_rechazado: bool = False
    recordatorios_pendientes: tuple[RecordatorioPendiente, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class EventoPrevio:
    event_id: str
    etiqueta: str
    motivo: str
    confianza: float


@dataclass
class Efectos:
    """Cambios de estado que produce un evento, aplicados de golpe al final."""

    intento: tuple[str, str] | None = None             # (call_id, etiqueta)
    recordatorios_creados: list[dict] = field(default_factory=list)
    recordatorios_cancelados: list[str] = field(default_factory=list)
    baja: dict | None = None                           # {telefono, canal, motivo}
    canales_rechazados: list[str] = field(default_factory=list)


class Repositorio:
    def __init__(self, ruta: Path):
        ruta.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: controlamos las transacciones a mano con BEGIN IMMEDIATE.
        self._db = sqlite3.connect(ruta, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_ESQUEMA)

    def cerrar(self) -> None:
        self._db.close()

    # ------------------------------------------------------------------ lecturas

    def buscar_evento(self, idempotency_key: str) -> EventoPrevio | None:
        fila = self._db.execute(
            "SELECT event_id, etiqueta, motivo, confianza FROM eventos WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        return EventoPrevio(**dict(fila)) if fila else None

    def contexto_lead(self, contact_id: str, ahora: datetime) -> ContextoLead:
        q = self._db.execute
        etiquetas = [r["etiqueta"] for r in q("SELECT etiqueta FROM intentos WHERE contact_id = ?", (contact_id,))]
        baja = q("SELECT 1 FROM no_contactar WHERE contact_id = ? AND canal IN ('todos', 'voz')", (contact_id,)).fetchone()
        whatsapp = q(
            "SELECT 1 FROM canales_rechazados WHERE contact_id = ? AND canal = 'whatsapp' "
            "UNION SELECT 1 FROM no_contactar WHERE contact_id = ? AND canal = 'whatsapp'",
            (contact_id, contact_id),
        ).fetchone()
        # Pendiente = programado, no cancelado, cancelable si el lead responde y aún no vencido.
        pendientes = [
            RecordatorioPendiente(r["reminder_id"], r["canal"], r["cuando"])
            for r in q(
                "SELECT reminder_id, canal, cuando FROM recordatorios "
                "WHERE contact_id = ? AND estado = 'scheduled' AND cancelar_si = 'lead_responde' "
                "ORDER BY rowid",
                (contact_id,),
            )
            if datetime.fromisoformat(r["cuando"]) > ahora
        ]
        return ContextoLead(
            intentos_previos=len(etiquetas),
            cortadas_previas=sum(e in ETIQUETAS_CORTADA for e in etiquetas),
            dado_de_baja=baja is not None,
            whatsapp_rechazado=whatsapp is not None,
            recordatorios_pendientes=tuple(pendientes),
        )

    def orden_existente(self, idempotency_key: str) -> bool:
        return self._db.execute(
            "SELECT 1 FROM ordenes WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone() is not None

    # ------------------------------------------------------------------ escritura

    def registrar_procesamiento(
        self,
        *,
        evento_tipo: str,
        contact_id: str,
        idempotency_key: str,
        resultado: str,
        decision: Decision,
        ordenes: list[Orden],
        efectos: Efectos,
        occurred_at: str,
        emitir,
    ) -> None:
        """Aplica todo el estado del evento y llama a `emitir()` dentro de la misma transacción.

        `emitir` escribe los .jsonl. Si falla, se hace ROLLBACK y el evento queda como no
        procesado: una nueva entrega lo procesará limpio. Si el proceso muriera justo entre
        escribir los ficheros y el COMMIT, la reentrega duplicaría líneas; es la única ventana
        no atómica (fichero + SQLite no comparten transacción) y es de microsegundos.
        """
        db = self._db
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute(
                "INSERT INTO entregas VALUES (?, ?, ?, ?)",
                (decision.event_id, idempotency_key, resultado, occurred_at),
            )
            if resultado == "procesado":
                db.execute(
                    "INSERT INTO eventos VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (idempotency_key, decision.event_id, evento_tipo, contact_id, decision.etiqueta,
                     decision.motivo, decision.confianza, occurred_at),
                )
            for orden in ordenes:
                db.execute(
                    "INSERT INTO ordenes VALUES (?, ?, ?, ?, ?)",
                    (orden.orden_id, orden.idempotency_key, orden.event_id, orden.operacion,
                     json.dumps(orden.cuerpo, ensure_ascii=False)),
                )
            if efectos.intento:
                call_id, etiqueta = efectos.intento
                db.execute("INSERT INTO intentos VALUES (?, ?, ?, ?)", (call_id, contact_id, occurred_at, etiqueta))
            for r in efectos.recordatorios_creados:
                db.execute(
                    "INSERT INTO recordatorios VALUES (?, ?, ?, ?, ?, 'scheduled', ?, NULL)",
                    (r["reminder_id"], contact_id, r["canal"], r["cuando"], r["cancelar_si"], decision.event_id),
                )
            for reminder_id in efectos.recordatorios_cancelados:
                db.execute(
                    "UPDATE recordatorios SET estado = 'cancelled', cancelado_por = ? WHERE reminder_id = ?",
                    (decision.event_id, reminder_id),
                )
            if efectos.baja:
                db.execute(
                    "INSERT OR REPLACE INTO no_contactar VALUES (?, ?, ?, ?, ?)",
                    (contact_id, efectos.baja["telefono"], efectos.baja["canal"], efectos.baja["motivo"],
                     decision.event_id),
                )
            for canal in efectos.canales_rechazados:
                db.execute(
                    "INSERT OR IGNORE INTO canales_rechazados VALUES (?, ?, ?)",
                    (contact_id, canal, decision.event_id),
                )
            emitir()
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
