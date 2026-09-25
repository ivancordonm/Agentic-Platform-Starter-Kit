# Ejemplo: triaje de soporte

Proyecto **aislado** de `project/`: no modifica el motor. Usa cinco agentes,
una bifurcación condicional (escalado o consulta de conocimiento), salidas Pydantic,
un MCP local de solo lectura y trazas de ejecución. Todos los tickets y artículos
son ficticios. No envía correos, crea tickets ni ejecuta acciones externas.

## Probar sin coste de modelo

Desde la raíz del repositorio:

```bash
uv sync --locked
PROJECT_DIR=examples/support_triage/project make validate
uv run pytest -q tests/test_support_triage.py
```

Las pruebas usan un runner falso para verificar las rutas; no demuestran calidad
del modelo. Para comprobar la conexión MCP real, inicia el servidor en otra
terminal y ejecuta el preflight:

```bash
uv run python -m examples.support_triage.mcp_server
uv run python scripts/support_triage_check.py
```

El MCP escucha en `127.0.0.1:8766/mcp`. El preflight consulta `KB-101`, comprueba
que una búsqueda desconocida no devuelve resultados y no usa OpenAI.

## Probar con el modelo (facturable)

Pon `OPENAI_API_KEY` en `.env` y arranca el MCP como arriba. En otra terminal:

```bash
PROJECT_DIR=examples/support_triage/project RUN_DB_PATH=.support-runs.sqlite3 make dev
```

Abre `http://127.0.0.1:8501`. En **Run & Debug**, prueba cada ticket con contexto
`{}`; después revisa **Runs & Traces**. Cada ejecución puede hacer varias llamadas
facturables al modelo. El modelo por defecto está en `project/models.yaml`;
cámbialo si tu cuenta no tiene acceso.

| Caso | Ticket de ejemplo | Ruta esperada | Resultado esperado |
| --- | --- | --- | --- |
| Crítico | «Desde las 09:00 nadie de nuestra empresa puede iniciar sesión; afecta a 200 usuarios.» | `classify → escalate → review` | P1, revisión humana, sin artículo ni diagnóstico confirmado |
| Documentado | «No encuentro la factura de agosto en la aplicación.» | `classify → lookup → compose → review` | Cita `KB-202`, solo pasos documentados |
| Sin artículo | «¿Cómo exporto los registros de auditoría en formato XML?» | `classify → lookup → compose → review` | Sin artículos inventados; pide revisión humana |

El enrutado y la salida **no están garantizados por las pruebas offline**: una
ejecución real también depende del modelo. Compara la clasificación, la secuencia
de nodos, `article_ids`, `needs_human` y la respuesta final. El log del MCP
prueba directamente que se invocó la herramienta; la traza de la plataforma
registra los nodos, no los argumentos internos de herramientas del SDK.

Para inspeccionar por API: `GET /runs`, `GET /runs/{run_id}` y
`GET /runs/{run_id}/events`. Al configurar `PLATFORM_API_TOKEN`, añade
`Authorization: Bearer <token>` o introdúcelo en la barra lateral de la UI.
No uses datos reales de clientes: las entradas y salidas quedan en las trazas.

## Cómo se adapta a otro proyecto

1. Copia `project/` a un directorio nuevo y define `project.yaml` y `models.yaml`.
2. Define contratos Pydantic en `schemas/` antes de escribir los prompts.
3. Declara los agentes y sus prompts en `agents.yaml`; adjunta solo las
   herramientas que necesita cada agente.
4. Declara herramientas con permisos mínimos en `tools.yaml`. Para MCP, limita
   `allowed_tools` y prueba la conexión por separado.
5. Compón el grafo en `workflow.yaml`. Las condiciones leen campos escalares de
   resultados estructurados; `output.select` elige la salida final.
6. Ejecuta `make validate`, tests con runner falso y, por último, una prueba real
   deliberada. Observa rutas, trazas, fallos y recargas antes de confiar en la UI.

Este ejemplo usa un revisor como último nodo, **no** implementa aprobación
humana ni crea tickets automáticamente. Si se quiere pasar a producción, harían
falta integración, autorización, tratamiento de datos y evaluación de calidad.
