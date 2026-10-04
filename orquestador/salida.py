"""Escritura en modo append de `salida/decisiones.jsonl` y `salida/ordenes.jsonl`."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .modelos import Decision, Orden


class Salida:
    def __init__(self, directorio: Path):
        directorio.mkdir(parents=True, exist_ok=True)
        self.decisiones = directorio / "decisiones.jsonl"
        self.ordenes = directorio / "ordenes.jsonl"

    def escribir(self, decision: Decision, ordenes: list[Orden]) -> None:
        # Primero las órdenes y luego la decisión que las referencia.
        self._append(self.ordenes, [o.model_dump(mode="json") for o in ordenes])
        self._append(self.decisiones, [decision.model_dump(mode="json")])

    @staticmethod
    def _append(ruta: Path, filas: list[dict]) -> None:
        if not filas:
            return
        with ruta.open("a", encoding="utf-8", newline="\n") as fichero:
            for fila in filas:
                fichero.write(json.dumps(fila, ensure_ascii=False) + "\n")
            fichero.flush()
            os.fsync(fichero.fileno())
