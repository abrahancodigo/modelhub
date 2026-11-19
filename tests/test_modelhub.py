import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import claude_bridge
import modelhub

PROFILE = {'protocol': 'openai', 'base_url': 'https://api.example.com/v1',
           'model': 'example-model', 'api_key': 'secret'}


class ProfileVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        patcher = patch.object(modelhub, 'HOME', self.home)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_label_and_card_show_base_url(self):
        label = modelhub.profile_label('work', PROFILE)
        self.assertIn('https://api.example.com/v1', label)
        self.assertIn('openai', label)
        card = modelhub.profile_card('work', PROFILE)
        self.assertIn('https://api.example.com/v1', card['detail'])
        self.assertIn('Clave guardada', card['detail'])

    def test_rows_map_label_to_name(self):
        rows = modelhub.profile_rows({'work': PROFILE})
        self.assertEqual(list(rows.values()), ['work'])
        self.assertIn('https://api.example.com/v1', list(rows)[0])

    def test_key_status_without_leaking_key(self):
        self.assertEqual(modelhub.key_status(PROFILE), 'Clave guardada')
        self.assertEqual(modelhub.key_status(dict(PROFILE, api_key='')), 'Sin clave')
        self.assertNotIn('secret', modelhub.profile_card('work', PROFILE)['detail'])

    def test_check_all_stores_catalogue_results(self):
        modelhub.private_json(self.home / 'profiles.json', {'work': PROFILE})
        with patch.object(modelhub, 'discover', return_value=['example-model']), \
             patch.object(modelhub.ui, 'confirm', return_value=True), \
             patch.object(modelhub.ui, 'message'):
            modelhub.check_all()
        check = modelhub.load_state()['checks']['work']
        self.assertTrue(check['ok'])
        self.assertEqual(check['summary'], 'Catálogo accesible')

    def test_check_all_does_nothing_when_cancelled(self):
        modelhub.private_json(self.home / 'profiles.json', {'work': PROFILE})
        with patch.object(modelhub, 'discover') as discover, \
             patch.object(modelhub.ui, 'confirm', return_value=False):
            modelhub.check_all()
        discover.assert_not_called()

    def test_load_state_rejects_non_boolean_plain(self):
        (self.home / 'state.json').write_text(json.dumps({'plain': 'yes'}))
        with self.assertRaises(ValueError):
            modelhub.load_state()

    def test_load_reports_invalid_profile_name(self):
        modelhub.private_json(self.home / 'profiles.json', {'bad name': PROFILE})
        with self.assertRaisesRegex(ValueError, 'Nombre de perfil inválido'):
            modelhub.load()


class AdapterValidationTests(unittest.TestCase):
    def test_command_must_be_literal(self):
        with self.assertRaisesRegex(ValueError, 'command debe ser literal'):
            modelhub.validate_adapter({'command': '{{profile}}', 'protocols': ['openai']})

    def test_reserved_environment_names_are_rejected(self):
        with self.assertRaises(ValueError):
            modelhub.validate_adapter({'command': 'x', 'protocols': ['openai'],
                                       'env': {'PATH': '/tmp'}})


class BridgeConversionTests(unittest.TestCase):
    def test_text_is_kept_after_tool_result(self):
        payload = {'messages': [{'role': 'user', 'content': [
            {'type': 'tool_result', 'tool_use_id': 'toolu_1', 'content': 'output'},
            {'type': 'text', 'text': 'continua'},
        ]}]}
        messages = claude_bridge.convert_messages(payload)
        self.assertEqual(messages[0]['role'], 'tool')
        self.assertEqual(messages[-1], {'role': 'user', 'content': 'continua'})

    def test_assistant_text_is_kept_with_tool_calls(self):
        payload = {'messages': [{'role': 'assistant', 'content': [
            {'type': 'text', 'text': 'voy'},
            {'type': 'tool_use', 'id': 'toolu_1', 'name': 'ls', 'input': {'path': '.'}},
        ]}]}
        messages = claude_bridge.convert_messages(payload)
        self.assertEqual(messages[0]['content'], 'voy')
        self.assertEqual(messages[0]['tool_calls'][0]['function']['name'], 'ls')

    def test_thinking_signature_is_reused(self):
        payload = {'model': 'm', 'messages': [{'role': 'assistant', 'content': [
            {'type': 'thinking', 'thinking': 'x', 'signature': 'sig-1'}]}]}
        _, _, signature = claude_bridge.convert_request(payload)
        self.assertEqual(signature, 'sig-1')

    def test_tool_use_ids_are_reused(self):
        payload = {'model': 'm', 'messages': [{'role': 'assistant', 'content': [
            {'type': 'tool_use', 'id': 'toolu_abc', 'name': 'ls', 'input': {}}]}]}
        _, ids, _ = claude_bridge.convert_request(payload)
        self.assertEqual(list(ids), ['toolu_abc'])


if __name__ == '__main__':
    unittest.main()
