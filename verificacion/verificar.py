"""Verificación de extremo a extremo: ejecuta los lotes como en la evaluación y comprueba la salida.

    python verificacion/verificar.py

Para cada lote (oficial y sintético), desde cero y con un proceso por evento:
  1. Esquemas: cada decisión contra decision.schema.json y cada `cuerpo` contra el requestBody
     de su operación en crm-openapi.yaml.
  2. Invariantes: una decisión por evento recibido; decision.ordenes == órdenes de ese evento;
     orden_id e idempotency_key únicos; cerrar_llamada exactamente en cada call.ended propio
     procesado por primera vez; toda programar_llamada dentro de la ventana.
  3. Expectativas: etiqueta, operaciones y campos clave fijados a mano en escenarios.py.
  4. Idempotencia (R5): se reentrega el lote oficial entero sobre el mismo estado y no puede
     aparecer ni una orden nueva, y cada decisión repite su etiqueta.

Usa el clasificador que diga el entorno (MODO_CLASIFICADOR / OPENAI_API_KEY): sirve igual para
validar el modo simulado y el LLM.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import jsonschema
import yaml

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "verificacion"))

from escenarios import ESPERADO_OFICIAL, escribir_lote_sintetico  # noqa: E402
from orquestador import tiempo  # noqa: E402
from orquestador.config import cargar_config  # noqa: E402

CFG = cargar_config(RAIZ / "config" / "campana.yaml")
ESQUEMA_DECISION = json.loads((RAIZ / "esquemas" / "decision.schema.json").read_text(encoding="utf-8"))


def esquemas_operaciones() -> dict[str, dict]:
    api = yaml.safe_load((RAIZ / "esquemas" / "crm-openapi.yaml").read_text(encoding="utf-8"))
    return {
        op["operationId"]: op["requestBody"]["content"]["application/json"]["schema"]
        for ruta in api["paths"].values() for op in ruta.values()
    }


ESQUEMAS_OPS = esquemas_operaciones()


class Informe:
    def __init__(self, lote: str):
        self.lote, self.errores, self.comprobaciones = lote, [], 0

    def check(self, condicion: bool, mensaje: str) -> None:
        self.comprobaciones += 1
        if not condicion:
            self.errores.append(mensaje)


def leer_jsonl(ruta: Path) -> list[dict]:
    return [json.loads(l) for l in ruta.read_text(encoding="utf-8").splitlines() if l.strip()] if ruta.exists() else []


def ejecutar_lote(carpeta: Path, trabajo: Path, conservar: bool = False) -> int:
    entorno = {**os.environ, "KONTAKTU_SALIDA": str(trabajo / "salida"),
               "KONTAKTU_ESTADO": str(trabajo / "estado.sqlite3"), "KONTAKTU_TRAZA": "0"}
    args = [sys.executable, str(RAIZ / "procesar_lote.py"), str(carpeta)] + (["--conservar"] if conservar else [])
    return subprocess.run(args, env=entorno, capture_output=True, text=True, encoding="utf-8").returncode


def ficheros_en_orden(carpeta: Path) -> list[str]:
    return [l.strip() for l in (carpeta / "orden.txt").read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")]


def valor_campo(ordenes_evento: list[dict], ruta: str):
    """'programar_llamada.no_antes_de' o 'programar_recordatorio[1].cuando'."""
    operacion, campo = ruta.split(".", 1)
    indice = 0
    if "[" in operacion:
        operacion, indice = operacion[:-1].split("[")
        indice = int(indice)
    candidatas = [o for o in ordenes_evento if o["operacion"] == operacion]
    return candidatas[indice]["cuerpo"].get(campo) if len(candidatas) > indice else "<sin orden>"


def verificar_lote(nombre: str, carpeta: Path, esperado: dict, trabajo: Path) -> Informe:
    inf = Informe(nombre)
    shutil.rmtree(trabajo, ignore_errors=True)
    ejecutar_lote(carpeta, trabajo)
    decisiones = leer_jsonl(trabajo / "salida" / "decisiones.jsonl")
    ordenes = leer_jsonl(trabajo / "salida" / "ordenes.jsonl")

    # 1. Esquemas.
    for d in decisiones:
        try:
            jsonschema.validate(d, ESQUEMA_DECISION)
        except jsonschema.ValidationError as e:
            inf.check(False, f"decisión {d.get('event_id')} no cumple el esquema: {e.message}")
    for o in ordenes:
        inf.check(o["operacion"] in ESQUEMAS_OPS, f"operación desconocida {o['operacion']}")
        try:
            jsonschema.validate(o["cuerpo"], ESQUEMAS_OPS[o["operacion"]])
            inf.check(True, "")
        except jsonschema.ValidationError as e:
            inf.check(False, f"{o['orden_id']} {o['operacion']}: cuerpo inválido: {e.message}")
        if o["operacion"] == "programar_recordatorio":
            c = o["cuerpo"]
            inf.check(c["canal"] != "whatsapp_lead" or "plantilla" in c, f"{o['orden_id']}: falta plantilla")
            inf.check(c["canal"] != "tarea_comercial" or "tipo_tarea" in c, f"{o['orden_id']}: falta tipo_tarea")
        if o["operacion"] == "programar_llamada":
            cuando = datetime.fromisoformat(o["cuerpo"]["no_antes_de"])
            inf.check(tiempo.en_ventana(cuando, CFG), f"{o['orden_id']}: {cuando} fuera de la ventana (R3)")

    # 2. Invariantes.
    inf.check(len({o["orden_id"] for o in ordenes}) == len(ordenes), "orden_id repetido")
    inf.check(len({o["idempotency_key"] for o in ordenes}) == len(ordenes), "idempotency_key repetida (R5)")
    por_evento = defaultdict(list)
    for o in ordenes:
        por_evento[o["event_id"]].append(o)
    decision_por_evento = {d["event_id"]: d for d in decisiones}
    for d in decisiones:
        inf.check(d["ordenes"] == [o["orden_id"] for o in por_evento[d["event_id"]]],
                  f"{d['event_id']}: decision.ordenes no coincide con ordenes.jsonl")

    vistos: set[str] = set()
    for fichero in ficheros_en_orden(carpeta):
        texto = (carpeta / fichero).read_text(encoding="utf-8")
        expectativa = esperado[fichero]
        etiqueta_esp, ops_esp, campos_esp = expectativa[:3]
        codigo_esp = expectativa[3] if len(expectativa) > 3 else 0
        try:
            evento = json.loads(texto)
        except json.JSONDecodeError:
            inf.check(codigo_esp != 0, f"{fichero}: JSON inválido no esperado")
            continue
        d = decision_por_evento.get(evento["event_id"])
        inf.check(d is not None, f"{fichero}: no hay línea en decisiones.jsonl")
        if d is None:
            continue
        propias = por_evento[evento["event_id"]]
        primera_vez = evento["idempotency_key"] not in vistos
        vistos.add(evento["idempotency_key"])
        cierres = sum(o["operacion"] == "cerrar_llamada" for o in propias)
        debe_cerrar = evento["type"] == "call.ended" and evento["organization_id"] == CFG.organization_id and primera_vez
        inf.check(cierres == int(debe_cerrar), f"{fichero}: {cierres} cerrar_llamada, se esperaban {int(debe_cerrar)}")

        # 3. Expectativas.
        inf.check(d["etiqueta"] == etiqueta_esp, f"{fichero}: etiqueta {d['etiqueta']}, esperada {etiqueta_esp}")
        obtenidas = Counter(o["operacion"] for o in propias)
        inf.check(obtenidas == Counter(ops_esp), f"{fichero}: operaciones {dict(obtenidas)}, esperadas {dict(Counter(ops_esp))}")
        for ruta, valor in campos_esp.items():
            real = valor_campo(propias, ruta)
            inf.check(real == valor, f"{fichero}: {ruta} = {real}, esperado {valor}")
    return inf


def verificar_idempotencia(carpeta: Path, trabajo: Path) -> Informe:
    """Reentrega el lote completo sobre el estado ya existente (R5)."""
    inf = Informe("idempotencia (lote oficial reentregado)")
    lineas_antes = leer_jsonl(trabajo / "salida" / "ordenes.jsonl")
    decisiones_antes = leer_jsonl(trabajo / "salida" / "decisiones.jsonl")
    ejecutar_lote(carpeta, trabajo, conservar=True)
    lineas_despues = leer_jsonl(trabajo / "salida" / "ordenes.jsonl")
    nuevas = leer_jsonl(trabajo / "salida" / "decisiones.jsonl")[len(decisiones_antes):]
    inf.check(len(lineas_despues) == len(lineas_antes),
              f"la reentrega añadió {len(lineas_despues) - len(lineas_antes)} órdenes")
    inf.check(len(nuevas) == len(decisiones_antes), "la reentrega no dejó una decisión por evento")
    originales = {d["event_id"]: d["etiqueta"] for d in decisiones_antes}
    for d in nuevas:
        inf.check(d["ordenes"] == [], f"{d['event_id']}: la reentrega emitió órdenes")
        inf.check(d["etiqueta"] == originales.get(d["event_id"]),
                  f"{d['event_id']}: reentrega con etiqueta {d['etiqueta']} != {originales.get(d['event_id'])}")
    return inf


def main() -> int:
    trabajo = RAIZ / "verificacion" / ".ejecucion"
    sinteticos = RAIZ / "verificacion" / "eventos_sinteticos"
    esperado_sintetico = escribir_lote_sintetico(sinteticos)

    informes = [verificar_lote("lote oficial", RAIZ / "eventos", ESPERADO_OFICIAL, trabajo / "oficial")]
    informes.append(verificar_idempotencia(RAIZ / "eventos", trabajo / "oficial"))
    informes.append(verificar_lote("lote sintético", sinteticos, esperado_sintetico, trabajo / "sintetico"))

    total_errores = 0
    for inf in informes:
        estado = "OK " if not inf.errores else "MAL"
        print(f"[{estado}] {inf.lote}: {inf.comprobaciones} comprobaciones, {len(inf.errores)} fallos")
        for error in inf.errores:
            print(f"       - {error}")
        total_errores += len(inf.errores)
    return 1 if total_errores else 0


if __name__ == "__main__":
    for flujo in (sys.stdout, sys.stderr):
        flujo.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
