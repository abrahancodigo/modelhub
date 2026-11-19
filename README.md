# ModelHub

Menú de terminal en español para guardar APIs y elegir con qué agente usarlas.
Python 3.9+; sin paquetes adicionales. Interfaz con flechas en Linux/macOS/WSL
mediante `curses` y modo de texto para terminales que no lo soportan.

```bash
python3 /root/modelhub/modelhub.py
# Si está instalado el enlace en PATH:
modelhub
```

1. Sigue la bienvenida o entra en **Mis APIs y modelos → Agregar API**.
2. Elige el formato `openai` (Chat Completions) o `anthropic` (Messages).
3. Introduce la base URL y API key; la clave se escribe oculta.
4. Pulsa Enter en **Modelo** para consultar `/models`, filtrar y elegir.
   Si el proveedor no publica modelos, introduce el ID manualmente.
5. Selecciona **Empezar a trabajar**, tu perfil y un agente compatible.

## Interfaz amigable

```text
MODELHUB / Tus APIs, tus agentes

› Empezar a trabajar
  Volver a usar pi · trabajo · coder
  ★ Qwen rápido · qwen · Ligero · coder
  Favoritos
  Mis APIs y modelos
  Explorar e instalar agentes
  Ajustes
  Salir

↑↓ Mover · Enter Elegir · Esc Volver · Escribe para buscar
```

- **Flechas y Enter:** selección sin números; Esc retrocede un paso en los
  asistentes y vuelve a la pantalla anterior en los submenús. En el inicio sale.
  PgUp/PgDn desplazan listas largas.
- **Empezar a trabajar:** un único recorrido API → agente → modo → resumen → abrir.
  En Qwen, **Ligero** significa menos contexto para sesiones sencillas;
  **Completo** conserva las funciones y personalizaciones habituales.
- **Carpeta de trabajo:** en el resumen final elige **Elegir carpeta de trabajo**
  y navega con ↑↓/Enter, ← para subir y escritura para filtrar. La ruta elegida
  aparece en el resumen, se usa para abrir el agente y se guarda con el favorito.
  Claude Code la usa como proyecto en lugar de arrancar en `$HOME`.
- **Resumen persistente:** la cabecera muestra API, agente, modo y modelo mientras
  configuras la sesión. Las claves nunca forman parte del resumen.
- **API y base URL visibles:** las listas de perfiles muestran nombre, formato
  (`openai`/`anthropic`), modelo y base URL; las tarjetas añaden si hay clave
  guardada y el último estado de conexión. Las claves nunca se muestran.
- **Tarjetas de agentes:** al seleccionar uno, un panel muestra nombre, estado de
  instalación, descripción, protocolos y API/modelo seleccionados. En terminales
  pequeñas se priorizan la navegación y el resumen; el modo texto muestra los detalles.
- **Colores discretos:** verde para resultados correctos, amarillo para pendientes
  y rojo para errores, acompañados siempre de texto. Se desactivan con `NO_COLOR=1`
  o cuando el terminal no admite color.
- **Búsqueda en vivo:** escribe en cualquier lista, incluidos los modelos.
  Retroceso elimina letras; Ctrl+U limpia el filtro.
- **Última selección:** aparece al iniciar un agente. Reutiliza agente, API y
  modelo desde el directorio actual; no reanuda automáticamente una conversación.
- **Favoritos con nombre:** guarda combinaciones de agente, perfil, modelo y modo
  con nombres como «Qwen rápido». Aparecen directamente en el inicio y se pueden
  renombrar desde Favoritos. También puedes guardarlos desde el resumen antes de
  abrir un agente. Las claves se consultan del perfil al abrirlo.
  Los favoritos antiguos sin nombre siguen funcionando.
- **Retroceder sin perder datos:** el formulario de API conserva nombre, protocolo,
  URL, clave y modelo al volver atrás, incluso el texto que estabas escribiendo.
  Las claves conservadas permanecen ocultas. El borrador vive en memoria y solo
  se guarda al terminar el formulario; salir desde el primer paso lo descarta.
  El recorrido de inicio conserva API, agente, modo y modelo al retroceder.
- **Bienvenida:** guía inicial; puede repetirse desde Ajustes.
- **Siguiente paso visible:** al guardar una API o cambiar de modelo puedes
  elegir **Elegir agente y comenzar** o **Probar conexión**. El inicio guía
  API → agente → resumen; conserva el perfil al pasar al catálogo de instalación.
  El resumen permite probar la API o cambiar de modelo antes de abrir el agente.
  Las pantallas muestran el contexto en varias líneas y avisan cuándo entras al
  agente y cuándo regresas a ModelHub.
