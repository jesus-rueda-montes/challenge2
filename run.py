"""Punto de entrada: procesa UN evento.

    python run.py eventos/01-call-ended-nuria.json

Salida: append en salida/decisiones.jsonl y salida/ordenes.jsonl. Código 0 si se procesó el
evento, distinto de 0 si no se pudo (2 = entrada inválida, 1 = error interno). Un fallo no deja
estado a medias (la transacción se deshace), así que el siguiente evento se procesa normal (R8).

Variables de entorno (también desde .env):
    OPENAI_API_KEY        clave de OpenAI. Sin ella se usa el clasificador simulado.
    MODELO                modelo de OpenAI (por defecto gpt-4.1-mini).
    MODO_CLASIFICADOR     auto | llm | simulado (por defecto auto).
    KONTAKTU_SALIDA       directorio de salida (por defecto salida/).
    KONTAKTU_ESTADO       fichero SQLite (por defecto estado/kontaktu.sqlite3).
    KONTAKTU_CONFIG       configuración de campaña (por defecto config/campana.yaml).
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
MODELO_POR_DEFECTO = "gpt-4.1-mini"

EXIT_OK, EXIT_ERROR, EXIT_ENTRADA = 0, 1, 2


def crear_clasificador():
    from orquestador.clasificacion.simulado import ClasificadorSimulado

    modo = os.getenv("MODO_CLASIFICADOR", "auto").strip().lower()
    hay_clave = bool(os.getenv("OPENAI_API_KEY", "").strip())
    if modo == "simulado" or (modo == "auto" and not hay_clave):
        return ClasificadorSimulado()
    if not hay_clave:
        raise RuntimeError("MODO_CLASIFICADOR=llm pero falta OPENAI_API_KEY")
    from orquestador.clasificacion.conversacion import ClasificadorLLM

    return ClasificadorLLM(os.getenv("MODELO", "").strip() or MODELO_POR_DEFECTO)


def _registrar_error(directorio_salida: Path, ruta: str, error: BaseException) -> None:
    """Deja constancia del fallo fuera de los .jsonl (que solo llevan eventos procesados)."""
    directorio_salida.mkdir(parents=True, exist_ok=True)
    with (directorio_salida / "errores.log").open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat()} {ruta} {type(error).__name__}: {error}\n")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("uso: python run.py <ruta-del-evento.json>", file=sys.stderr)
        return EXIT_ENTRADA
    ruta = argv[1]

    from dotenv import load_dotenv

    load_dotenv(RAIZ / ".env")
    directorio_salida = Path(os.getenv("KONTAKTU_SALIDA", "salida"))

    import jsonschema
    from pydantic import ValidationError

    from orquestador.config import cargar_config
    from orquestador.grafo import Dependencias, construir_grafo
    from orquestador.persistencia import Repositorio
    from orquestador.salida import Salida

    repo = None
    try:
        repo = Repositorio(Path(os.getenv("KONTAKTU_ESTADO", "estado/kontaktu.sqlite3")))
        deps = Dependencias(
            cfg=cargar_config(Path(os.getenv("KONTAKTU_CONFIG", RAIZ / "config" / "campana.yaml"))),
            repo=repo,
            clasificador=crear_clasificador(),
            salida=Salida(directorio_salida),
            esquema_evento=json.loads((RAIZ / "esquemas" / "evento.schema.json").read_text(encoding="utf-8")),
        )
        estado = construir_grafo().invoke({"ruta_evento": ruta, "traza": []}, context=deps)
    except (FileNotFoundError, json.JSONDecodeError, jsonschema.ValidationError, ValidationError) as error:
        _registrar_error(directorio_salida, ruta, error)
        print(f"[{ruta}] evento inválido: {type(error).__name__}: {error}", file=sys.stderr)
        return EXIT_ENTRADA
    except Exception as error:  # noqa: BLE001 — R8: informar y salir con código != 0
        _registrar_error(directorio_salida, ruta, error)
        traceback.print_exc()
        return EXIT_ERROR
    finally:
        if repo is not None:
            repo.cerrar()

    d = estado["decision"]
    print(f"{d.event_id} → {d.etiqueta} ({d.confianza}) · {len(d.ordenes)} órdenes · {d.motivo}")
    if os.getenv("KONTAKTU_TRAZA", "1") != "0":
        for linea in estado["traza"]:
            print(f"    · {linea}")
    return EXIT_OK


if __name__ == "__main__":
    # La consola de Windows no siempre es UTF-8; que una tilde no tumbe el proceso.
    for flujo in (sys.stdout, sys.stderr):
        if hasattr(flujo, "reconfigure"):
            flujo.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv))
