# Prompts dados al asistente de programación

Asistente: **Claude Code** (Claude Opus). Se usó para analizar el enunciado, proponer un plan y
escribir el código bajo revisión. El modelo que usa *el sistema* en ejecución es de OpenAI (ver
README). Aquí están los mensajes tal y como los escribí, en orden. La conversación completa exportada
(respuestas y herramientas incluidas) está en [PromptsClaudeCode/](../PromptsClaudeCode/conversacion.md).

---

**1. Análisis y plan**

> Analiza el enunciado.md y define un plan para resolver el ejercicio en los pasos que necesites.
> Si tienes alguna duda no dudes en consultarme. Todavía no desarrolles nada. Solo define los pasos

*Respuesta resumida:* un plan en 10 pasos (esqueleto, config y modelos, persistencia SQLite,
utilidades de tiempo, grafo LangGraph, clasificador por señalización, clasificador LLM, reglas de
negocio, CLI, verificación y documentación), una tabla con la salida esperada de los 16 eventos y
6 dudas de interpretación.

**2. Duda sobre el asistente**

> duda: la ia que use debe ser cualquier modelo de openai. eso significa que no puedo usar
> claude code? que debo usar codex?

*Respuesta resumida:* la restricción se refiere al modelo que llama el programa en ejecución, no a
la herramienta de desarrollo. El enunciado incluso pide los prompts dados al asistente.

**3. Respuestas a las dudas del plan**

> 1. […] Uno barato ya que, si no me equivoco, solo se va a usar para simular las decisiones del programa no?
> 2. Reintento de ocupado: […] Si fijas punto medio en 60 pero luego cae fuera de la ventana horaria y
>    otros números dentro del rango 30-90 entran es mejor no usar siempre 60. Razonalo cada vez.
> 3. Intentos agotados en sin_respuesta, ocupado y cortada: […] Si.
> 4. Cortada fuera de la ventana: […] Efectivamente.
> 5. Hora de los recordatorios: […] De acuerdo.
> 6. Callback de un lead con los intentos agotados: […] Aquí diria que te ciñeses a las instrucciones
> Luego tocará documentar las decisiones tomadas y el por qué.

*Resultado:* decisiones D5, D6, D7, D9, D12 y D13 de [DECISIONES.md](../DECISIONES.md).

**4. Desarrollo**

> 1. No tengo api de openai osea que prepara un modo de prueba sin modelo.
> Además, acuerdate de ir haciendo periodicamente los commits a github. Adelante

*Resultado:* el clasificador simulado
([simulado.py](../orquestador/clasificacion/simulado.py)), que se selecciona solo cuando no hay
clave, y el desarrollo por pasos con un commit por bloque.

**5. Comprobación final**

> realiza una ultima comprobación de que todo está bien y se cumplen las especificaciones del enunciado

*Resultado:* auditoría requisito a requisito, clon limpio con instalación desde
`requirements.txt` y verificación completa. Se corrigieron dos riesgos de la ruta LLM (esquema
estricto sin `minimum`/`maximum`; sin `temperature` en modelos de razonamiento) y la confianza
de `cerrar_llamada` se alineó con la de la decisión.
