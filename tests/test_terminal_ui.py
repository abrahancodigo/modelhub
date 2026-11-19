import fcntl
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest
from unittest.mock import patch

import terminal_ui as ui


class TerminalTests(unittest.TestCase):
    def terminal_run(self, code, keys, ready=b'MODELHUB'):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 100, 0, 0))
        before = termios.tcgetattr(slave)
        env = dict(os.environ, TERM='xterm-256color')
        process = subprocess.Popen([sys.executable, '-c', code], stdin=slave, stdout=slave, stderr=slave,
                                   cwd=Path(__file__).resolve().parents[1], env=env)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        data = b''
        deadline = time.monotonic() + 8
        sent = False
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                data += os.read(master, 65536)
            if ready in data and not sent:
                os.write(master, keys)
                sent = True
            if process.poll() is not None:
                while select.select([master], [], [], 0)[0]:
                    data += os.read(master, 65536)
                break
        else:
            process.kill()
            process.wait()
            self.fail('La interfaz no respondió a las teclas: ' + repr(data[-1000:]))
        self.assertEqual(process.returncode, 0, data.decode(errors='replace'))
        self.assertEqual(termios.tcgetattr(slave), before, 'El terminal no recuperó su estado')
        return data

    def test_search_as_you_type_and_enter_in_real_terminal(self):
        data = self.terminal_run("import terminal_ui as u; print('RESULT=' + u.select('Modelos', ['alpha', 'beta', 'gamma']))", b'bet\r')
        self.assertIn(b'RESULT=beta', data)

    def test_multiline_step_and_next_action_are_visible(self):
        code = "import terminal_ui as u; u.select('Paso 3/3: listo\\nAPI: work\\nSelecciona Abrir para comenzar.', ['Abrir', 'Volver'])"
        data = self.terminal_run(code, b'\r')
        self.assertIn(b'API: work', data)
        self.assertIn(b'Selecciona Abrir para comenzar.', data)

    def test_card_and_persistent_context_are_visible(self):
        code = "import terminal_ui as u; u.set_context(profile='work', agent='qwen', mode='Ligero'); u.select('Agentes', ['qwen'], cards={'qwen': {'title': 'Qwen Code - Instalado', 'detail': 'API: work\\nModelo: coder'}})"
        data = self.terminal_run(code, b'\r')
        self.assertIn(b'Qwen Code - Instalado', data)
        self.assertIn(b'Modo: Ligero', data)
        self.assertIn(b'Modelo: coder', data)

    def test_message_scroll_reaches_end_with_context_banner(self):
        code = "import terminal_ui as u; u.set_context(profile='work', agent='qwen', mode='Ligero'); u.message('Detalle', '\\n'.join(['line ' + str(i) for i in range(40)] + ['LAST-SENTINEL']))"
        data = self.terminal_run(code, b'\x1bOB' * 45 + b'\r')
        self.assertIn(b'LAST-SENTINEL', data)

    def test_prompt_escape_retains_partial_text_without_leaking_secret(self):
        code = "import terminal_ui as u\ntry: u.prompt('Clave', secret=True)\nexcept u.Cancelled as e:\n assert e.value == 'hidden-key'\n print('SAVED-DRAFT')"
        data = self.terminal_run(code, b'hidden-key\x1b')
        self.assertIn(b'SAVED-DRAFT', data)
        self.assertNotIn(b'hidden-key', data)

    def test_folder_picker_navigates_and_returns_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / 'project'
            project.mkdir()
            code = (f"import terminal_ui as u; "
                    f"print('RESULT=' + str(u.pick_directory('Carpeta', start={tmp!r})))")
            data = self.terminal_run(code, b'\x1bOB\x1bOB\r\r')
            expected = ('RESULT=' + str(project.resolve())).encode()
        self.assertIn(expected, data)

    def test_status_colors_have_text_and_context_is_restored(self):
        self.assertEqual(ui.tone('No instalado'), 'pending')
        self.assertEqual(ui.tone('Falló la comprobación'), 'error')
        self.assertEqual(ui.tone('Modelo respondió'), 'success')
        with patch.dict(ui.CONTEXT, {'profile': 'outer'}, clear=True):
            @ui.scoped
            def inner():
                ui.set_context(profile='inner')
                raise ui.Cancelled()
            with self.assertRaises(ui.Cancelled):
                inner()
            self.assertEqual(ui.CONTEXT, {'profile': 'outer'})

    def test_arrow_navigation_in_real_terminal(self):
        data = self.terminal_run("import terminal_ui as u; print('RESULT=' + u.select('Agentes', ['alpha', 'beta']))", b'\x1bOB\r')
        self.assertIn(b'RESULT=beta', data)

    def test_escape_cancels_and_restores_terminal(self):
        code = "import terminal_ui as u\ntry: u.select('Agentes', ['alpha'])\nexcept u.Cancelled: print('CANCELLED')"
        self.assertIn(b'CANCELLED', self.terminal_run(code, b'\x1b'))

    def test_secret_input_is_masked(self):
        code = "import terminal_ui as u\nassert u.prompt('API key', secret=True) == 'my-secret-key'\nprint('DONE')"
        data = self.terminal_run(code, b'my-secret-key\r')
        self.assertIn(b'DONE', data)
        self.assertNotIn(b'my-secret-key', data)

    def test_filter_matches_multiple_words(self):
        self.assertEqual(ui.matches(['Pi · work · coder', 'Qwen · home · coder'], 'WORK pi'), ['Pi · work · coder'])

    def test_unavailable_terminal_falls_back_to_numbered_menu(self):
        with patch.object(ui, 'PLAIN', False), patch.object(ui, 'interactive', return_value=True), \
             patch.object(ui.curses, 'wrapper', side_effect=ui.curses.error('unknown terminal')), \
             patch('builtins.input', return_value='2'):
            self.assertEqual(ui.select('Agentes', ['one', 'two']), 'two')
            self.assertTrue(ui.PLAIN)


class DirectoryPickerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'project').mkdir()

    def test_plain_menu_picks_nested_folder_and_uses_it(self):
        with patch.object(ui, 'interactive', return_value=False), \
             patch('builtins.input', side_effect=['3', '1']):
            picked = ui.pick_directory('Carpeta', start=self.root)
        self.assertEqual(picked, (self.root / 'project').resolve())

    def test_plain_menu_accepts_typed_path(self):
        with patch.object(ui, 'interactive', return_value=False), \
             patch('builtins.input', side_effect=[str(self.root / 'project'), '1']):
            picked = ui.pick_directory('Carpeta', start=self.root)
        self.assertEqual(picked, (self.root / 'project').resolve())

    def test_plain_menu_cancel_raises(self):
        with patch.object(ui, 'interactive', return_value=False), \
             patch('builtins.input', return_value='0'):
            with self.assertRaises(ui.Cancelled):
                ui.pick_directory('Carpeta', start=self.root)

    def test_invalid_start_falls_back_to_home(self):
        with patch.object(ui, 'interactive', return_value=False), \
             patch('builtins.input', return_value='1'):
            picked = ui.pick_directory('Carpeta', start=self.root / 'missing')
        self.assertEqual(picked, Path.home().resolve())


class ToneSubstrings(unittest.TestCase):
    def test_desinstalado_is_not_success_and_related_cases_hold(self):
        self.assertNotEqual(ui.tone('Agente desinstalado'), 'success')
        self.assertEqual(ui.tone('✓ Instalado'), 'success')
        self.assertEqual(ui.tone('No instalado'), 'pending')
        self.assertEqual(ui.tone('Modelo respondió'), 'success')
        self.assertEqual(ui.tone('Falló la comprobación'), 'error')


if __name__ == '__main__':
    unittest.main()
