# Decisiones de diseño e interpretación

Cada entrada: **qué dice la fuente**, **dónde estaba la duda**, **qué decidí** y **por qué**.
Las marcadas (✔ acordada) se discutieron explícitamente durante el desarrollo.

## Clasificación

**D1 · Qué decide el modelo y qué no.** El enunciado pide cruzar transcripción y señalización
(R1). El modelo solo se invoca para llamadas contestadas por una persona (`200` + AMD `human`,
`uncertain` o `not_run`, con transcripción) y solo clasifica y extrae. SIP, AMD, la cita y todo
lo temporal es código. *Por qué:* determinismo donde es posible. Es más barato y más fácil de
auditar, y el ejemplo resuelto lo dice: llamar al modelo para un 486 es «gastar dinero».

**D2 · El evento manda sobre el modelo.** Con `agent_outcome.appointment` presente, la etiqueta
es `visita_reservada` aunque el modelo diga otra cosa, salvo `no_contactar`, porque «una baja
manda sobre cualquier otra etiqueta». Sin cita, el modelo no puede devolver `visita_reservada`:
se convierte en `visita_sin_confirmar` (N5). Si dice `documentacion_enviada` pero también que el
lead rechazó WhatsApp, pasa a `documentacion_pendiente`. *Por qué:* la existencia de la cita es
un hecho del CRM, no una interpretación.

**D3 · AMD.** `machine-vm`/`machine-unavailable` → `buzon`, con confianza 0.95 si viene de
`livekit_amd` y 0.7 si viene de `heuristic_regex` (caso 13: misma etiqueta, menos certeza).
`machine-ivr` y `5xx` → `otro`. `uncertain` → persona. Un `200` sin ningún turno del lead → `otro`.

**D4 · Fallo del modelo.** Si el modelo no responde o falla tras los reintentos del cliente, la
llamada se etiqueta `otro` con confianza 0.3, y N4 crea `revisar_llamada`. *Por qué:* R8 pide no
bloquear, y perder una llamada es peor que mandarla a un humano.

**D5 · Modo simulado (✔ acordada).** Sin clave de OpenAI se usa un clasificador por reglas de texto
con la misma interfaz y el mismo esquema de salida. Se elige solo (`MODO_CLASIFICADOR=auto`).

## Fechas y plazos (R3)

**D6 · Ocupado (✔ acordada).** El rango es [30, 90] min. No fijo +60 siempre: prefiero el punto
medio (coincide con el ejemplo, 10:31 → 11:31) y, si cae fuera de la ventana, el instante válido
del rango más cercano a él (ocupado a las 19:10 → 20:00). Si ningún instante del rango es válido,
uso el primer hueco a partir del mínimo (19:45 → sábado 10:00). La separación general de 2 h no
aplica: «gana el específico».

**D7 · Cortada fuera de ventana (✔ acordada).** «Lo antes posible»: el primer hueco válido desde
+30 min. Si eso supera las 4 h (p. ej. el sábado a las 13:50 → lunes 10:00), se acepta: no existe
un hueco mejor. Se aplica igual a `visita_sin_confirmar`.

**D8 · Callback.** El modelo devuelve la hora que pidió el lead, sin ajustar. El código la lleva a
la ventana. Si cambia (caso 12), se envía además `aviso_cambio_hora`. Horas sin franja: de 1 a 7
→ tarde («a las seis» = 18:00); de 8 a 12 → mañana. Callback sin hora concreta, o con una hora ya
pasada → separación mínima general.

**D9 · Recordatorios y tareas no son llamadas (✔ acordada).** No se ajustan a la ventana. El
recordatorio al lead se programa a +48 h **naturales** de reloj (en UTC, inmune al cambio de
hora); el del comercial, a +3 días **hábiles** a la misma hora local (el sábado no cuenta). Las
tareas vencen a +2 días naturales. `confirmar_visita_direccion` vence 2 h antes de la visita y,
si ese momento ya ha pasado, en el instante del evento.

**D10 · Ventana inclusiva.** 20:00 es válido y 20:01 no. Domingo, sin ventana.

## Intentos y canales

**D11 · Intento.** Todo `call.ended` propio procesado por primera vez cuenta, conteste alguien o
no. Las reentregas y los eventos de otra organización no cuentan. Los intentos se cuentan por
`contact_id`.

**D12 · N3 para cualquier reintento por voz (✔ acordada).** `casos.md` lo menciona en `buzon`,
pero la regla N3 es general. Con el intento actual ≥ `max_intentos`, ninguna etiqueta que
reintentaría por voz (sin_respuesta, ocupado, buzon, cortada, visita_sin_confirmar, callback)
programa otra llamada: se usa el canal de respaldo (`primer_toque_respaldo`).

**D13 · Callback con intentos agotados (✔ acordada: ceñirse a las instrucciones).**
`max_intentos` es «por lead» y N3 no hace excepciones, así que se aplica el respaldo y no se
programa la llamada. Es una interpretación: `casos.md` dice «llamada programada» para el
callback, pero no contempla este cruce.

**D14 · Rechazada.** Un 603 va directo al respaldo, sin reintento por voz, se hayan agotado o
no los intentos.

**D15 · N1 se recuerda.** Si el lead rechaza WhatsApp, queda persistido (`canales_rechazados`).
Después no recibe recordatorios ni avisos por WhatsApp, y tampoco el respaldo: N1 habla de
«documentación y respuestas», pero mandarle el respaldo por un canal que rechazó contradice su
intención. En ese caso no se emite nada (no hay otra operación de respaldo en el CRM).

**D16 · N2 hacia delante.** El evento `no_contactar` emite solo `marcar_no_contactar` (canal
`todos`) y `cerrar_llamada`. Si después llega otra llamada de ese lead, solo se emite
`cerrar_llamada`: se bloquean llamadas, WhatsApp, recordatorios y tareas. `cancelar_recordatorio`
no se bloquea, porque reduce el contacto. Los recordatorios pendientes de un lead que se da de
baja **no** se cancelan en ese evento, porque `casos.md` dice «ninguna otra orden».

**D17 · N4.** Cuentan como cortadas `cortada` y `visita_sin_confirmar`. A partir de la segunda con
el mismo lead → `revisar_llamada` (asignada a `cualquiera`), además del reintento.

## Eventos que no son casos

**D18 · Orden de enrutado.** Primero otra organización, luego reentrega y después el tipo de
evento. Un evento ajeno no se mira más, y una reentrega no se reclasifica.

**D19 · Reentrega.** Se repiten la etiqueta y la confianza originales, con un `motivo` que
explica la reentrega. Vale también para `message.received`.

**D20 · R7.** Al llegar un mensaje se cancelan los recordatorios del lead que estén programados,
no cancelados, con `cancelar_si = lead_responde` y con fecha posterior al mensaje (los ya
vencidos se dan por ejecutados).

## Identificadores

**D21 · Claves e ids.** La clave es `<idempotency_key del evento>:<operacion>`, con
`:<distintivo>` solo si hay varias órdenes de la misma operación en el evento (`:lead`,
`:comercial`, `:<reminder_id>`). `orden_id` = `ord_` + sha1(clave)[:8] y `reminder_id` = `rem_` +
sha1(clave)[:8]. Así se reproduce exactamente el ejemplo resuelto.
