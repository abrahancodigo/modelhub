"""Official npm installation recipes. No shell interpolation or sudo."""
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess


CATALOG = {
    'opencode': {
        'name': 'OpenCode', 'description': 'Agente de programación con múltiples proveedores.',
        'package': '@opencode/cli', 'node': (20, 0, 0),
        'url': 'https://opencode.ai/v2/docs/',
    },
    'pi': {
        'name': 'Pi', 'description': 'Terminal ligera y extensible para trabajar con tus modelos.',
        'package': '@earendil-works/pi-coding-agent', 'node': (22, 19, 0),
        'url': 'https://pi.dev', 'ignore_scripts': True,
    },
    'qwen': {
        'name': 'Qwen Code', 'description': 'Agente abierto; integración ModelHub con APIs OpenAI.',
        'package': '@qwen-code/qwen-code@latest', 'node': (22, 0, 0),
        'url': 'https://github.com/QwenLM/qwen-code#installation',
    },
    'cline': {
        'name': 'Cline', 'description': 'Asistente con interfaz de terminal y herramientas de código.',
        'package': 'cline', 'node': (20, 0, 0),
        'url': 'https://docs.cline.bot/getting-started/installing-cline',
    },
    'claude': {
        'name': 'Claude Code', 'description': 'CLI de Anthropic; perfil Anthropic directo o OpenAI mediante puente local.',
        'package': '@anthropic-ai/claude-code', 'node': (22, 0, 0),
        'url': 'https://code.claude.com/docs/en/setup',
    },
    'codex': {
        'name': 'Codex', 'description': 'CLI de OpenAI; necesita un endpoint con API Responses (/responses).',
        'package': '@openai/codex', 'node': (20, 0, 0),
        'url': 'https://developers.openai.com/codex/',
    },
    'kimi': {
        'name': 'Kimi Code', 'description': 'Agente de terminal de Moonshot; admite APIs OpenAI y Anthropic.',
        'package': '@moonshot-ai/kimi-code', 'node': (22, 19, 0),
        'url': 'https://github.com/MoonshotAI/kimi-code',
    },
}


def prerequisites(agent):
    item = CATALOG[agent]
    missing = []
    if os.name != 'posix':
        missing.append('La instalación integrada está disponible en Linux, macOS y WSL.')
    if not shutil.which('npm'):
        missing.append('Falta npm en PATH.')
    minimum = '.'.join(map(str, item['node']))
    node = shutil.which('node')
    if not node:
        missing.append(f'Falta Node.js >= {minimum}.')
    else:
        try:
            result = subprocess.run([node, '--version'], capture_output=True, text=True, timeout=5)
            match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?', result.stdout.strip())
            if result.returncode or not match or tuple(map(int, match.groups())) < item['node']:
                missing.append(f'Se necesita Node.js >= {minimum}.')
        except (OSError, subprocess.TimeoutExpired):
            missing.append('No se pudo comprobar Node.js.')
    return missing


def install_command(agent, home):
    item = CATALOG[agent]
    command = ['npm', 'install', '--global', '--prefix', str(home / 'tools')]
    if item.get('ignore_scripts'):
        command.append('--ignore-scripts')
    return command + [item['package']]


def install(agent, home, confirm, resolve):
    if agent not in CATALOG:
        raise ValueError('Este agente no tiene una receta de instalación en el catálogo.')
    if resolve(agent):
        print(f'{agent} ya está instalado.')
        return True
    missing = prerequisites(agent)
    if missing:
        raise ValueError('\n'.join(missing) + '\nInstala Node.js desde https://nodejs.org/en/download y vuelve a intentarlo.')
    command = install_command(agent, home)
    detail = (f"Fuente: {CATALOG[agent]['url']}\n\nComando:\n{shlex.join(command)}\n\n"
              f"Destino: {home / 'tools'}\nSe instalará solo este agente. npm mostrará el progreso.")
    if not confirm(f'¿Instalar {CATALOG[agent]["name"]}?', detail):
        return False
    print(f'Instalando {agent}… descarga y preparación en curso.', flush=True)
    try:
        result = subprocess.run(command, timeout=900)
    except subprocess.TimeoutExpired:
        raise ValueError('La instalación superó 15 minutos. Comprueba la red y vuelve a intentarlo.') from None
    if result.returncode:
        raise ValueError(f'La instalación falló (código {result.returncode}). Revisa la salida de npm; puedes reintentar desde el catálogo.')
    if not resolve(agent):
        raise ValueError('npm terminó, pero el ejecutable no apareció. Revisa la salida y el directorio tools/bin.')
    print(f'{agent} instalado y detectado.')
    return True
