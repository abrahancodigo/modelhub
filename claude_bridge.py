"""Traduce Anthropic Messages a OpenAI Chat Completions para Claude Code.

Claude Code solo habla la API Anthropic Messages. Los perfiles ModelHub con
protocolo `openai` necesitan un puente local que reescriba peticiones y
respuestas, incluido el streaming SSE y el uso de herramientas en ambos
sentidos. Este modulo no tiene dependencias externas: usa solo la biblioteca
estandar, igual que el resto de ModelHub.

Flujo: Claude Code -> 127.0.0.1:<puerto>/v1/messages -> traductor ->
proveedor OpenAI (/chat/completions).
"""
import http.server
import json
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request

MAX_BODY = 64 * 1024 * 1024

STOP_REASONS = {'tool_calls': 'tool_use', 'function_call': 'tool_use',
                'length': 'max_tokens', 'stop': 'end_turn', 'content_filter': 'end_turn'}


def _json_string(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _tool_arguments(raw):
    """OpenAI devuelve los argumentos como texto JSON; Anthropic exige un objeto."""
    try:
        value = json.loads(raw or '{}')
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _tool_name(call):
    function = call.get('function') or {}
    name = function.get('name') or call.get('name')
    return name if isinstance(name, str) and name else None


def _tool_block(call):
    name = _tool_name(call)
    arguments = _tool_arguments((call.get('function') or {}).get('arguments'))
    if not name or arguments is None:
        return None
    return {'type': 'tool_use', 'id': call.get('id') or ('toolu_' + name), 'name': name, 'input': arguments}


def _tool_result_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [item['text'] for item in content
                 if isinstance(item, dict) and item.get('type') == 'text' and isinstance(item.get('text'), str)]
        return '\n'.join(parts) if parts else _json_string(content)
    return _json_string(content)


# ---------------------------------------------------------------- peticion

def convert_messages(payload):
    """Anthropic Messages -> lista de mensajes OpenAI Chat Completions."""
    instructions = []
    system = payload.get('system')
    if isinstance(system, str):
        instructions.append(system)
    elif isinstance(system, list):
        instructions.extend(item['text'] for item in system
                            if isinstance(item, dict) and isinstance(item.get('text'), str))
    messages = [{'role': 'system', 'content': '\n\n'.join(instructions)}] if instructions else []
    for message in payload.get('messages') or []:
        if not isinstance(message, dict):
            continue
        content = message.get('content')
        role = message.get('role') if message.get('role') in ('user', 'assistant') else 'user'
        if isinstance(content, str):
            messages.append({'role': role, 'content': content})
            continue
        texts, calls, results = [], [], []
        for block in content or []:
            if not isinstance(block, dict):
                continue
            kind = block.get('type')
            if kind == 'text':
                texts.append(block.get('text') or '')
            elif kind == 'tool_use':
                arguments = block.get('input')
                calls.append({'id': block.get('id') or 'toolu', 'type': 'function',
                              'function': {'name': block.get('name') or '',
                                           'arguments': _json_string(arguments if isinstance(arguments, dict) else {})}})
            elif kind == 'tool_result':
                results.append({'role': 'tool', 'tool_call_id': block.get('tool_use_id') or 'toolu',
                                'content': _tool_result_content(block.get('content'))})
        text = '\n'.join(part for part in texts if part)
        if calls:
            # OpenAI exige una unica respuesta assistant con su lista completa de
            # tool_calls: el turno de Claude Code se reconstruye en un solo mensaje.
            # El texto previo del assistant se conserva: OpenAI admite content
            # junto a tool_calls y perderlo degradaría el contexto del proveedor.
            messages.append({'role': 'assistant', 'content': text or None, 'tool_calls': calls})
            messages.extend(results)
        elif results:
            # Los mensajes role tool deben ir justo despues del assistant con
            # tool_calls; el texto que Anthropic adjunta al mismo turno de usuario
            # se conserva despues para no perder instrucciones.
            messages.extend(results)
            if text:
                messages.append({'role': role, 'content': text})
        elif text:
            messages.append({'role': role, 'content': text})
    return messages



def convert_tools(tools):
    """Anthropic tools -> funciones OpenAI; descarta las herramientas de servidor."""
    converted = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        name = tool.get('name')
        if not isinstance(name, str) or not name or name == 'thinking':
            continue
        schema = tool.get('input_schema')
        parameters = schema if isinstance(schema, dict) and schema else {'type': 'object', 'properties': {}}
        converted.append({'type': 'function', 'function': {
            'name': name, 'description': tool.get('description') or '', 'parameters': parameters}})
    return converted


def convert_request(payload):
    """Peticion /v1/messages -> cuerpo /chat/completions y estado del turno."""
    if not isinstance(payload, dict):
        raise ValueError('La peticion no es un objeto JSON.')
    messages = convert_messages(payload)
    if not messages or all(message['role'] == 'system' for message in messages):
        messages.append({'role': 'user', 'content': ''})
    body = {'model': payload.get('model') or '', 'messages': messages}
    if isinstance(payload.get('max_tokens'), int) and payload['max_tokens'] > 0:
        body['max_tokens'] = payload['max_tokens']
    for field in ('temperature', 'top_p'):
        if isinstance(payload.get(field), (int, float)) and not isinstance(payload.get(field), bool):
            body[field] = payload[field]
    if isinstance(payload.get('stop_sequences'), list):
        body['stop'] = payload['stop_sequences']
    tools = convert_tools(payload.get('tools'))
    if tools:
        body['tools'] = tools
    choice = payload.get('tool_choice')
    if isinstance(choice, dict):
        kind = choice.get('type')
        if kind == 'auto':
            body['tool_choice'] = 'auto'
        elif kind == 'any':
            body['tool_choice'] = 'required'
        elif kind == 'none':
            body['tool_choice'] = 'none'
        elif kind == 'tool':
            body['tool_choice'] = {'type': 'function', 'function': {'name': choice.get('name') or ''}}
    # Los ids Anthropic (toolu_*) son opacos para Claude Code: los reutilizamos
    # para devolver el bloque thinking y el mismo id en los tool_use del turno.
    # Claude Code trata los ids toolu_* como opacos: los generamos aqui y los
    # reutilizamos al responder para devolver exactamente los mismos ids y el
    # bloque thinking con su firma. Reenviamos el primero que ya exista.
    signature, ids = None, []
    for message in payload.get('messages') or []:
        for block in (message.get('content') if isinstance(message, dict) else None) or []:
            if not isinstance(block, dict):
                continue
            # Los bloques thinking de Anthropic no son tool_use: su firma viaja en
            # el campo `signature` y hay que reenviarla en la respuesta.
            if block.get('type') == 'thinking' and signature is None:
                value = block.get('signature')
                signature = value if isinstance(value, str) and value else 'thinking'
            elif block.get('type') == 'tool_use' and isinstance(block.get('id'), str):
                ids.append(block['id'])
    if not ids:
        ids = ['toolu_1', 'toolu_2', 'toolu_3', 'toolu_4', 'toolu_5']
    return body, iter(ids), signature



# ---------------------------------------------------------------- respuesta

def _usage(usage):
    usage = usage or {}
    return {'input_tokens': usage.get('prompt_tokens') or 0,
            'output_tokens': usage.get('completion_tokens') or 0,
            'cache_read_input_tokens': (usage.get('prompt_tokens_details') or {}).get('cached_tokens') or 0,
            'cache_creation_input_tokens': 0}


def _stop_reason(reason, tools):
    if tools and (reason in (None, '', 'stop')):
        return 'tool_use'
    return STOP_REASONS.get(reason or 'stop', 'end_turn')


def _blocks(message):
    blocks = []
    if isinstance(message.get('reasoning_content'), str) and message['reasoning_content']:
        blocks.append({'type': 'thinking', 'thinking': message['reasoning_content'], 'signature': 'thinking'})
    text = message.get('content')
    if isinstance(text, str) and text:
        blocks.append({'type': 'text', 'text': text})
    for call in message.get('tool_calls') or []:
        if isinstance(call, dict):
            block = _tool_block(call)
            if block:
                blocks.append(block)
    return blocks


def convert_response(payload, ids=(), signature=None):
    """Respuesta Chat Completions -> mensaje Anthropic Messages."""
    if not isinstance(payload, dict):
        raise ValueError('La respuesta no es un objeto JSON.')
    choices = payload.get('choices') or []
    if not choices or not isinstance(choices[0], dict):
        raise ValueError('El proveedor no devolvio ninguna eleccion.')
    choice = choices[0]
    blocks = _blocks(choice.get('message') or {})
    content = []
    for block in blocks:
        if block['type'] == 'thinking':
            content.append({**block, 'signature': signature or block['signature']})
        elif block['type'] == 'tool_use':
            content.append({**block, 'id': next(ids, None) or block['id']})
        else:
            content.append(block)
    if not content:
        content = [{'type': 'text', 'text': ''}]
    return {'id': 'msg_' + str(payload.get('id') or 'bridge'), 'type': 'message', 'role': 'assistant',
            'model': payload.get('model') or '', 'content': content,
            'stop_reason': _stop_reason(choice.get('finish_reason'),
                                        [block for block in content if block['type'] == 'tool_use']),
            'stop_sequence': None, 'usage': _usage(payload.get('usage'))}


# ---------------------------------------------------------------- streaming

def _event(name, data):
    return ('event: ' + name + '\ndata: ' + _json_string(data) + '\n\n').encode()


class Stream:
    """Acumula fragmentos OpenAI y emite el flujo SSE que Claude Code espera."""

    def __init__(self, model, ids=(), signature=None):
        self.model = model
        self.ids = iter(ids)
        self.signature = signature
        self.kinds = {}
        self.closed = {}
        self.tools = {}
        self.text_index = None
        self.reasoning_index = None
        self.finish = None
        self.usage = {}
        self.started = False

    def start(self):
        self.started = True
        message = {'id': 'msg_bridge', 'type': 'message', 'role': 'assistant', 'model': self.model,
                   'content': [], 'stop_reason': None, 'stop_sequence': None, 'usage': _usage(None)}
        return _event('message_start', {'type': 'message_start', 'message': message})

    def _open_block(self, kind, payload):
        index = len(self.kinds)
        self.kinds[index] = kind
        self.closed[index] = False
        return index, _event('content_block_start', {'type': 'content_block_start', 'index': index,
                                                     'content_block': payload})

    def _close(self, index):
        if index is None or self.closed.get(index, True):
            return b''
        self.closed[index] = True
        return _event('content_block_stop', {'type': 'content_block_stop', 'index': index})

    def feed(self, chunk):
        if not isinstance(chunk, dict):
            return b''
        output = self.start() if not self.started else b''
        usage = chunk.get('usage')
        if isinstance(usage, dict):
            self.usage.update(usage)
        choices = chunk.get('choices') or []
        if not choices or not isinstance(choices[0], dict):
            return output
        choice = choices[0]
        if choice.get('finish_reason'):
            self.finish = choice['finish_reason']
        delta = choice.get('delta') or {}
        reasoning = delta.get('reasoning_content')
        if isinstance(reasoning, str) and reasoning:
            # El proveedor puede intercalar razonamiento después del texto: en
            # ese caso hay que abrir un bloque nuevo, porque pintar sobre un
            # índice ya cerrado (content_block_stop) rompe el protocolo.
            if self.reasoning_index is None or self.closed.get(self.reasoning_index):
                self.reasoning_index, start = self._open_block('thinking', {'type': 'thinking', 'thinking': ''})
                output += start
            output += _event('content_block_delta', {
                'type': 'content_block_delta', 'index': self.reasoning_index,
                'delta': {'type': 'thinking_delta', 'thinking': reasoning}})
        text = delta.get('content')
        if isinstance(text, str) and text:
            output += self._close(self.reasoning_index)
            if self.text_index is None or self.closed.get(self.text_index):
                self.text_index, start = self._open_block('text', {'type': 'text', 'text': ''})
                output += start
            output += _event('content_block_delta', {
                'type': 'content_block_delta', 'index': self.text_index,
                'delta': {'type': 'text_delta', 'text': text}})
        for call in delta.get('tool_calls') or []:
            if isinstance(call, dict):
                output += self._tool_delta(call)
        return output

    def _tool_delta(self, call):
        # El indice de tool_calls de OpenAI es solo suyo: hay que mapearlo al
        # indice real del bloque Anthropic, que es compartido con texto y thinking.
        output = b''
        position = call.get('index') if isinstance(call.get('index'), int) else 0
        function = call.get('function') or {}
        if position not in self.tools:
            output += self._close(self.text_index) + self._close(self.reasoning_index)
            raw = call.get('id') if isinstance(call.get('id'), str) else ''
            identifier = next(self.ids, None) or (raw if raw.startswith('toolu_') else 'toolu_' + raw)
            index, start = self._open_block('tool', {'type': 'tool_use', 'id': identifier,
                                                     'name': function.get('name') or '', 'input': {}})
            self.tools[position] = index
            output += start
        else:
            index = self.tools[position]
        # El proveedor puede mandar el nombre y los argumentos en el mismo chunk
        # que abre la herramienta, o repartir los argumentos en varios.
        arguments = function.get('arguments')
        if isinstance(arguments, str) and arguments:
            output += _event('content_block_delta', {'type': 'content_block_delta', 'index': index,
                                                     'delta': {'type': 'input_json_delta',
                                                               'partial_json': arguments}})
        return output

    def close(self):
        output = self.start() if not self.started else b''
        for index in sorted(self.kinds):
            output += self._close(index)
        tools = [key for key, kind in self.kinds.items() if kind == 'tool']
        output += _event('message_delta', {'type': 'message_delta',
                                           'delta': {'stop_reason': _stop_reason(self.finish, tools),
                                                     'stop_sequence': None},
                                           'usage': _usage(self.usage)})
        output += _event('message_stop', {'type': 'message_stop'})
        return output


# ---------------------------------------------------------------- servidor

class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'ModelHubBridge'
    sys_version = ''

    def log_message(self, *args):
        pass

    def _path(self):
        return urllib.parse.urlsplit(self.path).path.rstrip('/') or '/'

    def _reply(self, status, payload):
        body = json.dumps(payload).encode()
        try:
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _error(self, exc):
        if isinstance(exc, urllib.error.HTTPError):
            status = exc.code
            try:
                detail = exc.read()[:400].decode(errors='replace')
            except OSError:
                detail = ''
            finally:
                exc.close()
            kind = 'authentication_error' if status in (401, 403) else 'api_error'
            if status == 429:
                kind = 'rate_limit_error'
            message = ('El proveedor respondio HTTP %d. Revisa el modelo y la clave. %s' % (status, detail)).strip()
        elif isinstance(exc, (urllib.error.URLError, TimeoutError, OSError)):
            status, kind, message = 502, 'api_error', 'No se pudo conectar con el proveedor (red, TLS o tiempo de espera).'
        else:
            status, kind, message = 500, 'api_error', 'Error del traductor: ' + type(exc).__name__ + '.'
        self._reply(status, {'type': 'error', 'error': {'type': kind, 'message': message}})

    def do_GET(self):
        path, model = self._path(), self.server.model
        if path == '/v1/models':
            self._reply(200, {'data': [{'id': model, 'type': 'model', 'display_name': model,
                                        'created_at': '2026-01-01T00:00:00Z'}],
                              'first_id': model, 'last_id': model, 'has_more': False})
        elif path == '/v1/messages/count_tokens':
            self._reply(200, {'input_tokens': self.server.counter})
        else:
            self._reply(404, {'type': 'error', 'error': {'type': 'not_found_error', 'message': 'Ruta desconocida.'}})

    def do_POST(self):
        if self._path() != '/v1/messages':
            self._reply(404, {'type': 'error', 'error': {'type': 'not_found_error', 'message': 'Ruta desconocida.'}})
            return
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            self._reply(413, {'type': 'error', 'error': {'type': 'invalid_request_error',
                                                         'message': 'Cuerpo no valido.'}})
            return
        try:
            payload = json.loads(self.rfile.read(length))
        except ValueError:
            self._reply(400, {'type': 'error', 'error': {'type': 'invalid_request_error',
                                                         'message': 'JSON no valido.'}})
            return
        if not isinstance(payload, dict) or not isinstance(payload.get('messages'), list):
            self._reply(400, {'type': 'error', 'error': {'type': 'invalid_request_error',
                                                         'message': 'Faltan mensajes o modelo.'}})
            return
        payload['model'] = self.server.model
        try:
            body, ids, signature = convert_request(payload)
        except (ValueError, TypeError) as exc:
            self._reply(400, {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': str(exc)}})
            return
        if signature and not self.server.signature:
            with self.server.lock:
                self.server.signature = self.server.signature or signature
        stream = bool(payload.get('stream'))
        body['stream'] = stream
        try:
            if stream:
                self._stream(body, ids)
            else:
                self._once(body, ids)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _provider(self, body, stream=True):
        # En modo no-streaming hay que pedir JSON completo: si el cuerpo lleva
        # stream=true el proveedor devuelve SSE y json.load falla.
        payload = dict(body)
        payload['stream'] = bool(stream)
        request = urllib.request.Request(
            self.server.base_url + '/chat/completions', data=json.dumps(payload).encode(),
            headers={'Content-Type': 'application/json', 'Accept': 'application/json',
                     'Authorization': 'Bearer ' + self.server.api_key})
        return urllib.request.build_opener().open(request, timeout=self.server.timeout)

    def _once(self, body, ids):
        try:
            with self._provider(body, stream=False) as response:
                payload = json.load(response)
            message = convert_response(payload, ids, self.server.signature)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError, TypeError) as exc:
            self._error(exc)
            return
        self._reply(200, message)


    def _stream(self, body, ids):
        try:
            response = self._provider(body)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
            self._error(exc)
            return
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.send_header('Transfer-Encoding', 'chunked')
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError, OSError):
            response.close()
            return
        stream = Stream(self.server.model, ids, self.server.signature)
        try:
            self._write(stream.start())
            for line in response:
                line = line.strip()
                if not line.startswith(b'data:'):
                    continue
                raw = line[5:].strip()
                if raw == b'[DONE]':
                    break
                try:
                    chunk = json.loads(raw)
                except ValueError:
                    continue
                self._write(stream.feed(chunk))
            self._write(stream.close())
        except (BrokenPipeError, ConnectionResetError, urllib.error.URLError, TimeoutError, OSError):
            pass
        finally:
            response.close()
            self._end()

    def _end(self):
        try:
            self.wfile.write(b'0\r\n\r\n')
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _write(self, data):
        if not data:
            return
        self.wfile.write(('%x\r\n' % len(data)).encode() + data + b'\r\n')
        self.wfile.flush()


class Bridge(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port, base_url, api_key, model, timeout=600.0, counter=0):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key or 'not-required'
        self.model = model
        self.timeout = timeout
        self.counter = counter
        # Este token solo vale para el bucle local: si algo redirige el trafico,
        # no sirve fuera de esta sesion ni revela la clave real del proveedor.
        self.server_token = secrets.token_urlsafe(32)
        self.signature = None
        self.lock = threading.Lock()
        super().__init__(('127.0.0.1', port), Handler)



def start(profile, counter=0):
    """Escucha solo en 127.0.0.1 con un puerto libre y devuelve el puente en marcha."""
    bridge = Bridge(0, profile['base_url'], profile['api_key'], profile['model'], counter=counter)
    thread = threading.Thread(target=bridge.serve_forever, name='modelhub-claude-bridge', daemon=True)
    thread.start()
    return bridge
