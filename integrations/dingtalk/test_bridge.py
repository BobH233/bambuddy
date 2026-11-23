import base64
import hashlib
import hmac
import json
import tempfile
import threading
import unittest
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest.mock import patch
import bridge


class BridgeTest(unittest.TestCase):
    def test_connection_is_closed_even_when_transaction_fails(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(bridge, 'ROOT', Path(directory)):
            bridge.init_db()
            for fail in (False, True):
                try:
                    with bridge.database() as db:
                        db.execute('SELECT 1')
                        if fail:
                            raise ValueError('rollback')
                except ValueError:
                    pass
                with self.assertRaises(sqlite3.ProgrammingError):
                    db.execute('SELECT 1')

    def test_chinese_progress_and_completion_photo(self):
        url = 'https://bambu.example.com/api/v1/notifications/photos/print_progress_20261115_150000_' + 'a' * 32 + '.jpg'
        event = dict(event='print_progress', title='Print 75% Complete', message='old', printer='Lamps-X2D',
                     filename='模型PLA.gcode', progress='75', remaining_time='32m', image_url=url,
                     timestamp='2026-11-15T06:34:17.375814+00:00')
        c = {'external_url': 'https://bambu.example.com/'}
        title, text = bridge.markdown(event, c)
        self.assertEqual(title, '打印进度 75%')
        self.assertIn('剩余时间：32分钟', text)
        self.assertIn('任务：模型PLA.gcode', text)
        self.assertIn('2026-11-15 14:34:17', text)
        self.assertIn('![打印现场截图](' + url + ')', text)
        self.assertTrue(text.endswith('![打印现场截图](' + url + ')'))
        self.assertEqual(text.count(url), 1)
        event.update(event='print_complete', duration='1h 2m', filament_grams='12.3')
        event['finish_photo_url'] = event.pop('image_url')
        title, text = bridge.markdown(event, c)
        self.assertEqual(title, '打印完成')
        self.assertIn('1小时 2分钟', text)
        self.assertIn('![打印完成照片]', text)
        self.assertEqual(text.count(event['finish_photo_url']), 1)

    def test_photo_urls_cannot_open_other_media_or_verdict_capabilities(self):
        c = {'external_url': 'https://bambu.example.com/'}
        for url in ('https://evil.example/a.jpg', '/relative.jpg', 'https://bambu.example.com/api/v1/camera/1?token=private',
                    'https://bambu.example.com/api/v1/archives/1/photos/photo.jpg', 'https://[bad'):
            self.assertIsNone(bridge.photo_url({'image_url': url}, c))
        title, text = bridge.markdown({'event': 'print_progress', 'progress': 25, 'message': 'old'})
        self.assertEqual(title, '打印进度 25%')
        self.assertNotIn('![', text)
        self.assertIn('未知', text)

    def test_all_enabled_event_types_have_localization_except_app_authored_text(self):
        events = set(bridge.__dict__['localized'].__globals__['TEMPLATES'])
        self.assertEqual(len(events - {'test'}), 34)
        title, text = bridge.markdown({'event': 'app:external', 'title': '外部应用', 'message': 'Custom diagnostic'})
        self.assertEqual(title, '外部应用')
        self.assertIn('Custom diagnostic', text)
    def test_signature_and_host_restrictions(self):
        url = bridge.signed_url('https://oapi.dingtalk.com/robot/send?access_token=example', 'SECexample', 123)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        expected = base64.b64encode(hmac.new(b'SECexample', b'123\nSECexample', hashlib.sha256).digest()).decode()
        self.assertEqual(query['sign'], [expected])
        self.assertEqual(query['timestamp'], ['123'])
        for bad in ('http://oapi.dingtalk.com/robot/send?access_token=a', 'https://example.com/robot/send?access_token=a'):
            with self.assertRaises(ValueError): bridge.signed_url(bad, '')

    def test_dingtalk_http_200_error_is_not_delivery(self):
        sender = bridge.Sender()
        c = {'webhook_url': 'https://oapi.dingtalk.com/robot/send?access_token=example'}
        with patch.object(sender, 'post', return_value={'errcode': 310000, 'errmsg': 'wrong keyword'}):
            with self.assertRaisesRegex(RuntimeError, 'dingtalk_errcode_310000'):
                sender.send({'title': '完成', 'message': '已完成'}, c)

    def test_app_token_and_group_payload(self):
        sender = bridge.Sender()
        c = dict(mode='app',app_key='key',app_secret='secret',robot_code='robot',open_conversation_id='group')
        with patch.object(sender, 'post', side_effect=[{'accessToken': 'cached', 'expireIn': 7200}, {'processQueryKey': 'one'}, {'processQueryKey': 'two'}]) as post:
            sender.send({'title': '打印完成', 'message': '测试'}, c)
            sender.send({'message': '测试2'}, c)
            self.assertEqual(post.call_count, 3)
            self.assertEqual(post.call_args_list[1].args[1]['msgKey'], 'sampleMarkdown')
            self.assertEqual(post.call_args_list[1].args[2]['x-acs-dingtalk-access-token'], 'cached')

    def test_successful_delivery_is_committed_and_not_retried(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(bridge, 'ROOT', Path(directory)):
            bridge.init_db()
            with bridge.database() as db:
                db.execute('INSERT INTO messages(payload,created) VALUES(?,?)', ('{"message":"test"}', 1))
            sender = bridge.Sender()
            with patch.object(sender, 'send') as send:
                self.assertTrue(bridge.deliver_one(sender, {}))
                self.assertFalse(bridge.deliver_one(sender, {}))
                send.assert_called_once()
            with bridge.database() as db:
                row = db.execute('SELECT status,sent,error FROM messages').fetchone()
            self.assertEqual(row['status'], 'sent')
            self.assertIsNotNone(row['sent'])
            self.assertIsNone(row['error'])

    def test_failure_is_retried_then_persisted_as_failed(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(bridge, 'ROOT', Path(directory)):
            bridge.init_db()
            with bridge.database() as db:
                db.execute('INSERT INTO messages(payload,created,attempts) VALUES(?,?,?)', ('{"message":"test"}', 1, 9))
            sender = bridge.Sender()
            with patch.object(sender, 'send', side_effect=RuntimeError('dingtalk_errcode_310000')):
                self.assertTrue(bridge.deliver_one(sender, {}))
            with bridge.database() as db:
                row = db.execute('SELECT status,attempts,error FROM messages').fetchone()
            self.assertEqual(tuple(row), ('failed', 10, 'dingtalk_errcode_310000'))

    def test_body_is_bounded(self):
        title, text = bridge.markdown({'title': 'line\nbreak','message': '材料' * 15000})
        self.assertNotIn('\n', title)
        self.assertLessEqual(len(text.encode()), 14000)

    def test_authenticated_receive_is_persisted_without_photos(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(bridge, 'ROOT', Path(directory)):
            config = {'adapter_token': 'local-token', 'webhook_url': ''}
            path=Path(directory,'config.json');path.write_text(json.dumps(config))
            bridge.init_db()
            server=bridge.ThreadingHTTPServer(('127.0.0.1',0),bridge.Handler)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            url=f'http://127.0.0.1:{server.server_port}/notify'
            def send(token):
                req=urllib.request.Request(url,data=json.dumps({'message':'测试','title':'打印完成','image':'unused-photo',
                     'event':'print_progress','progress':75,'remaining_time':'32m','image_url':'https://example.com/photo',
                     'good_url':'private-verdict-capability'}).encode(),headers={'Authorization':'Bearer '+token})
                return urllib.request.urlopen(req)
            try:
                with self.assertRaises(urllib.error.HTTPError) as error: send('wrong')
                self.assertEqual(error.exception.code,401)
                with self.assertRaises(urllib.error.HTTPError) as error: send('local-token')
                self.assertEqual(error.exception.code,503)
                config['webhook_url']='https://oapi.dingtalk.com/robot/send?access_token=example';path.write_text(json.dumps(config))
                with send('local-token') as response:self.assertEqual(response.status,202)
                with bridge.database() as db:row=db.execute('SELECT * FROM messages').fetchone()
                self.assertEqual(row['status'],'pending')
                self.assertNotIn('image',json.loads(row['payload']))
                self.assertNotIn('good_url',json.loads(row['payload']))
                self.assertEqual(json.loads(row['payload'])['progress'],75)
                self.assertIn('image_url',json.loads(row['payload']))
            finally:
                server.shutdown();server.server_close();thread.join()


if __name__=='__main__': unittest.main()
