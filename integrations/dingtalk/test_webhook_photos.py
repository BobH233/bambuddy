"""Run with Bambuddy's virtualenv and APP_ROOT pointing to its source tree.

All HTTP clients and photo persistence are mocked; no real messages are sent.
"""
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.environ['APP_ROOT'])
import backend.app.core.database  # Initialize Base before the model import cycle.
path = Path(os.environ.get('NOTIFICATION_SOURCE', str(Path(os.environ['APP_ROOT']) / 'backend/app/services/notification_service.py')))
spec = importlib.util.spec_from_file_location('notification_under_test', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class WebhookPhotosTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = module.NotificationService()
        self.http = MagicMock()
        self.http.post = AsyncMock(return_value=MagicMock(status_code=202))
        self.service._get_client = AsyncMock(return_value=self.http)
        self.url = 'https://bambu.example/api/v1/notifications/photos/print_progress_20261115_150000_' + 'a' * 32 + '.jpg'
        self.config = dict(webhook_url='https://adapter.example/notify', payload_format='generic', photo_as_url=True)

    async def test_url_mode_does_not_send_base64(self):
        await self.service._send_webhook(self.config, 'Title', 'Message', image_data=b'jpeg',
                                         image_url=self.url, event_type='print_progress', variables={'progress': '75'})
        payload = self.http.post.call_args.kwargs['json']
        self.assertEqual(payload['image_url'], self.url)
        self.assertEqual(payload['progress'], '75')
        self.assertNotIn('image', payload)

    async def test_generic_default_still_sends_base64(self):
        config = {**self.config, 'photo_as_url': False}
        await self.service._send_webhook(config, 'Title', 'Message', image_data=b'jpeg')
        self.assertEqual(self.http.post.call_args.kwargs['json']['image'], 'anBlZw==')

    async def test_photo_provider_opt_out_and_photo_unavailable(self):
        self.service._get_or_build_photo_url = AsyncMock(return_value=self.url)
        provider = module.NotificationProvider(name='test', provider_type='webhook', enabled=True,
                                               config=json.dumps(self.config), attach_photo=True)
        await self.service._send_to_provider(provider, 'Title', 'Message', image_data=b'jpeg')
        self.assertEqual(self.http.post.call_args.kwargs['json']['image_url'], self.url)
        provider.attach_photo = False
        self.service._get_or_build_photo_url.reset_mock()
        await self.service._send_to_provider(provider, 'Title', 'Message', image_data=b'jpeg')
        self.service._get_or_build_photo_url.assert_not_awaited()
        self.assertNotIn('image_url', self.http.post.call_args.kwargs['json'])
        provider.attach_photo = True
        self.service._get_or_build_photo_url.return_value = None
        await self.service._send_to_provider(provider, 'Title', 'Message')
        self.assertNotIn('image_url', self.http.post.call_args.kwargs['json'])


if __name__ == '__main__':
    unittest.main()