- **Modo de texto:** `modelhub --plain` o `MODELHUB_PLAIN=1 modelhub`. También se
  puede guardar esta preferencia en Ajustes. Se usa automáticamente sin TTY.
- **Espera configurable:** `MODELHUB_TIMEOUT=segundos` ajusta el límite de tiempo
  de las peticiones HTTP (15 por defecto, valores entre 1 y 300).
- **Errores legibles:** mensajes para credenciales rechazadas, límites de API,
  problemas de red, agentes ausentes y requisitos de instalación.

## Explorar e instalar agentes

El catálogo incluye **OpenCode, Pi, Qwen Code, Cline, Claude Code, Codex y
Kimi Code**, con descripciones, protocolos y estado de instalación. Los
adaptadores personalizados también aparecen; su instalación es manual.

1. Abre **Explorar e instalar agentes** y elige uno.
2. ModelHub comprueba npm y la versión mínima de Node.js (Pi y Kimi: 22.19+,
   Qwen y Claude: 22+, Cline, OpenCode y Codex: 20+).
3. Muestra la fuente oficial y el comando completo. La confirmación empieza en
   **Cancelar**; selecciona **Sí, continuar** para instalar.
4. npm muestra su salida de progreso. Al terminar, ModelHub verifica que apareció
   el ejecutable y ofrece **Configurar con mi API y abrir**.

Se utilizan paquetes npm oficiales con `--global --prefix <MODELHUB_HOME>/tools`,
sin `sudo`. ModelHub detecta `tools/bin` automáticamente y lo añade al PATH de los
agentes que inicia. Tu PATH de shell no se modifica. Las instalaciones existentes
en PATH tienen prioridad y no se reinstalan. Si falta Node.js/npm, se indica el
requisito y el enlace de instalación; no se instala Node automáticamente.
La instalación integrada está disponible en Linux, macOS y WSL.

```bash
modelhub explore
modelhub install pi
modelhub install qwen
```

## Probar una API

En **Mis APIs y modelos → tu perfil → Probar conexión** puedes elegir:

- **Consultar catálogo:** GET `/models`, sin inferencia. Indica si el modelo
  aparece en la lista. ModelHub intenta las rutas habituales del proveedor,
  admite varios formatos de respuesta y sigue la paginación OpenAI/Anthropic;
  que una ruta devuelva 401/403 no impide probar las otras.
- **Probar respuesta del modelo:** envía «Reply OK.» con hasta 16 tokens de
  salida, previa confirmación porque puede tener coste. Usa Chat Completions o
  Anthropic Messages según el perfil. No verifica el uso de herramientas.

- **Comprobar todas:** desde **Mis APIs y modelos → Comprobar todas las APIs**
  se consulta el catálogo de todos los perfiles seguidos, sin inferencia.

El estado y la fecha de la última prueba se muestran junto a cada API.
Cambiar URL, clave o modelo invalida ese estado. No se hacen comprobaciones de
red automáticas al abrir el menú. Un proveedor sin `/models` puede funcionar con
un ID escrito manualmente y la prueba de respuesta.

```bash
modelhub check trabajo
modelhub check trabajo --inference
```

## Qwen: primera respuesta lenta

Un saludo en un agente también envía su prompt del sistema y las definiciones de
herramientas. En la sesión diagnosticada, la primera petición llevaba 16.997 tokens
y tardó 115 segundos hasta el primer token; la segunda reutilizó 16.640 tokens en
caché y empezó a responder en 3,2 segundos. No se registraron tokens de razonamiento.
Estos tiempos describen esas peticiones, no una latencia garantizada del proveedor.

ModelHub permite ahora 5 minutos de espera por petición antes de recibir la
respuesta HTTP, en lugar del límite fijo anterior de 60 segundos. Puedes ajustar
ese tiempo con la variable nativa de Qwen (milisegundos):

```bash
QWEN_CODE_API_TIMEOUT_MS=600000 modelhub run qwen trabajo
```

