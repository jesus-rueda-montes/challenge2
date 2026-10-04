"""Aritmética de fechas en la zona de la campaña (R3).

Dos tipos de suma, a propósito distintas:
- `sumar_duracion`: horas/minutos *transcurridos*. Se hace en UTC para que un cambio de hora
  (DST) no altere la duración real ("48 horas naturales" son 48 h de reloj real).
- `sumar_dias_naturales` / `sumar_dias_habiles`: días de calendario, conservando la hora local
  de pared ("dentro de 2 días" a la misma hora aunque ese día dure 23 o 25 horas).

La ventana de llamadas es inclusiva en ambos extremos: 20:00 vale, 20:01 no.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .config import Config

_LIMITE_DIAS_BUSQUEDA = 14  # una semana entera sin ventana sería un error de configuración


def a_zona(dt: datetime, cfg: Config) -> datetime:
    return dt.astimezone(cfg.zona)


def sumar_duracion(dt: datetime, delta: timedelta, cfg: Config) -> datetime:
    return (dt.astimezone(timezone.utc) + delta).astimezone(cfg.zona)


def sumar_dias_naturales(dt: datetime, dias: int, cfg: Config) -> datetime:
    local = a_zona(dt, cfg)
    return datetime.combine(local.date() + timedelta(days=dias), local.timetz().replace(tzinfo=None), cfg.zona)


def sumar_dias_habiles(dt: datetime, dias: int, cfg: Config) -> datetime:
    """Avanza `dias` días hábiles conservando la hora local. Un sábado no cuenta como hábil."""
    local = a_zona(dt, cfg)
    actual = local.date()
    restantes = dias
    while restantes > 0:
        actual += timedelta(days=1)
        if actual.weekday() in cfg.dias_habiles:
            restantes -= 1
    return datetime.combine(actual, local.timetz().replace(tzinfo=None), cfg.zona)


def franja(dia: date, cfg: Config) -> tuple[datetime, datetime] | None:
    """Apertura y cierre de la ventana de llamadas de ese día, o None si no se llama."""
    horario = cfg.ventana[dia.weekday()]
    if horario is None:
        return None
    apertura, cierre = horario
    return datetime.combine(dia, apertura, cfg.zona), datetime.combine(dia, cierre, cfg.zona)


def en_ventana(dt: datetime, cfg: Config) -> bool:
    local = a_zona(dt, cfg)
    limites = franja(local.date(), cfg)
    return limites is not None and limites[0] <= local <= limites[1]


def siguiente_hueco_valido(dt: datetime, cfg: Config) -> datetime:
    """Primer instante >= dt que cae dentro de la ventana de llamadas."""
    local = a_zona(dt, cfg)
    for desplazamiento in range(_LIMITE_DIAS_BUSQUEDA):
        limites = franja(local.date() + timedelta(days=desplazamiento), cfg)
        if limites is None:
            continue
        apertura, cierre = limites
        if local <= cierre:
            return max(local, apertura)
    raise ValueError("la ventana de llamadas no tiene ninguna franja en las próximas dos semanas")


def hueco_mas_cercano(preferido: datetime, desde: datetime, hasta: datetime, cfg: Config) -> datetime | None:
    """Instante de [desde, hasta] dentro de la ventana más próximo a `preferido`.

    Devuelve None si ningún instante del rango cae en la ventana. En empate gana el más temprano.
    """
    preferido, desde, hasta = (a_zona(x, cfg) for x in (preferido, desde, hasta))
    mejor: datetime | None = None
    dia = desde.date()
    while dia <= hasta.date():
        limites = franja(dia, cfg)
        if limites is not None:
            inicio, fin = max(limites[0], desde), min(limites[1], hasta)
            if inicio <= fin:
                candidato = min(max(preferido, inicio), fin)
                if mejor is None or abs(candidato - preferido) < abs(mejor - preferido):
                    mejor = candidato
        dia += timedelta(days=1)
    return mejor


def formatear(dt: datetime, cfg: Config) -> str:
    """ISO 8601 con offset explícito, sin segundos fraccionarios."""
    return a_zona(dt, cfg).replace(microsecond=0).isoformat()
