"""Reproduce la entrega real: un proceso nuevo por evento, en el orden de orden.txt.

    python procesar_lote.py                 # eventos/orden.txt, desde cero
    python procesar_lote.py --conservar     # sin borrar salida/ ni estado/
    python procesar_lote.py otra/carpeta    # otro lote con su propio orden.txt

Un evento que falla no detiene el lote (R8): se informa y se sigue con el siguiente.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    carpeta = Path(args[0]) if args else RAIZ / "eventos"
    salida = Path(os.getenv("KONTAKTU_SALIDA", "salida"))
    estado = Path(os.getenv("KONTAKTU_ESTADO", "estado/kontaktu.sqlite3"))

    if "--conservar" not in sys.argv:
        shutil.rmtree(salida, ignore_errors=True)
        estado.unlink(missing_ok=True)

    fallos = 0
    for linea in (carpeta / "orden.txt").read_text(encoding="utf-8").splitlines():
        nombre = linea.strip()
        if not nombre or nombre.startswith("#"):
            continue
        codigo = subprocess.call([sys.executable, str(RAIZ / "run.py"), str(carpeta / nombre)])
        if codigo != 0:
            fallos += 1
            print(f"!! {nombre} terminó con código {codigo}", file=sys.stderr)
    print(f"\nlote terminado: {fallos} fallos · salida en {salida}/")
    return 1 if fallos else 0


if __name__ == "__main__":
    for flujo in (sys.stdout, sys.stderr):
        if hasattr(flujo, "reconfigure"):
            flujo.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