El ajuste se aplica al proceso hijo; no impone un límite a la duración de la
sesión ni de sus herramientas. Si has configurado `generationConfig.timeout`
explícitamente en el proveedor de Qwen, ese valor tiene prioridad sobre la
variable de entorno. ModelHub conserva ahora los ajustes de generación del
mismo modelo y URL, incluido `contextWindowSize`, y migra automáticamente la
configuración antigua exacta de 60 segundos con un reintento al volver a abrirlo.

Un comando de terminal y una respuesta del modelo tienen límites distintos.
Qwen usa por defecto 2 minutos para un comando en primer plano; para comandos
largos puedes ajustar `tools.shell.defaultTimeoutMs` en
`agents/<perfil>/qwen/settings.json` (hasta `600000`, o `0` sin límite).
Los límites de streaming se controlan por separado con
`QWEN_STREAM_IDLE_TIMEOUT_MS` y `QWEN_STREAM_MAX_LIFETIME_MS`; ModelHub respeta
esas variables. Consulta la [configuración oficial de Qwen](https://qwenlm.github.io/qwen-code-docs/en/users/configuration/settings/).

Para reducir el contexto, selecciona **Empezar a trabajar → tu API → Qwen →
Ligero → Abrir agente ahora**, o ejecuta:

```bash
modelhub run qwen trabajo --light
```

Este modo usa un prompt de programación corto, `--safe-mode` y
`--approval-mode default`. Omite contexto personalizado, skills, hooks,
extensiones y MCP; mantiene las herramientas de programación de Qwen y las
credenciales/modelo elegidos. Es una alternativa explícita para sesiones sencillas;
usa el modo normal cuando necesites las instrucciones y personalizaciones completas.
**Volver a usar** recuerda el modo ligero. Las opciones adicionales después de
`--` se pasan al CLI y pueden sobrescribir sus parámetros.

Una prueba real del modo ligero recibió el primer token en 2,4 segundos con 5.246
tokens de entrada y sin caché. El arranque completo más la petición tardó 17 segundos.
La carga del proveedor, el contexto y las herramientas usadas pueden cambiar estos tiempos.

## Nuevos agentes: Codex y Kimi Code

ModelHub prepara la configuración de cada uno en un directorio propio bajo
`agents/<perfil>`, sin tocar tu configuración personal:

- **Codex** (`@openai/codex`): escribe `config.toml` con un proveedor
  `modelhub` y `wire_api = "responses"`. Solo funciona con perfiles cuyo
  endpoint expone la **API Responses** (`/responses`); no vale un endpoint de
  Chat Completions puro. Verifícalo con **Probar conexión** antes de abrir.
- **Kimi Code** (`@moonshot-ai/kimi-code`): escribe `config.toml` con el
  proveedor `modelhub`, `api_key_env = "MODELHUB_API_KEY"` y `default_model`
  apuntando al modelo del perfil. Acepta perfiles `openai` (tipo `openai`) y
  `anthropic`; con Anthropic se elimina el sufijo `/v1` de la URL porque el
  cliente añade `/v1/messages`. Requiere Node.js 22.19+.

```bash
modelhub install codex
modelhub install kimi
```

## Detección automática de agentes

ModelHub comprueba PATH y su directorio `tools/bin` cada vez que abres el selector
o ejecutas `modelhub agents`.
Si instalas **OpenCode, Cline, Claude Code, Qwen Code (`qwen`), Pi (`pi`),
Codex (`codex`) o Kimi Code (`kimi`)**, aparece
automáticamente cuando su ejecutable está en PATH. No necesitas reiniciar ModelHub.
Si el instalador cambia PATH, abre ModelHub desde una terminal que tenga ese cambio.
Al elegirlo, ModelHub prepara la configuración con tu perfil y abre el agente.
La detección comprueba la presencia del ejecutable, no su versión ni la API real.

```bash
modelhub agents
modelhub run qwen trabajo
modelhub run pi trabajo
modelhub register-agent
```

Para un CLI distinto, registra una vez su formato con **Registrar agente**.
El asistente pide el ejecutable y los nombres de las variables de entorno para
API key, base URL y modelo, según la documentación de ese CLI. Desde entonces,
su instalación y desinstalación se detectan igual que las de los integrados.
No existe un estándar que permita deducir la configuración de cualquier programa
desconocido solo por estar instalado.

### Adaptadores adicionales sin modificar código

ModelHub carga automáticamente `~/.config/modelhub/adapters/*.json` (o
`$MODELHUB_HOME/adapters/*.json`). El nombre del archivo es el ID del agente.
Ejemplo `mi-agente.json`, adaptando los nombres a los que acepte ese CLI:

```json
{
  "command": "mi-agente",
  "protocols": ["openai"],
  "env": {
    "OPENAI_API_KEY": "{{api_key}}",
    "OPENAI_BASE_URL": "{{base_url}}"
  },
  "args": ["--model", "{{model}}"]
}
```

`command` es un nombre en PATH o una ruta absoluta. `args` es una lista, no un
comando de shell. Las variables disponibles son `{{api_key}}`, `{{base_url}}`,
`{{model}}`, `{{protocol}}`, `{{profile}}` y `{{runtime}}`.
Por seguridad, el entorno del adaptador no puede redefinir `PATH`, `HOME`, `PWD`
ni ninguna variable `MODELHUB_*`; esos nombres se rechazan en el registro.
Para CLIs que necesitan JSON, añade `"files": {"settings.json": {"model": "{{model}}"}}`
y pasa `"{{runtime}}/settings.json"` mediante el argumento o variable que usen.
Los archivos se generan dentro del directorio propio del adaptador con permisos
`0600`. Los nombres de archivo y las claves JSON son literales; las sustituciones
se aplican a valores. Cada arranque usa los valores actuales del perfil.
Los adaptadores inválidos se omiten con un mensaje; no reemplazan integraciones
incluidas. Puedes actualizar su JSON sin reiniciar ModelHub.

Puedes guardar varios perfiles del mismo proveedor con modelos o claves diferentes.
Las consultas de modelos no validan que el modelo soporte herramientas; para un
agente de programación necesitas un modelo con esa capacidad. El descubrimiento
sigue la paginación que publica la API; si el catálogo no está disponible, el
ID del modelo se puede escribir a mano.

## Comandos

```bash
modelhub --version
modelhub add trabajo
modelhub list
modelhub edit trabajo
modelhub models trabajo
modelhub run opencode trabajo
modelhub run cline trabajo
modelhub run claude trabajo
modelhub run claude trabajo --dir /ruta/mi-proyecto
modelhub run claude trabajo --dir /ruta/mi-proyecto -- --continue
modelhub run claude trabajo --dir /ruta/proyecto-a --dir /ruta/proyecto-b
modelhub remove trabajo
modelhub doctor
modelhub favorites
modelhub last
```

Ejecuta ModelHub desde el directorio del proyecto en el que quieres trabajar.
Los agentes arrancan en la carpeta actual; puedes elegir otra con `--dir`.
Para Claude Code puedes repetir `--dir` o separar varias rutas con `:`: la
primera es el directorio de trabajo y las demás se habilitan con `--add-dir`.
Si abres Claude Code desde tu carpeta de inicio ($HOME), ModelHub te pide una o
más carpetas de proyecto; no permite abrir todo $HOME porque ahí se guardan
claves y configuración. Los agentes deben estar instalados en
PATH o mediante el catálogo; `doctor` comprueba esto.

## Compatibilidad

- **OpenCode V2:** crea configuración nativa `providers` en un directorio XDG
  del perfil y abre un servidor privado (`--standalone`) que recibe la clave.
  Las preferencias globales de OpenCode quedan separadas; las configuraciones del
  proyecto o del administrador pueden prevalecer sobre el modelo del perfil.
- **Cline CLI actual:** usa `cline auth --provider --apikey --baseurl --modelid`,
  configura un directorio por perfil y lanza su TUI en modo backend local.
  La interfaz oficial de autenticación pasa la clave como argumento durante
  ese breve proceso; ModelHub no imprime su salida ni el comando con la clave.
  La preparación tiene un límite de 60 segundos y muestra un error si no termina;
  este límite no se aplica a la sesión de trabajo posterior.
  En Cline CLI 3.0.73, `run_commands` tiene además un límite interno de
  30 segundos que la CLI no permite configurar; `--timeout` limita la tarea
  completa y no amplía ese límite de herramienta. Es una
  [limitación reportada en Cline](https://github.com/cline/cline/issues/10549).
- **Claude Code:** usa `--settings`, `--model` y variables Anthropic con
  autenticación `x-api-key`. Con un perfil **Anthropic Messages** se conecta
  directamente. Para una API solo OpenAI, ModelHub levanta un puente local en
  `127.0.0.1` (puerto libre) que traduce Anthropic Messages ↔ OpenAI Chat
  Completions, incluidos el streaming SSE y el uso de herramientas; la clave
  real permanece en el puente y Claude Code recibe solo un token local efímero.
  El puente vive únicamente lo que dura la sesión. La compatibilidad con modelos
  no Claude depende del proveedor y no está garantizada por Anthropic.
  Arranca en la carpeta de proyecto elegida (`--dir` o la actual); nunca en
  $HOME, y las rutas extra de `--dir` (separadas por `:`) se abren con
  `--add-dir` para acceder a más proyectos sin cambiar el directorio de
  trabajo.
- **Qwen Code:** perfiles OpenAI compatibles mediante `OPENAI_API_KEY`,
  `OPENAI_BASE_URL`, `OPENAI_MODEL` y argumentos `--auth-type openai`,
  `--openai-base-url`, `--model`. Ahora registra el proveedor y modelo en
  `agents/<perfil>/qwen/settings.json`, selecciona Chat Completions explícitamente
  y usa `QWEN_HOME`/`QWEN_RUNTIME_DIR` por perfil. El modelo queda disponible en
  `/model`; su `envKey` referencia `MODELHUB_API_KEY` sin escribir la clave allí.
  Los modelos generados antiguos se reemplazan al cambiar de API/modelo; las otras
  preferencias del directorio del perfil se conservan, incluidos los ajustes
  de generación del mismo modelo y URL. La espera por petición es de 5 minutos
  por defecto, configurable con `QWEN_CODE_API_TIMEOUT_MS`, y los reintentos
  quedan a cargo del cliente. Las sesiones anteriores al cambio
  siguen en `~/.qwen`; las nuevas sesiones de ModelHub quedan en el perfil.
  Si Qwen se queda esperando, **Empezar a trabajar → tu API → Qwen → Completo →
  Abrir Qwen en modo diagnóstico** permite probar `--safe-mode` con la misma API.
  Este modo desactiva temporalmente contexto, extensiones, skills, hooks y MCP.
- **Pi:** genera `models.json` con un proveedor `modelhub`, configura
  `PI_CODING_AGENT_DIR` por perfil y lanza `--provider modelhub --model ...`.
  Admite `openai-completions` y `anthropic-messages`; la clave se resuelve desde
  `MODELHUB_API_KEY` en el entorno del proceso.

OpenAI: normalmente `https://proveedor.example/v1` (usa la ruta de tu proveedor).
Anthropic: `https://proveedor.example`; se acepta también el sufijo `/v1`.
Las APIs que requieren OAuth, Azure u otros encabezados personalizados necesitan
una integración adicional. Actualmente se admiten Bearer para OpenAI y x-api-key
para Anthropic.

## Almacenamiento

Perfiles: `~/.config/modelhub/profiles.json`, o `$XDG_CONFIG_HOME/modelhub`.
Puedes usar `MODELHUB_HOME=/otra/ruta` para cambiar todo el almacenamiento.
Las claves se guardan en JSON **sin cifrar**, con permisos `0600`; los directorios
propios se crean con `0700`. Los archivos se reemplazan de forma atómica.
Evita editar perfiles simultáneamente desde dos procesos.
El listado no muestra claves. Cline y Claude reciben copias de configuración
dentro de `agents/<perfil>`. Eliminar un perfil elimina también ese directorio,
incluido el historial que el agente haya guardado allí.
`state.json` guarda la última selección, favoritos, preferencias y resultados de
pruebas, sin copiar las API keys. Eliminar un perfil también quita sus favoritos
y resultados. Los agentes instalados en `tools/` se conservan al eliminar perfiles.

## Comprobaciones

```bash
cd /root/modelhub
python3 -m unittest discover -s tests -v
```

Las pruebas no requieren claves reales, no instalan paquetes y no hacen llamadas
de inferencia de pago; el descubrimiento de modelos y los mensajes de la interfaz
se sustituyen por dobles en los tests.

Cubren la visibilidad de perfiles (formato, modelo y base URL), la validación de
`state.json` y de los perfiles, los adaptadores, la comprobación masiva del
catálogo y la traducción del puente Anthropic ↔ OpenAI, incluidos firmas de
`thinking`, ids de herramientas y texto junto a `tool_result`. Las pruebas de
interfaz usan una pseudoterminal real para flechas, búsqueda, Esc, entrada oculta
y restauración del terminal. La compatibilidad con tu proveedor y los CLIs
concretos se verifica ejecutándolos.
