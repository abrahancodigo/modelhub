#!/usr/bin/env python3
"""ModelHub: perfiles de API y lanzador de agentes, sin dependencias externas."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

import agent_catalog
import claude_bridge
import terminal_ui as ui


HOME = Path(os.environ.get("MODELHUB_HOME", Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "modelhub")).expanduser().resolve()
VERSION = "1.2.0"
CLINE_AUTH_TIMEOUT = 60
BUILTIN_AGENTS = {
    "opencode": {"command": "opencode", "protocols": ["openai", "anthropic"]},
    "cline": {"command": "cline", "protocols": ["openai", "anthropic"]},
    "claude": {"command": "claude", "protocols": ["anthropic", "openai"]},

    "qwen": {"command": "qwen", "protocols": ["openai"]},
    "pi": {"command": "pi", "protocols": ["openai", "anthropic"]},
    "codex": {"command": "codex", "protocols": ["openai"]},
    "kimi": {"command": "kimi", "protocols": ["openai", "anthropic"]},
}

QWEN_LIGHT_PROMPT = (
    'Eres un asistente de programación en terminal. Responde en el idioma del usuario y sé conciso. '
    'Para tareas de código, inspecciona los archivos relevantes antes de editar, respeta las convenciones '
    'del proyecto y verifica los cambios con las pruebas apropiadas. Usa las herramientas disponibles '
    'cuando sean necesarias; no uses herramientas para un saludo. Conserva el trabajo existente y pide '
    'aclaración cuando falte información esencial. No afirmes haber ejecutado acciones que no realizaste.'
)


def qwen_mode_args(mode):
    if mode == 'light':
        return ['--safe-mode', '--system-prompt', QWEN_LIGHT_PROMPT, '--approval-mode', 'default']
    if mode == 'normal':
        return []
    raise ValueError('Modo de Qwen desconocido.')


def resolve_agent(command):
    return shutil.which(command) or shutil.which(command, path=str(HOME / 'tools' / 'bin'))


def load_state():
    path = HOME / 'state.json'
    state = {'favorites': [], 'checks': {}, 'last': None, 'welcomed': False, 'plain': False}
    if path.exists():
        try:
            data = json.loads(path.read_text())
            if not isinstance(data, dict):
                raise ValueError()
            state.update(data)
            if not isinstance(state['favorites'], list) or not isinstance(state['checks'], dict):
                raise ValueError()
            for entry in state['favorites'] + ([state['last']] if state['last'] is not None else []):
                if not isinstance(entry, dict) or not all(isinstance(entry.get(k), str) and entry[k] for k in ('profile', 'agent', 'model')):
                    raise ValueError()
            if any(not isinstance(value, dict) for value in state['checks'].values()):
                raise ValueError()
            if not isinstance(state['welcomed'], bool) or not isinstance(state['plain'], bool):
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError(f'Estado inválido: {path}. Revisa el archivo; tus perfiles no se han modificado.') from None
    return state


def save_state(state):
    private_json(HOME / 'state.json', state)


def fingerprint(profile, salt=''):
    # salt='' keeps legacy checks valid; new checks store a random salt so
    # state.json never exposes an offline-brute-forceable digest of the key.
    return hashlib.sha256((salt + json.dumps(profile, sort_keys=True)).encode()).hexdigest()


def connection_status(name, profile):
    check = load_state()['checks'].get(name, {})
    if check.get('fingerprint') != fingerprint(profile, check.get('salt', '')):
        return 'Sin comprobar'
    when = check.get('time')
    suffix = f" · {time.strftime('%d/%m %H:%M', time.localtime(when))}" if when else ''
    return check.get('summary', 'Sin comprobar') + suffix


def key_status(profile):
    return 'Clave guardada' if profile.get('api_key') else 'Sin clave'


def profile_label(name, profile, with_status=True):
    label = f"{name} · {profile['protocol']} · {profile['model']} · {profile['base_url']}"
    return label + (f" · {connection_status(name, profile)}" if with_status else '')


def profile_rows(profiles, with_status=True):
    return {profile_label(name, profile, with_status): name for name, profile in profiles.items()}


def profile_card(name, profile, context=''):
    lines = [f"API: {profile['protocol']} · {profile['base_url']}",
             f"Modelo: {profile['model']}",
             f"{key_status(profile)} · {connection_status(name, profile)}"]
    if context:
        lines.append(context)
    return {'title': f"{name} · {profile['base_url']}", 'detail': '\n'.join(lines)}


def profile_cards(profiles):
    return {profile_label(name, profile): profile_card(name, profile) for name, profile in profiles.items()}


def render(value, context):
    """Substitute explicit tokens once, without interpreting inserted secrets."""
    if isinstance(value, str):
        def replace(match):
            key = match.group(1)
            if key not in context:
                raise ValueError(f"Variable desconocida en adaptador: {key}")
            return context[key]
        return re.sub(r"\{\{([a-z_]+)\}\}", replace, value)
    if isinstance(value, list):
        return [render(item, context) for item in value]
    if isinstance(value, dict):
        return {key: render(item, context) for key, item in value.items()}
    return value


def validate_adapter(spec):
    if not isinstance(spec, dict) or set(spec) - {"command", "protocols", "args", "env", "files"}:
        raise ValueError("Campos de adaptador inválidos.")
    if not isinstance(spec.get("command"), str) or not spec["command"]:
        raise ValueError("El adaptador necesita command (nombre del ejecutable o ruta absoluta).")
    if "{{" in spec["command"]:
        raise ValueError("command debe ser literal: no admite variables {{...}}.")
    protocols = spec.get("protocols")
    if not isinstance(protocols, list) or not protocols or any(p not in ("openai", "anthropic") for p in protocols):
        raise ValueError("El adaptador necesita protocols: openai y/o anthropic.")
    args, env, files = spec.get("args", []), spec.get("env", {}), spec.get("files", {})
    if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
        raise ValueError("args debe ser una lista de argumentos.")
    if not isinstance(env, dict) or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k) or not isinstance(v, str) for k, v in env.items()):
        raise ValueError("env debe contener nombres de variables y valores de texto.")
    if any(k in ('PATH', 'HOME', 'PWD') or k.startswith('MODELHUB_') for k in env):
        raise ValueError("El adaptador no puede redefinir variables reservadas: PATH, HOME, PWD ni ningún MODELHUB_*.")
    if not isinstance(files, dict):
        raise ValueError("files debe ser un objeto de archivos JSON.")
    for filename in files:
        path = Path(filename)
        if path.is_absolute() or ".." in path.parts or path.suffix != ".json":
            raise ValueError("Los archivos del adaptador deben ser rutas JSON relativas sin '..'.")
    render(spec, dict.fromkeys(("base_url", "api_key", "model", "protocol", "runtime", "profile"), "test"))
    return spec


def agent_registry():
    registry = dict(BUILTIN_AGENTS)
    for path in sorted((HOME / "adapters").glob("*.json")):
        try:
            name = validate_name(path.stem)
            if name in registry:
                raise ValueError("El nombre ya pertenece a un adaptador integrado.")
            registry[name] = validate_adapter(json.loads(path.read_text()))
        except (ValueError, OSError) as exc:
            # A broken third-party entry should not hide the working agents.
            print(f"Adaptador ignorado: {path.name} ({type(exc).__name__}). Revisa su formato.", file=sys.stderr)
    return registry


def installed_agents(protocol=None):
    return {name: spec for name, spec in agent_registry().items()
            if (protocol is None or protocol in spec["protocols"]) and resolve_agent(spec["command"])}


def list_agents():
    for name, spec in agent_registry().items():
        binary = resolve_agent(spec["command"])
        print(f"  {name}: {binary or 'no instalado / fuera de PATH'} · {', '.join(spec['protocols'])}")


def register_agent():
    name = validate_name(ask("Nombre del nuevo agente"))
    if name in agent_registry() or (HOME / "adapters" / f"{name}.json").exists():
        raise ValueError("Ese adaptador ya existe. Edita su JSON para actualizarlo.")
    command = ask("Ejecutable (nombre en PATH o ruta absoluta)", name)
    protocol = choose("Protocolo", ["openai", "anthropic"])
    print("Indica los nombres de variables que documenta este CLI.")
    env = {}
    for label, token in (("API key", "api_key"), ("Base URL", "base_url"), ("Modelo", "model")):
        variable = ask(f"Variable de entorno para {label}")
        if variable in env:
            raise ValueError("Cada campo necesita una variable de entorno diferente.")
        env[variable] = "{{" + token + "}}"
    spec = validate_adapter({"command": command, "protocols": [protocol], "env": env, "args": []})
    path = HOME / "adapters" / f"{name}.json"
    private_json(path, spec)
    print(f"Adaptador guardado: {path}. Se detectará cuando su ejecutable esté en PATH.")


def private_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".modelhub-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def private_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".modelhub-")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    os.chmod(path, 0o600)


def toml_string(value):
    # TOML basic strings reject raw control characters; escape whatever JSON may carry.
    result = value.replace("\\", "\\\\").replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
    return ''.join(ch if ord(ch) >= 32 else f'\\u{ord(ch):04X}' for ch in result)


def load():
    path = HOME / "profiles.json"
    if not path.exists():
        return {}
    invalid = f"Configuración inválida: {path}. Revisa el archivo; no se ha sobrescrito."
    try:
        data = json.loads(path.read_text())
    except (ValueError, TypeError):
        raise ValueError(invalid) from None
    if not isinstance(data, dict):
        raise ValueError(invalid)
    for name, profile in data.items():
        try:
            validate_name(name)
        except ValueError:
            raise ValueError(f"Nombre de perfil inválido: '{name}'. Usa letras, números, guiones o guiones bajos.") from None
        if not isinstance(profile, dict) or not all(isinstance(profile.get(k), str) for k in ("base_url", "api_key", "model", "protocol")):
            raise ValueError(invalid)
        try:
            validate_url(profile["base_url"])
        except ValueError:
            raise ValueError(f"Base URL inválida en el perfil '{name}': {profile['base_url']}") from None
        if profile["protocol"] not in ("openai", "anthropic") or not profile["model"]:
            raise ValueError(invalid)
    return data


def validate_name(value):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", value):
        raise ValueError("Nombre: usa letras, números, guiones o guiones bajos (máximo 64).")
    return value


def validate_url(value):
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Base URL: usa http(s)://servidor/ruta, sin credenciales, query ni fragmento.")
    return value.rstrip("/")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    # Los proveedores deben responder la verdad sin seguir redirecciones, y
    # tampoco queremos que urllib reintente el login básico: ese reintento
    # convierte un 401 legítimo en un 403 confuso.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

    def http_error_auth_reqed(self, authreq, host, req, headers):
        return None


def entry_id(entry):
    # Los catálogos no son uniformes: {"id": ...}, {"name": ...} o cadenas sueltas.
    if isinstance(entry, str):
        return entry.strip()
    if isinstance(entry, dict):
        for key in ('id', 'name', 'model', 'model_id', 'slug'):
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ''


def request_timeout():
    seconds = 15.0
    try:
        seconds = float(os.environ.get('MODELHUB_TIMEOUT', seconds))
    except (TypeError, ValueError):
        pass
    return min(300.0, max(1.0, seconds))


def error_code(message):
    match = re.match(r'HTTP (\d{3})', str(message))
    return int(match.group(1)) if match else None


def request_json(profile, endpoint, body=None, base=None, _seen=()):
    base = base or validate_url(profile["base_url"])
    # Comparar sin '/' final evita que un perfil anthropic editado a mano
    # (con / de sobra) no reciba el sufijo /v1 en /messages y /models.
    if (base == profile["base_url"].rstrip("/") and profile["protocol"] == "anthropic"
            and not base.endswith("/v1") and not endpoint.startswith("/v1/")):
        base += "/v1"
    headers = {"Accept": "application/json"}
    if profile["protocol"] == "anthropic":
        headers["anthropic-version"] = "2023-06-01"
        if profile["api_key"]:
            headers["x-api-key"] = profile["api_key"]
    elif profile["api_key"]:
        headers["Authorization"] = "Bearer " + profile["api_key"]
    if body is not None:
        headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(base + endpoint, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
    seen = (request.full_url,) + tuple(_seen)
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=request_timeout()) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        code = exc.code
        location = exc.headers.get('Location') if exc.headers else None
        exc.close()
        if code in (301, 302, 303, 307, 308) and location:
            # Gateways que redirigen hacia la ruta real del catálogo: seguirla a
            # mano, sin repetir una URL ya visitada, evita bucles y muestra los
            # modelos en vez de rendirse en la primera redirección.
            target = urllib.parse.urljoin(request.full_url, location)
            if target not in seen:
                return request_json(profile, '', body=body, base=target, _seen=seen)
        hints = {401: 'API key rechazada.', 403: 'La clave no tiene permiso para este recurso.',
                 404: 'Ruta no encontrada; revisa la base URL o usa un modelo manual.',
                 429: 'Límite de peticiones o saldo del proveedor.',
                 400: 'El proveedor rechazó el modelo o el formato de la petición.'}
        raise ValueError(f"HTTP {code}. {hints.get(code, 'El proveedor no pudo completar la petición; prueba más tarde.')}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ValueError("No se pudo conectar (red, TLS o tiempo de espera). Revisa la URL y la conexión.") from None
    except ValueError:
        raise ValueError("La API no devolvió JSON válido.") from None
    return payload


def catalog_endpoints(profile):
    """Tuplas (base_url, endpoint) de catálogo que se prueban en orden.

    Se prueban las variantes habituales de gateways y agregadores antes de
    rendirse: /models bajo la base configurada, la variante `?limit` de
    OpenRouter/Cerebras, bajo la raíz del host, con /v1 cuando la base ya lo
    incluye y /api/tags en servidores locales estilo Ollama.
    """
    full = validate_url(profile['base_url'])
    root = urllib.parse.urlunsplit(urllib.parse.urlsplit(full)._replace(path='', query='', fragment='')).rstrip('/')
    host = urllib.parse.urlsplit(full).netloc
    deepseek = host in ('api.deepseek.com', 'api.moonshot.cn', 'api.moonshot.ai')
    tagged = root.endswith(':11434')  # servidor local estilo Ollama
    candidates = [(full, '/models?limit=1000')] \
        if full.endswith('/v1') or profile['protocol'] == 'anthropic' else []
    candidates += [(full, '/models'), (root, '/models'), (root, '/v1/models')]
    if not (full.endswith('/v1') or tagged):
        # Gateway que publica el catálogo al final de la ruta configurada.
        candidates.append((full, '/models?limit=1000'))
    if tagged:
        candidates.append((root, '/api/tags'))
    if deepseek:
        # Usan el protocolo OpenAI pero su GET /models solo responde en la raíz.
        candidates = [(root, endpoint) for _, endpoint in candidates]
    endpoints, seen = [], set()
    for pair in candidates:
        if pair[0] and pair not in seen:
            seen.add(pair)
            endpoints.append(pair)
    return endpoints


def catalog_cursor(profile, endpoints):
    """Endpoint de una sola petición que ya devolvió modelos, o None."""
    cursor = (profile.get('base_url'), profile.get('catalog_path'))
    return cursor if cursor in endpoints else None


def discover(profile):
    """Devuelve el catálogo de modelos con tolerancia a APIs que restringen /models.

    Algunos gateways restringen una ruta del catálogo y publican los modelos en
    otra variante. Se prueban todas las rutas habituales incluso ante 401/403;
    ciertos proveedores aplican permisos distintos según la ruta.
    """
    endpoints = catalog_endpoints(profile)
    remembered = catalog_cursor(profile, endpoints)
    if remembered:
        endpoints = [remembered] + [pair for pair in endpoints if pair != remembered]
    errors = []
    for base, endpoint in endpoints:
        try:
            models = fetch_models(profile, base, endpoint)
        except ValueError as error:
            errors.append(str(error))
            continue
        if models:
            return sorted(models)
    raise ValueError(catalog_error(errors))


def catalog_error(errors):
    auth_error = next((error for error in errors if error_code(error) in (401, 403)), None)
    detail = auth_error or (errors[0] if errors else 'La API no respondió al catálogo de modelos.')
    return (f'{detail} No se pudo leer el catálogo en las rutas habituales. '
            'Escribe el ID del modelo a mano y usa Comprobar conexión con inferencia para validarlo.')


def fetch_models(profile, base, endpoint):
    models = set()
    after = None
    seen_afters = set()
    for _ in range(100):  # límite de seguridad: 100 páginas
        path = endpoint
        if after is not None:
            # OpenAI usa `after`; Anthropic usa `after_id`. Conserva los demás
            # parámetros de la ruta, por ejemplo `limit`, entre páginas.
            split = urllib.parse.urlsplit(endpoint)
            params = [(key, value) for key, value in urllib.parse.parse_qsl(split.query, keep_blank_values=True)
                      if key not in ('after', 'after_id')]
            cursor_name = 'after_id' if profile['protocol'] == 'anthropic' else 'after'
            params.append((cursor_name, after))
            path = urllib.parse.urlunsplit(split._replace(query=urllib.parse.urlencode(params)))
        payload = request_json(profile, path, base=base)
        if isinstance(payload, dict):
            # OpenAI/Anthropic usan `data`; otros servicios usan `models`,
            # `results` o `items`. Se admite también el formato de Ollama.
            entries = next((payload[key] for key in ('data', 'models', 'results', 'items')
                            if isinstance(payload.get(key), list)), [])
        else:
            entries = payload
        if not isinstance(entries, list):
            raise ValueError("La respuesta no contiene una lista de modelos.")
        # Catálogos no uniformes: cadenas o diccionarios con id, name, model,
        # model_id o slug.
        models.update(entry_id(entry) for entry in entries if entry_id(entry))
        # Anthropic publica last_id; los catálogos OpenAI suelen usar el último id.
        has_more = bool(isinstance(payload, dict) and payload.get("has_more"))
        if not has_more:
            break
        ids_in_page = [entry_id(entry) for entry in entries if entry_id(entry)]
        if not ids_in_page:
            break
        after = (payload.get('last_id') if profile['protocol'] == 'anthropic' else None) or ids_in_page[-1]
        if after in seen_afters:
            break
        seen_afters.add(after)
    if not models:
        raise ValueError("Esta API no publicó modelos. Puedes escribir el ID manualmente.")
    return models



def check_connection(name, profile, inference=False, report=True):
    if report:
        print('Probando conexión…', flush=True)
    ok = True
    try:
        if inference:
            endpoint = '/messages' if profile['protocol'] == 'anthropic' else '/chat/completions'
            payload = request_json(profile, endpoint, {'model': profile['model'], 'max_tokens': 16,
                                   'messages': [{'role': 'user', 'content': 'Reply OK.'}]})
            field = 'content' if profile['protocol'] == 'anthropic' else 'choices'
            if not isinstance(payload, dict) or not isinstance(payload.get(field), list) or not payload[field]:
                raise ValueError('La respuesta no contiene una salida de modelo válida.')
            summary = 'Modelo respondió'
            detail = 'La API aceptó una petición al modelo seleccionado. No se ha verificado soporte de herramientas.'
        else:
            models = discover(profile)
            summary = 'Catálogo accesible'
            detail = f'{len(models)} modelos recibidos. '
            detail += ('Tu modelo aparece en la lista.' if profile['model'] in models else 'Tu modelo no aparece en esta página del catálogo.')
            detail += '\nEsto comprueba /models, no una respuesta de inferencia ni necesariamente la autenticación.'
    except ValueError as exc:
        ok, summary, detail = False, 'Falló la comprobación', str(exc)
    salt = secrets.token_hex(8)
    state = load_state()
    state['checks'][name] = {'fingerprint': fingerprint(profile, salt), 'salt': salt, 'summary': summary,
                             'detail': detail, 'time': time.time(), 'ok': ok}
    save_state(state)
    if report:
        ui.message(summary, detail)
    return ok


def check_all():
    profiles = load()
    if not profiles:
        ui.message('Sin APIs', 'No hay perfiles guardados. Usa: modelhub add')
        return
    if not ui.confirm('¿Comprobar el catálogo de todas las APIs?',
                      f'Se consultará GET /models en {len(profiles)} perfiles. No se envía inferencia.'):
        return
    lines = []
    for name, profile in profiles.items():
        print(f'Comprobando {name}…', flush=True)
        ok = check_connection(name, profile, report=False)
        check = load_state()['checks'].get(name, {})
        lines.append(f"{'✓' if ok else '✕'} {name} · {profile['base_url']}\n"
                     f"   {check.get('summary', 'Sin resultado')}")
    ui.message('APIs comprobadas', '\n'.join(lines))


def ask(label, default="", required=True):
    while True:
        value = ui.prompt(label, default)
        if value or not required:
            return value
        print("Introduce un valor.")


def choose(label, values, cards=None):
    if not values:
        raise ValueError('No hay opciones disponibles para: ' + label.splitlines()[0])
    return ui.select(label, values, cards=cards) if cards else ui.select(label, values)


def pick_model(profile, initial=''):
    value = ui.prompt("Modelo: escribe el ID o pulsa Enter para buscar en tu API", default=initial)
    if value:
        return value
    try:
        models = discover(profile)
    except ValueError as exc:
        ui.message('No pudimos cargar la lista de modelos', f'{exc}\nSiguiente paso: escribe el ID del modelo manualmente o pulsa Esc para volver.')
        return ask("ID del modelo", profile.get("model", ""))
    filtered = models
    if not ui.interactive():
        term = ui.prompt(f"{len(models)} modelos. Filtrar por texto (Enter: todos)").casefold()
        filtered = [m for m in models if term in m.casefold()]
    selected = choose("Modelos", filtered + ["Escribir ID manualmente"])
    return ask("ID del modelo") if selected == "Escribir ID manualmente" else selected


@ui.scoped
def edit_profile(profiles, name=None, editing=False):
    if name:
        validate_name(name)
    if name in profiles and not editing:
        raise ValueError("El perfil ya existe. Usa: modelhub edit " + name)
    if editing and name not in profiles:
        raise ValueError("No existe ese perfil.")
    draft = dict(profiles.get(name, {}))
    draft_name = name or ''
    first = 1 if name else 0
    step = first
    while step < 5:
        ui.set_context(profile=draft_name, agent='', mode='', model=draft.get('model', ''))
        try:
            if step == 0:
                candidate = ask('Nueva API · Paso 1/5: nombre del perfil', draft_name)
                draft_name = candidate
                validate_name(candidate)
                if candidate in profiles:
                    raise ValueError('Ese perfil ya existe. Usa Editar API para modificarlo.')
            elif step == 1:
                current = draft.get('protocol', 'openai')
                draft['protocol'] = choose('API · Paso 2/5: formato\nEsc vuelve al paso anterior y conserva tus datos.',
                                           [current] + [p for p in ('openai', 'anthropic') if p != current])
            elif step == 2:
                example = 'https://api.openai.com/v1' if draft['protocol'] == 'openai' else 'https://api.anthropic.com'
                draft['base_url'] = ask(f'API · Paso 3/5: base URL (ejemplo: {example})', draft.get('base_url', ''))
                draft['base_url'] = validate_url(draft['base_url'])
            elif step == 3:
                key = ui.prompt("API · Paso 4/5: clave oculta (Enter conserva; '-' sin clave)",
                                default=draft.get('api_key', ''), secret=True)
                draft['api_key'] = '' if key == '-' else key
            else:
                draft['model'] = pick_model(draft, initial=draft.get('model', ''))
            step += 1
        except ui.Cancelled as exc:
            if exc.value:
                field = {0: None, 2: 'base_url', 3: 'api_key', 4: 'model'}.get(step)
                if step == 0:
                    draft_name = exc.value
                elif field:
                    draft[field] = '' if field == 'api_key' and exc.value == '-' else exc.value
            if step == first:
                raise
            step -= 1
        except ValueError as exc:
            ui.message('Revisa este dato', str(exc))
    name = draft_name
    profiles[name] = draft
    private_json(HOME / "profiles.json", profiles)
    state = load_state()
    state['checks'].pop(name, None)
    save_state(state)
    ui.message('API guardada', f"Perfil '{name}' · {draft['model']}\nYa puedes elegir un agente para trabajar.")
    return name


def selected(profiles, name):
    if not name:
        if not profiles:
            raise ValueError('No hay perfiles. Primero usa: modelhub add')
        rows = profile_rows(profiles)
        name = rows[choose('Perfiles\nCada fila muestra formato, modelo y base URL.', list(rows),
                            cards=profile_cards(profiles))]
    if name not in profiles:
        raise ValueError(f"No existe el perfil '{name}'.")
    return name, profiles[name]


def opencode_config(profile):
    base = profile["base_url"]
    package = "openai-compatible"
    if profile["protocol"] == "anthropic":
        package = "anthropic"
        if not base.endswith("/v1"):
            base += "/v1"
    return {
        "$schema": "https://opencode.ai/config.json",
        "model": "modelhub/" + profile["model"],
        "providers": {"modelhub": {
            "name": "ModelHub", "env": ["MODELHUB_API_KEY"],
            "package": "@opencode/ai/providers/" + package,
            "settings": {"baseURL": base, "apiKey": "{env:MODELHUB_API_KEY}"},
            "models": {profile["model"]: {"name": profile["model"]}},
        }},
    }


def prepare_qwen(runtime, profile, env):
    """Register the actual provider, not just ephemeral CLI model overrides."""
    config_home = runtime / 'qwen'
    path = config_home / 'settings.json'
    settings = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(settings, dict):
        raise ValueError(f'Configuración Qwen inválida: {path}')
    if any(not isinstance(settings.get(key, {}), dict) for key in ('modelProviders', 'security', 'model')):
        raise ValueError(f'Configuración Qwen inválida: {path}')
    if not isinstance(settings.get('security', {}).get('auth', {}), dict):
        raise ValueError(f'Configuración Qwen inválida: {path}')
    providers = settings.setdefault('modelProviders', {})
    models = providers.setdefault('openai', [])
    if not isinstance(models, list) or any(not isinstance(m, dict) for m in models):
        raise ValueError(f'Lista de modelos Qwen inválida: {path}')
    generation = {}
    for model in models:
        if (model.get('id') == profile['model']
                and model.get('envKey') == 'MODELHUB_API_KEY'
                and model.get('baseUrl') == profile['base_url']):
            generation = model.get('generationConfig', {})
            if not isinstance(generation, dict):
                raise ValueError(f'Configuración de generación Qwen inválida: {path}')
            generation = dict(generation)
            break
    # Older ModelHub launches forced a 60s timeout, even when Qwen's native
    # environment override requested a longer wait. Migrate only that exact
    # generated configuration; keep deliberate model/provider tuning intact.
    if generation == {'timeout': 60000, 'maxRetries': 1}:
        generation = {}
    entry = {'id': profile['model'], 'name': profile['model'],
             'baseUrl': profile['base_url'], 'envKey': 'MODELHUB_API_KEY',
             'wireApi': 'chat-completions',
             'generationConfig': generation}
    providers['openai'] = [m for m in models if m.get('id') != profile['model'] and m.get('envKey') != 'MODELHUB_API_KEY'] + [entry]
    settings.setdefault('security', {}).setdefault('auth', {})['selectedType'] = 'openai'
    settings.setdefault('model', {})['name'] = profile['model']
    private_json(path, settings)
    # Keep the default in the child environment so it never becomes a persisted
    # provider timeout that shadows future QWEN_CODE_API_TIMEOUT_MS overrides.
    env.setdefault('QWEN_CODE_API_TIMEOUT_MS', '300000')
    env.update({'QWEN_HOME': str(config_home), 'QWEN_RUNTIME_DIR': str(config_home),
                'MODELHUB_API_KEY': profile['api_key'] or 'not-required',
                'OPENAI_API_KEY': profile['api_key'] or 'not-required',
                'OPENAI_BASE_URL': profile['base_url'], 'OPENAI_MODEL': profile['model']})


def prepare_codex(runtime, profile, env):
    """Point Codex at a Responses-API endpoint via an isolated CODEX_HOME."""
    config_home = runtime / 'codex'
    private_text(config_home / 'config.toml',
                 'model = "' + toml_string('modelhub/' + profile['model']) + '"\n'
                 'model_provider = "modelhub"\n'
                 '[model_providers.modelhub]\n'
                 'name = "ModelHub"\n'
                 'base_url = "' + toml_string(profile['base_url']) + '"\n'
                 'env_key = "MODELHUB_API_KEY"\n'
                 'wire_api = "responses"\n')
    env.update({'CODEX_HOME': str(config_home),
                'MODELHUB_API_KEY': profile['api_key'] or 'not-required'})


def prepare_kimi(runtime, profile, env):
    """Point Kimi Code at the profile via an isolated KIMI_CODE_HOME."""
    config_home = runtime / 'kimi'
    provider_type = 'openai' if profile['protocol'] == 'openai' else 'anthropic'
    base_url = profile['base_url']
    if provider_type == 'anthropic':
        # The Anthropic client appends /v1/messages itself.
        base_url = base_url.removesuffix('/v1')
    private_text(config_home / 'config.toml',
                 'default_model = "' + toml_string(profile['model']) + '"\n\n'
                 '[providers.modelhub]\n'
                 'type = "' + provider_type + '"\n'
                 'base_url = "' + toml_string(base_url) + '"\n'
                 'api_key_env = "MODELHUB_API_KEY"\n\n'
                 '[models."' + toml_string(profile['model']) + '"]\n'
                 'provider = "modelhub"\n'
                 'model = "' + toml_string(profile['model']) + '"\n'
                 'max_context_size = 262144\n')
    env.update({'KIMI_CODE_HOME': str(config_home),
                'MODELHUB_API_KEY': profile['api_key'] or 'not-required'})


def authenticate_cline(command, env):
    """Bound setup and stop the CLI wrapper together with its auth child."""
    with subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL,
                          start_new_session=os.name == 'posix') as process:
        try:
            return process.wait(timeout=CLINE_AUTH_TIMEOUT)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            # npm's Cline entry point starts a compiled child. Killing only
            # the wrapper leaves that child running and able to rewrite auth.
            try:
                if os.name == 'posix':
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
            process.wait()
            if isinstance(exc, subprocess.TimeoutExpired):
                raise ValueError('Cline superó el tiempo de espera al configurar el proveedor. '
                                 'Comprueba la conexión y vuelve a intentarlo.') from None
            raise


def prepare_claude(runtime, profile, env):
    """Configura Claude Code con un perfil Anthropic o via puente OpenAI.

    Claude Code solo habla Anthropic Messages. Para un perfil OpenAI se levanta
    un traductor local (127.0.0.1, puerto libre) que reescribe Anthropic <-> OpenAI
    y mantiene la clave real fuera del proceso del agente. Devuelve el puente
    para poder cerrarlo cuando termine la sesion.
    """
    for key in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN",
                "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
                "CLAUDE_CODE_USE_ANTHROPIC_AWS"):
        env.pop(key, None)
    config_home = runtime / 'claude'
    config_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    bridge = None
    settings = {"env": {"ANTHROPIC_BASE_URL": profile["base_url"].removesuffix("/v1"),
                        "ANTHROPIC_API_KEY": profile["api_key"],
                        "ANTHROPIC_MODEL": profile["model"],
                        "ANTHROPIC_DEFAULT_HAIKU_MODEL": profile["model"],
                        "ANTHROPIC_DEFAULT_SONNET_MODEL": profile["model"],
                        "ANTHROPIC_DEFAULT_OPUS_MODEL": profile["model"]}}
    if profile["protocol"] == "openai":
        counter = 0
        try:
            with urllib.request.urlopen(profile["base_url"].rstrip("/") + "/models",
                                        timeout=request_timeout()) as response:
                catalogue = json.load(response)
            # Algunos gateways publican una lista directa en vez de {"data": [...]}.
            data = catalogue.get("data") if isinstance(catalogue, dict) else catalogue
            counter = next((index + 1 for index, item in enumerate(data or [])
                            if isinstance(item, dict) and item.get("id") == profile["model"]), 0)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError, TypeError):
            # Sin catalogo seguimos: el puente solo necesita la base URL y la clave.
            counter = 0
        bridge = claude_bridge.start(profile, counter=counter)
        # La clave real vive solo en el puente: Claude Code recibe un token local.
        settings["env"]["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:%d" % bridge.server_port
        settings["env"]["ANTHROPIC_AUTH_TOKEN"] = bridge.server_token
        settings["env"].pop("ANTHROPIC_API_KEY", None)

    path = runtime / "claude.json"
    private_json(path, settings)
    # CLAUDE_CONFIG_DIR mantiene el historial de Claude Code dentro del perfil.
    env["CLAUDE_CONFIG_DIR"] = str(config_home)
    env.update(settings["env"])
    return bridge


def resolve_project_dir(agent, directory):
    """Devuelve (directorio de trabajo, [directorios extra]) donde arranca el agente.

    Claude Code recibe la carpeta de trabajo y las adicionales mediante
    `--add-dir`. La carpeta de inicio ($HOME) queda excluida porque puede
    contener claves y configuración; si ModelHub se ejecuta desde ahí, pide
    carpetas de proyecto concretas. Una carpeta principal inexistente se crea.
    """
    cwd = Path(os.getcwd()).resolve()
    home = Path.home().resolve()
    raw_parts = directory if isinstance(directory, (list, tuple)) else [directory]
    values = []
    for value in raw_parts:
        if not value:
            continue
        # Solo Claude Code admite varias carpetas en un mismo --dir (:).
        if agent == 'claude':
            values.extend(part for part in str(value).split(os.pathsep) if part)
        else:
            values.append(str(value))
    parts = [Path(value).expanduser().resolve() for value in values]
    if not parts and agent == 'claude' and cwd == home:
        parts = [ui.pick_directory('Claude Code · elige la carpeta de proyecto', start=home)]
    main = parts[0] if parts else None
    extras = list(dict.fromkeys(str(path) for path in parts[1:] if path != parts[0])) \
        if agent == 'claude' else []
    if main is None:
        main = cwd
    if agent == 'claude' and any(path == home for path in [main, *(Path(path) for path in extras)]):
        ui.message('Claude Code no puede arrancar en $HOME',
                   'Elige carpetas de proyecto concretas. Tu carpeta de inicio '
                   'contiene claves y configuración; puedes indicar varios '
                   'proyectos separados por «:».')
        raise ValueError('Claude Code no puede usar la carpeta de inicio; indica una o más carpetas de proyecto.')
    for extra in extras:
        if not Path(extra).is_dir():
            raise ValueError(f'La carpeta adicional de Claude Code no existe: {extra}')
    if not main.is_dir():
        main.mkdir(parents=True)
        ui.message('Carpeta creada', str(main))
    return main, extras


def launch(name, profile, agent, extra=(), mode='normal', directory=''):
    validate_name(name)
    registry = agent_registry()
    if agent not in registry:
        raise ValueError(f"Agente desconocido: '{agent}'. Usa 'modelhub register-agent' para añadir su adaptador.")
    spec = registry[agent]
    if mode != 'normal' and agent != 'qwen':
        raise ValueError('El modo ligero está disponible para Qwen.')
    if profile["protocol"] not in spec["protocols"]:
        raise ValueError(f"'{agent}' no admite perfiles {profile['protocol']} en este adaptador.")
    binary = resolve_agent(spec["command"])
    if not binary:
        raise ValueError(f"'{agent}' no está instalado o no está en PATH. Consulta modelhub doctor.")
    env = os.environ.copy()
    env['PATH'] = os.pathsep.join([env.get('PATH', ''), str(HOME / 'tools' / 'bin')])
    runtime = HOME / "agents" / name
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    bridge = None

    if agent == "opencode":
        # XDG_CONFIG_HOME is the documented global configuration location in V2.
        config_home = runtime / "xdg"
        private_json(config_home / "opencode" / "opencode.json", opencode_config(profile))
        env["XDG_CONFIG_HOME"] = str(config_home)
        env["MODELHUB_API_KEY"] = profile["api_key"]
        command = [binary, "--standalone"]
    elif agent == "cline":
        config_home = runtime / "cline"
        config_home.mkdir(parents=True, exist_ok=True, mode=0o700)
        env["CLINE_DIR"] = str(config_home)
        env["CLINE_DATA_DIR"] = str(config_home / "data")
        env["CLINE_SESSION_BACKEND_MODE"] = "local"
        provider = "openai" if profile["protocol"] == "openai" else "anthropic"
        auth = [binary, "auth", "--provider", provider, "--apikey", profile["api_key"] or "not-required", "--baseurl", profile["base_url"], "--modelid", profile["model"]]
        # Cline's documented auth interface accepts the key as an argument.
        if authenticate_cline(auth, env):
            raise ValueError("Cline no pudo configurar el proveedor. Comprueba la versión y 'cline auth --help'.")
        command = [binary, "--tui", "--provider", provider, "--model", profile["model"]]
    elif agent == "claude":
        bridge = prepare_claude(runtime, profile, env)
        command = [binary, "--settings", str(runtime / "claude.json"), "--model", profile["model"]]
    elif agent == "qwen":
        prepare_qwen(runtime, profile, env)
        command = [binary, "--auth-type", "openai", "--model", profile["model"],
                   "--openai-base-url", profile["base_url"]] + qwen_mode_args(mode)
    elif agent == "codex":
        prepare_codex(runtime, profile, env)
        command = [binary, "--model", profile["model"]]
    elif agent == "kimi":
        prepare_kimi(runtime, profile, env)
        # El modelo por defecto ya está declarado en default_model de
        # config.toml; -m no es necesario.
        command = [binary]
    elif agent == "pi":
        config_home = runtime / "pi"
        base = profile["base_url"]
        api = "openai-completions"
        if profile["protocol"] == "anthropic":
            api = "anthropic-messages"
            base = base.removesuffix("/v1")
        private_json(config_home / "models.json", {"providers": {"modelhub": {
            "baseUrl": base, "api": api, "apiKey": "${MODELHUB_API_KEY}",
            "models": [{"id": profile["model"]}],
        }}})
        env["PI_CODING_AGENT_DIR"] = str(config_home)
        env["MODELHUB_API_KEY"] = profile["api_key"] or "not-required"
        command = [binary, "--provider", "modelhub", "--model", profile["model"]]
    else:
        custom_home = runtime / "custom" / agent
        custom_home.mkdir(parents=True, exist_ok=True, mode=0o700)
        context = {**profile, "profile": name, "runtime": str(custom_home)}
        rendered = render(spec, context)
        for filename, contents in rendered.get("files", {}).items():
            path = custom_home / filename
            if custom_home.resolve() not in path.resolve().parents:
                raise ValueError("La ruta del archivo sale del directorio del adaptador.")
            private_json(path, contents)
        env.update(rendered.get("env", {}))
        command = [binary] + rendered.get("args", [])
    workdir, add_dirs = resolve_project_dir(agent, directory)
    if agent == 'claude' and add_dirs:
        command += ["--add-dir", *add_dirs]
    state = load_state()
    state['last'] = {'profile': name, 'agent': agent, 'model': profile['model']}
    if mode != 'normal':
        state['last']['mode'] = mode
    save_state(state)
    print(f"Abriendo {agent} · {name} · {profile['model']} en {workdir}", flush=True)
    try:
        return subprocess.run(command + list(extra), env=env, cwd=workdir).returncode
    finally:
        if bridge is not None:
            # El traductor de Claude Code solo vive lo que dura la sesion.
            bridge.shutdown()
            bridge.server_close()



def doctor():
    print(f"Perfiles: {HOME / 'profiles.json'}")
    list_agents()
    print(f"Adaptadores adicionales: {HOME / 'adapters'}")
    print("Instalación: https://opencode.ai/v2/docs/ · https://docs.cline.bot/getting-started/installing-cline · https://code.claude.com/docs/en/setup · https://developers.openai.com/codex/ · https://github.com/MoonshotAI/kimi-code")


@ui.scoped
def run_selection(entry, extra=(), directory=''):
    directory = entry.get('dir') or directory
    profiles = load()
    name, profile = selected(profiles, entry['profile'])
    if entry['agent'] not in agent_registry():
        raise ValueError('El adaptador ya no existe. Elige otro agente o vuelve a registrarlo.')
    spec = agent_registry()[entry['agent']]
    if not resolve_agent(spec['command']) and entry['agent'] in agent_catalog.CATALOG:
        if not install_agent(entry['agent']):
            return 1
    mode = entry.get('mode', 'normal')
    ui.set_context(profile=name, agent=entry['agent'], model=entry['model'],
                   mode='Ligero' if mode == 'light' else 'Completo')
    launch_args = (name, dict(profile, model=entry['model']), entry['agent'])
    if mode != 'normal':
        code = launch(*launch_args, extra, mode=mode, directory=directory)
    else:
        code = launch(*launch_args, extra, directory=directory) if extra \
            else launch(*launch_args, directory=directory)
    if code:
        ui.message('✕ El agente terminó con un error', f"Código de salida: {code}. Revisa su salida y prueba la conexión desde Mis APIs.")
    return code


def agent_cards(agents, profile_name=None, profile=None):
    cards = {}
    for agent in agents:
        item = agent_catalog.CATALOG.get(agent, {})
        spec = agent_registry()[agent]
        status = '✓ Instalado' if resolve_agent(spec['command']) else 'No instalado'
        cards[agent] = {'title': f"{item.get('name', agent)} · {status}",
                        'detail': f"{item.get('description', 'Adaptador personalizado.')}\n"
                                  f"API: {profile_name or 'Por elegir'} · Modelo: {(profile or {}).get('model', 'Por elegir')}\n"
                                  f"Compatible con: {' / '.join(spec['protocols'])}"}
    return cards


def choose_qwen_mode(current='normal'):
    modes = {'Ligero · menos contexto, sesiones sencillas': 'light',
             'Completo · funciones y personalizaciones habituales': 'normal'}
    options = sorted(modes, key=lambda label: modes[label] != current)
    return modes[choose('Empezar · elige el modo de Qwen\nLigero omite contexto personalizado, skills y MCP. Completo conserva esas funciones.', options)]


@ui.scoped
def start_flow(agent=None, profile_name=None):
    requested_agent = agent
    profiles = load()
    if not profiles:
        if ui.confirm('No tienes APIs guardadas. ¿Crear la primera?'):
            profile_name = edit_profile(profiles)
        else:
            return
    if agent:
        if agent not in agent_registry():
            raise ValueError(f"Agente desconocido: '{agent}'. Usa 'modelhub explore' para ver el catálogo.")
        protocols = agent_registry()[agent]['protocols']
        profiles = {name: p for name, p in profiles.items() if p['protocol'] in protocols}
        if not profiles:
            raise ValueError(f'No hay perfiles compatibles con {agent}. Añade una API {" / ".join(protocols)} desde Mis APIs.')
    name = profile_name
    profile = dict(profiles[name]) if name in profiles else {}
    mode = 'normal'
    workdir = ''
    step = (2 if agent else 1) if profile else 0
    while True:
        ui.set_context(profile=name or '', agent=agent or '', model=profile.get('model', ''),
                       mode=('Ligero' if mode == 'light' else 'Completo') if agent else '')
        try:
            if step == 0:
                rows = profile_rows(profiles)
                selected_name = rows[choose(
                    '◆ Empezar · 1/3 Tu API\nCada fila muestra formato, modelo y base URL. Esc vuelve.',
                    list(rows), cards=profile_cards(profiles))]
                if selected_name != name:
                    name, profile = selected_name, dict(profiles[selected_name])
                    agent, mode = requested_agent, 'normal'
                step = 2 if requested_agent else 1
            elif step == 1:
                available = installed_agents(profile['protocol'])
                if not available:
                    if ui.confirm('No hay agentes compatibles instalados. ¿Explorar el catálogo?'):
                        explore_agents(profile['protocol'], name)
                        return
                    step = 0
                    continue
                options = sorted(available, key=lambda item: item != agent) if agent else list(available)
                agent = choose('◆ Empezar · 2/3 Tu agente\nLa tarjeta muestra estado y API.', options,
                               cards=agent_cards(available, name, profile))
                step = 2
            elif step == 2:
                if agent == 'qwen':
                    mode = choose_qwen_mode(mode)
                else:
                    mode = 'normal'
                step = 3
            else:
                entry = {'profile': name, 'agent': agent, 'model': profile['model']}
                if mode == 'light':
                    entry['mode'] = mode
                if workdir:
                    entry['dir'] = workdir
                actions = ['Abrir agente ahora', 'Elegir carpeta de trabajo',
                           'Guardar como favorito', 'Probar conexión antes de abrir', 'Cambiar modelo']
                if agent == 'qwen':
                    actions += ['Cambiar modo', 'Abrir Qwen en modo diagnóstico']
                folder = workdir or str(Path.cwd())
                action = choose(
                    f'◆ Empezar · 3/3 Listo para abrir\nAPI: {name} · {profile["base_url"]}\n'
                    f'Agente: {agent} · Modelo: {profile["model"]}\nCarpeta: {folder}',
                    actions + ['Volver'])
                if action in ('Volver', 'Cambiar modo'):
                    step = 2 if agent == 'qwen' else 1
                elif action == 'Abrir agente ahora':
                    run_selection(entry)
                    return
                elif action == 'Elegir carpeta de trabajo':
                    try:
                        workdir = str(ui.pick_directory('Carpeta de trabajo', start=workdir or None))
                    except ui.Cancelled:
                        pass
                elif action == 'Guardar como favorito':
                    save_favorite(entry)
                elif action == 'Abrir Qwen en modo diagnóstico':
                    run_selection(dict(entry, mode='normal'), ['--safe-mode'])
                    return
                elif action == 'Probar conexión antes de abrir':
                    try:
                        test_flow(name, profile)
                    except ui.Cancelled:
                        pass
                elif action == 'Cambiar modelo':
                    try:
                        new_model = pick_model(profile)
                    except ui.Cancelled:
                        pass
                    else:
                        profile['model'] = new_model
                        saved = load()
                        if name in saved:
                            saved[name]['model'] = new_model
                            private_json(HOME / 'profiles.json', saved)
                        state = load_state()
                        state['checks'].pop(name, None)
                        save_state(state)
        except ui.Cancelled:
            if step == 0:
                return
            step = (2 if agent == 'qwen' else 1) if step == 3 else step - 1


@ui.scoped
def after_profile(name):
    start_flow(profile_name=name)


def favorite_title(entry):
    mode = 'Ligero' if entry.get('mode') == 'light' else 'Completo'
    label = entry.get('name') or f"{entry['agent']} · {entry['profile']}"
    return f"{label} · {entry['agent']} · {mode} · {entry['model']}"


def favorite_rows(entries):
    return {f"★ {i + 1}. {favorite_title(entry)}": entry for i, entry in enumerate(entries)}


def favorite_cards(entries, profiles):
    cards = {}
    for label, entry in favorite_rows(entries).items():
        profile = profiles.get(entry['profile'])
        if profile:
            context = f"Agente: {entry['agent']} · Modelo: {entry['model']}"
            if entry.get('dir'):
                context += f"\nCarpeta: {entry['dir']}"
            cards[label] = profile_card(entry['profile'], profile, context=context)
        else:
            cards[label] = {'title': label, 'detail': f"La API '{entry['profile']}' ya no existe."}
    return cards


def save_favorite(entry):
    state = load_state()
    identity = lambda e: (e['profile'], e['agent'], e['model'], e.get('mode', 'normal'))
    existing = next((e for e in state['favorites'] if identity(e) == identity(entry)), None)
    default = (existing or {}).get('name') or f"{entry['agent']} {'rápido' if entry.get('mode') == 'light' else 'trabajo'}"
    label = ask('Nombre del favorito (así aparecerá en el inicio)', entry.get('name') or default)
    if any(e.get('name') == label and e is not existing for e in state['favorites']):
        raise ValueError('Ya existe otro favorito con ese nombre. Elige un nombre diferente.')
    favorite = dict(entry, name=label)
    if existing is not None:
        state['favorites'][state['favorites'].index(existing)] = favorite
    else:
        state['favorites'].append(favorite)
    save_state(state)
    ui.message('Favorito guardado', f'{label}\nYa está visible en el inicio para abrirlo directamente.')


@ui.scoped
def add_favorite():
    profiles = load()
    step, name, agent, model, mode, label = 0, '', '', '', 'normal', ''
    while True:
        ui.set_context(profile=name, agent=agent, model=model, mode='Ligero' if mode == 'light' else 'Completo')
        try:
            if step == 0:
                new_name, profile = selected(profiles, None)
                if new_name != name:
                    name, model, agent, mode = new_name, profile['model'], '', 'normal'
            elif step == 1:
                compatible = [a for a, spec in agent_registry().items() if profiles[name]['protocol'] in spec['protocols']]
                agent = choose('Agente para este favorito', compatible, cards=agent_cards(compatible, name, profiles[name]))
            elif step == 2:
                if choose('Modelo del favorito', [f'Usar {model}', 'Elegir otro modelo']) == 'Elegir otro modelo':
                    model = pick_model(profiles[name])
            elif step == 3:
                mode = choose_qwen_mode(mode) if agent == 'qwen' else 'normal'
            else:
                entry = {'profile': name, 'agent': agent, 'model': model}
                if mode == 'light':
                    entry['mode'] = mode
                if label:
                    entry['name'] = label
                save_favorite(entry)
                return
            step += 1
        except ui.Cancelled as exc:
            if step == 4 and exc.value:
                label = exc.value
            if step == 0:
                raise
            step = 2 if step == 4 and agent != 'qwen' else step - 1


def favorites_menu():
    while True:
        state = load_state()
        rows = favorite_rows(state['favorites'])
        action = choose('Favoritos', list(rows) + ['Agregar favorito', 'Renombrar favorito', 'Eliminar favorito', 'Volver'],
                        cards=favorite_cards(state['favorites'], load()))
        if action == 'Volver':
            return
        try:
            if action == 'Agregar favorito':
                add_favorite()
            elif action in ('Eliminar favorito', 'Renombrar favorito'):
                if not rows:
                    ui.message('Favoritos', 'Todavía no hay favoritos.')
                    continue
                entry = rows[choose('Selecciona el favorito', list(rows))]
                if action == 'Renombrar favorito':
                    save_favorite(entry)
                else:
                    state['favorites'].remove(entry)
                    save_state(state)
            else:
                run_selection(rows[action])
        except ui.Cancelled:
            continue


@ui.scoped
def test_flow(name, profile):
    ui.set_context(profile=name, model=profile['model'])
    action = choose('Probar conexión', ['Consultar catálogo (GET /models)', 'Probar respuesta del modelo', 'Volver'])
    if action.startswith('Consultar'):
        check_connection(name, profile)
    elif action == 'Probar respuesta del modelo':
        if ui.confirm('¿Enviar una petición breve al modelo?', 'Se enviará «Reply OK.» con hasta 16 tokens de salida. El proveedor puede cobrar esta petición.'):
            check_connection(name, profile, inference=True)


@ui.scoped
def profile_actions(name):
    while True:
        profiles = load()
        if name not in profiles:
            return
        profile = profiles[name]
        ui.set_context(profile=name, model=profile['model'], agent='', mode='')
        detail = (f"Mi API · {name}\n"
                  f"Base URL: {profile['base_url']}\n"
                  f"Formato: {profile['protocol']} · Modelo: {profile['model']} · {key_status(profile)}\n"
                  f"Estado: {connection_status(name, profile)}")
        try:
            option = choose(detail, ['Abrir agente con esta API', 'Probar conexión', 'Cambiar modelo', 'Editar API', 'Eliminar perfil', 'Volver'])
        except ui.Cancelled:
            return
        try:
            if option == 'Volver':
                return
            if option == 'Abrir agente con esta API':
                start_flow(profile_name=name)
            elif option == 'Probar conexión':
                test_flow(name, profile)
            elif option == 'Cambiar modelo':
                profile['model'] = pick_model(profile)
                private_json(HOME / 'profiles.json', profiles)
                after_profile(name)
            elif option == 'Editar API':
                edit_profile(profiles, name, editing=True)
                after_profile(name)
            elif option == 'Eliminar perfil':
                remove(profiles, name)
        except ui.Cancelled:
            continue


def profiles_menu():
    while True:
        profiles = load()
        rows = profile_rows(profiles)
        action = choose('Mis APIs y modelos\nCada fila muestra formato, modelo y base URL.', 
                        list(rows) + ['Agregar API', 'Comprobar todas las APIs', 'Volver'],
                        cards=profile_cards(profiles))
        if action == 'Volver':
            return
        try:
            if action == 'Agregar API':
                name = edit_profile(profiles)
                after_profile(name)
            elif action == 'Comprobar todas las APIs':
                check_all()
            else:
                profile_actions(rows[action])
        except ui.Cancelled:
            continue


def install_agent(agent):
    return agent_catalog.install(agent, HOME, ui.confirm, resolve_agent)


@ui.scoped
def explore_agents(protocol=None, profile_name=None):
    while True:
        ui.set_context(agent='', mode='')
        registry = agent_registry()
        rows = {}
        for agent, spec in registry.items():
            if protocol and protocol not in spec['protocols']:
                continue
            if resolve_agent(spec['command']):
                status = '✓ Instalado'
            elif agent in agent_catalog.CATALOG:
                status = 'Requisitos pendientes' if agent_catalog.prerequisites(agent) else '↓ Disponible para instalar'
            else:
                status = 'No instalado · instalación manual'
            rows[f"{agent} · {status} · {' / '.join(spec['protocols'])}"] = agent
        profile = load().get(profile_name) if profile_name else None
        if profile:
            ui.set_context(profile=profile_name, model=profile['model'])
        cards = agent_cards(rows.values(), profile_name, profile)
        option = choose('Explorar e instalar agentes', list(rows) + ['Registrar otro agente', 'Volver'],
                        cards={label: cards[agent] for label, agent in rows.items()})
        if option == 'Volver':
            return
        if option == 'Registrar otro agente':
            register_agent()
            continue
        agent = rows[option]
        ui.set_context(agent=agent)
        item = agent_catalog.CATALOG.get(agent)
        installed = resolve_agent(registry[agent]['command'])
        detail = item['description'] + '\n' + item['url'] if item else 'Adaptador personalizado.'
        if not installed and item:
            missing = agent_catalog.prerequisites(agent)
            detail += '\n' + ('\n'.join(missing) if missing else 'Requisitos disponibles: listo para instalar.')
        actions = ['Configurar con mi API y abrir'] if installed else (['Instalar'] if item else [])
        try:
            action = choose(f"{agent}\n{detail}", actions + ['Volver'])
            if action == 'Configurar con mi API y abrir':
                start_flow(agent, profile_name)
            elif action == 'Instalar' and install_agent(agent):
                if ui.confirm('Instalado. ¿Configurar con mi API y abrir?'):
                    start_flow(agent, profile_name)
        except ui.Cancelled:
            continue


def settings_menu():
    option = choose('Ajustes', ['Interfaz con flechas', 'Interfaz de texto (números)', 'Repetir bienvenida', 'Diagnóstico', 'Volver'])
    state = load_state()
    if option.startswith('Interfaz'):
        state['plain'] = option.startswith('Interfaz de texto')
        save_state(state)
        ui.PLAIN = state['plain']
    elif option == 'Repetir bienvenida':
        onboarding()
    elif option == 'Diagnóstico':
        lines = [f"Datos: {HOME}", f"Directorio de trabajo: {Path.cwd()}"]
        lines.extend(f"{a}: {resolve_agent(s['command']) or 'no instalado'}" for a, s in agent_registry().items())
        ui.message('Diagnóstico', '\n'.join(lines))


def onboarding():
    action = choose('◆ Bienvenido a ModelHub\n1. Guarda tu API · 2. Elige agente · 3. Trabaja en tu proyecto.\nFlechas + Enter · Esc vuelve · escribe para buscar.', ['Crear mi primera API', 'Explorar agentes', 'Ir al inicio'])
    state = load_state()
    state['welcomed'] = True
    save_state(state)
    if action == 'Crear mi primera API':
        name = edit_profile(load())
        after_profile(name)
    elif action == 'Explorar agentes':
        explore_agents()


def menu():
    if not load_state()['welcomed']:
        try:
            onboarding()
        except ui.Cancelled:
            state = load_state()
            state['welcomed'] = True
            save_state(state)
        except (ValueError, OSError) as exc:
            ui.message('No se pudo completar la bienvenida', str(exc))
    while True:
        ui.CONTEXT.clear()
        state = load_state()
        profiles = load()
        last = state['last']
        quick = None
        if last and last['profile'] in profiles and last['agent'] in agent_registry():
            quick = f"Volver a usar {last['agent']} · {last['profile']} · {last['model']}"
            if last.get('mode') == 'light':
                quick += ' · ligero'
        favorites = favorite_rows(state['favorites'])
        options = ['Empezar a trabajar'] + ([quick] if quick else []) + list(favorites) + ['Favoritos',
                   'Mis APIs y modelos', 'Explorar e instalar agentes', 'Ajustes', 'Salir']
        cards = favorite_cards(state['favorites'], profiles)
        if quick:
            context = f"Agente: {last['agent']} · Modelo: {last['model']}"
            if last.get('dir'):
                context += f"\nCarpeta: {last['dir']}"
            cards[quick] = profile_card(last['profile'], profiles[last['profile']], context=context)
        try:
            next_step = '◆ API → agente → abrir.'
            action = choose(f"◆ Inicio · {len(profiles)} APIs · {len(installed_agents())} agentes\n{next_step}",
                            options, cards=cards)
        except ui.Cancelled:
            return 0
        try:
            if action == 'Salir':
                return 0
            if action == quick:
                run_selection(last)
            elif action == 'Empezar a trabajar':
                start_flow()
            elif action in favorites:
                run_selection(favorites[action])
            elif action == 'Favoritos':
                favorites_menu()
            elif action == 'Mis APIs y modelos':
                profiles_menu()
            elif action == 'Explorar e instalar agentes':
                explore_agents()
            elif action == 'Ajustes':
                settings_menu()
        except ui.Cancelled:
            continue
        except (ValueError, OSError) as exc:
            ui.message('No se pudo completar la acción', str(exc))


def listing(profiles):
    if not profiles:
        print("No hay perfiles. Usa: modelhub add")
    for name, profile in profiles.items():
        print(f"{name}: {profile['protocol']} · {profile['base_url']} · {profile['model']} · "
              f"{key_status(profile)} · {connection_status(name, profile)}")


def remove(profiles, name):
    if ui.confirm(f"¿Eliminar '{name}' y su estado de agentes?", 'También se eliminarán sus favoritos y el historial guardado dentro de este perfil.'):
        del profiles[name]
        private_json(HOME / "profiles.json", profiles)
        runtime = HOME / "agents" / name
        if runtime.exists():
            shutil.rmtree(runtime)
        state = load_state()
        state['favorites'] = [entry for entry in state['favorites'] if entry['profile'] != name]
        if state['last'] and state['last']['profile'] == name:
            state['last'] = None
        state['checks'].pop(name, None)
        save_state(state)
        print("Perfil eliminado.")


def main():
    parser = argparse.ArgumentParser(description="ModelHub: guarda APIs, consulta modelos y elige tu agente.")
    parser.add_argument('--version', action='version', version='ModelHub ' + VERSION)
    parser.add_argument('--plain', action='store_true', help='Usar menús de texto sin pantalla interactiva')
    sub = parser.add_subparsers(dest="command")
    for command in ("add", "edit", "models", "remove"):
        sub.add_parser(command).add_argument("profile", nargs="?")
    sub.add_parser("list")
    sub.add_parser("doctor")
    sub.add_parser("agents", help="Detectar agentes instalados y listar compatibilidad")
    sub.add_parser("register-agent", help="Añadir un adaptador basado en variables de entorno")
    sub.add_parser('explore', help='Explorar e instalar agentes')
    sub.add_parser('favorites', help='Abrir favoritos')
    sub.add_parser('last', help='Volver a usar el último agente, API y modelo')
    install = sub.add_parser('install', help='Instalar un agente del catálogo oficial')
    install.add_argument('agent', choices=agent_catalog.CATALOG)
    check = sub.add_parser('check', help='Comprobar un catálogo o respuesta del modelo')
    check.add_argument('profile', nargs='?')
    check.add_argument('--inference', action='store_true', help='Enviar una petición breve (puede tener coste)')
    run = sub.add_parser("run")
    run.add_argument("agent", help="Agente integrado o registrado; consulta 'modelhub agents'")
    run.add_argument("profile", nargs="?")
    run.add_argument('--light', action='store_true', help='Qwen: prompt corto, sin personalizaciones, aprobaciones normales')
    run.add_argument('--dir', action='append', default=[],
                     help='Carpeta de proyecto; repite --dir para dar acceso a varios proyectos en Claude Code. Por defecto, la carpeta actual.')
    args, extra = parser.parse_known_args()
    ui.PLAIN = args.plain or load_state()['plain'] or os.environ.get('MODELHUB_PLAIN') == '1'
    if extra and args.command != "run":
        parser.error("Argumentos no reconocidos: " + " ".join(extra))
    if not args.command:
        return menu()
    if args.command == "doctor":
        doctor()
        return 0
    if args.command == "agents":
        list_agents()
        return 0
    if args.command == "register-agent":
        register_agent()
        return 0
    if args.command == 'explore':
        explore_agents()
        return 0
    if args.command == 'favorites':
        favorites_menu()
        return 0
    if args.command == 'install':
        return 0 if install_agent(args.agent) else 1
    if args.command == 'last':
        last = load_state()['last']
        if not last:
            raise ValueError('Todavía no has iniciado un agente desde ModelHub.')
        return run_selection(last)
    profiles = load()
    if args.command == "list":
        listing(profiles)
    elif args.command == "add":
        edit_profile(profiles, args.profile)
    else:
        name, profile = selected(profiles, args.profile)
        if args.command == "edit":
            edit_profile(profiles, name, editing=True)
        elif args.command == "models":
            profile["model"] = pick_model(profile)
            private_json(HOME / "profiles.json", profiles)
        elif args.command == "remove":
            remove(profiles, name)
        elif args.command == "run":
            return launch(name, profile, args.agent, extra[1:] if extra[:1] == ["--"] else extra,
                          mode='light' if args.light else 'normal', directory=args.dir)
        elif args.command == 'check':
            if args.inference and not ui.confirm('¿Enviar una petición breve al modelo?', 'Se enviará «Reply OK.» con hasta 16 tokens de salida. Puede tener coste.'):
                return 1
            return 0 if check_connection(name, profile, args.inference) else 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError, ui.Cancelled):
        print("\nCancelado.")
        sys.exit(130)
    except (ValueError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
