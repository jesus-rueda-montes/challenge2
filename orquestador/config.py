"""Carga de `config/campana.yaml` en una estructura tipada e inmutable.

Todo lo que es política de la campaña (ventana, intentos, plazos, canal de respaldo) se lee de
aquí. Ningún número de negocio está escrito a mano en el resto del código.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

# Claves de día tal y como aparecen en campana.yaml, en el orden de datetime.weekday().
DIAS_SEMANA = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")


@dataclass(frozen=True)
class Config:
    organization_id: str
    system_key: str
    zona: ZoneInfo
    # weekday() -> (apertura, cierre) o None si ese día no se llama. Ambos extremos inclusive.
    ventana: dict[int, tuple[time, time] | None]
    max_intentos: int
    separacion_minima_horas: int
    ocupado_minutos_min: int
    ocupado_minutos_max: int
    cortada_minutos_min: int
    cortada_horas_max: int
    canal_respaldo: str
    dias_habiles: frozenset[int]
    documentacion_lead_horas: int
    seguimiento_comercial_dias_habiles: int
    confirmar_visita_margen_horas: int
    vencimiento_por_defecto_dias: int


def _hora(texto: str) -> time:
    horas, minutos = texto.split(":")
    return time(int(horas), int(minutos))


def cargar_config(ruta: Path) -> Config:
    datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    campana = datos["campana"]
    reintentos = datos["reintentos"]

    ventana: dict[int, tuple[time, time] | None] = {}
    for indice, dia in enumerate(DIAS_SEMANA):
        franja = datos["ventana_llamadas"].get(dia) or []
        ventana[indice] = (_hora(franja[0]), _hora(franja[1])) if franja else None

    return Config(
        organization_id=campana["organization_id"],
        system_key=campana["system_key"],
        zona=ZoneInfo(campana["zona_horaria"]),
        ventana=ventana,
        max_intentos=int(reintentos["max_intentos"]),
        separacion_minima_horas=int(reintentos["separacion_minima_horas"]),
        ocupado_minutos_min=int(reintentos["ocupado_minutos_min"]),
        ocupado_minutos_max=int(reintentos["ocupado_minutos_max"]),
        cortada_minutos_min=int(reintentos["cortada_minutos_min"]),
        cortada_horas_max=int(reintentos["cortada_horas_max"]),
        canal_respaldo=datos["canal_respaldo"],
        dias_habiles=frozenset(DIAS_SEMANA.index(d) for d in datos["dias_habiles"]),
        documentacion_lead_horas=int(datos["recordatorios"]["documentacion_lead_horas"]),
        seguimiento_comercial_dias_habiles=int(
            datos["recordatorios"]["seguimiento_comercial_dias_habiles"]
        ),
        confirmar_visita_margen_horas=int(datos["tareas"]["confirmar_visita_margen_horas"]),
        vencimiento_por_defecto_dias=int(datos["tareas"]["vencimiento_por_defecto_dias"]),
    )
