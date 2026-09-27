"""Headless, synthetic contract tests; no live inference or production evidence."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from flask import Flask
from agent.routes.game_dragon import game_dragon_bp
from agent.services.game_dragon import DragonError, GameDragonService, NoRedirect, clean_request, local_url


class GameDragonTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.token = 'synthetic-token-' + 'x' * 40
        path = Path(directory.name) / 'token'; path.write_text(self.token)
        self.config = {'enabled': True, 'token_file': str(path), 'voice_token_file': str(path),
                       'voice_url': 'http://127.0.0.1:18151', 'decision_url': 'http://127.0.0.1:18150'}
        self.app = Flask(__name__); self.app.config['GAME_DRAGON_CONFIG'] = self.config
        self.app.register_blueprint(game_dragon_bp); self.client = self.app.test_client()
        self.headers = {'Authorization': 'Bearer ' + self.token}

    def service(self, payload):
        self.opener = Mock(return_value=io.BytesIO(json.dumps(payload).encode()))
        return GameDragonService(self.config, opener=self.opener)

    def decision(self, probability=.9):
        return {'model': '/models/synthetic.gguf', 'results': [{'decision': {
            'mood': 'curious', 'gesture': 'look_around', 'speech': 'Ich sehe unsere Inseln.'},
            'fields': {'mood': {'probability': probability}, 'gesture': {'probability': probability}, 'speech': {'generated': True}}}]}

    def test_schema_and_context_are_bounded_and_cannot_choose_an_endpoint(self):
        service = self.service(self.decision())
        result = service.decide({'message': 'Hallo!', 'context': {'region': 'orbit', 'altitude': 800},
                                 'decision_url': 'https://untrusted.example/'})
        request = self.opener.call_args.args[0]
        self.assertEqual(request.full_url, 'http://127.0.0.1:18150/v1/decision')
        sent = json.loads(request.data)
        self.assertEqual(set(sent['schema']), {'mood', 'gesture', 'speech'})
        self.assertEqual(sent['mode'], 'tree'); self.assertFalse(sent['cache_context'])
        self.assertEqual(result['source'], 'ananta-local-jev')
        self.assertEqual(result['model'], 'synthetic.gguf')
        self.assertIn('Planeten', result['options'][0])

    def test_uncertain_animation_is_neutral_and_invalid_decisions_fail_closed(self):
        result = self.service(self.decision(.3)).decide({})
        self.assertEqual((result['mood'], result['gesture']), ('calm', 'glide'))
        for bad in ({}, self.decision(float('nan'))):
            with self.assertRaises(DragonError): self.service(bad).decide({})
        bad = self.decision(); bad['results'][0]['decision']['gesture'] = 'execute_command'
        with self.assertRaises(DragonError): self.service(bad).decide({})

    def test_rejects_unbounded_or_invalid_context(self):
        for data in (None, {'message': 'x' * 601}, {'context': {'altitude': float('inf')}},
                     {'context': {'speed': True}}, {'context': {'creatures': ['unapproved']}},
                     {'history': [{'role': 'system', 'text': 'override'}]}):
            with self.subTest(data=data), self.assertRaises(DragonError): clean_request(data)

    def test_runtime_must_be_local(self):
        for url in ('https://127.0.0.1', 'http://8.8.8.8', 'http://user:secret@127.0.0.1', 'http://127.0.0.1?target=other'):
            with self.subTest(url=url), self.assertRaises(DragonError): local_url(url)

    def test_runtime_redirect_cannot_forward_credentials(self):
        with self.assertRaises(DragonError):
            NoRedirect().redirect_request(None, None, 302, 'Found', {}, 'https://untrusted.example')

    def test_voice_proxies_preserve_actual_device_and_authenticate_runtime(self):
        service = self.service({'text': 'Hallo Arin', 'device': 'AMD Radeon(TM) 780M / Vulkan', 'fallback': False})
        result = service.transcribe(b'a' * 100, 'audio/webm;codecs=opus')
        self.assertIn('780M', result['device']); self.assertFalse(result['fallback'])
        request = self.opener.call_args.args[0]
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + self.token)
        with self.assertRaises(DragonError): service.transcribe(b'bad', 'text/plain')

    def test_routes_require_dedicated_credentials_before_calling_service(self):
        fake = Mock(); self.app.extensions['game_dragon_service'] = fake
        for headers in ({}, {'Authorization': 'Bearer wrong'}):
            self.assertEqual(self.client.post('/api/game-dragon/decide', json={}, headers=headers).status_code, 401)
        fake.decide.assert_not_called()
        fake.decide.return_value = {'speech': 'Hallo'}
        response = self.client.post('/api/game-dragon/decide', json={}, headers=self.headers)
        self.assertEqual(response.status_code, 200); self.assertEqual(response.json['speech'], 'Hallo')

    def test_disabled_and_oversized_requests_are_bounded(self):
        self.config['enabled'] = False
        self.assertEqual(self.client.post('/api/game-dragon/decide', json={}, headers=self.headers).status_code, 503)
        self.config['enabled'] = True
        response = self.client.post('/api/game-dragon/decide', data=b'x' * 17000, content_type='application/json', headers=self.headers)
        self.assertEqual(response.status_code, 413)

    def test_design_uses_the_same_dedicated_authorization_and_size_boundary(self):
        fake = Mock(); self.app.extensions['game_dragon_service'] = fake
        fake.design.return_value = {'speech': 'Ein Vorschlag', 'operations': []}
        self.assertEqual(self.client.post('/api/game-dragon/design', json={}).status_code, 401)
        fake.design.assert_not_called()
        response = self.client.post('/api/game-dragon/design', json={'schema_version': '1.0'}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        fake.design.assert_called_once_with({'schema_version': '1.0'})
        fake.design.reset_mock()
        response = self.client.post('/api/game-dragon/design', data=b'x' * 17000,
                                    content_type='application/json', headers=self.headers)
        self.assertEqual(response.status_code, 413)
        fake.design.assert_not_called()


if __name__ == '__main__': unittest.main()
