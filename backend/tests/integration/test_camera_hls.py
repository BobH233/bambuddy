import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text
from backend.app.api.routes import camera, updates
from backend.app.core import auth
from backend.app.services.camera_hls import HLSManager, HLSSession

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
async def logged_in(async_client):
    await async_client.post('/api/v1/auth/setup', json={
        'auth_enabled': True, 'admin_username': 'hlsadmin', 'admin_password': 'AdminPass1!'
    })
    return await auth.create_camera_stream_token(username='hlsadmin')


async def test_hls_rejects_missing_bad_and_revoked_tokens(async_client, logged_in, printer_factory, db_session):
    p = await printer_factory(model='P2S')
    path = f'/api/v1/printers/{p.id}/camera/hls/index.m3u8'
    assert (await async_client.get(path)).status_code == 401
    assert (await async_client.get(path + '?token=bad')).status_code == 401
    await db_session.execute(text("UPDATE users SET is_active=false WHERE username='hlsadmin'"))
    await db_session.commit()
    assert (await async_client.get(path, params={'token': logged_in})).status_code == 401


async def test_hls_playlist_and_fragments_keep_auth(async_client, logged_in, printer_factory, tmp_path):
    p = await printer_factory(model='P2S')
    name = '123456abcdef_000000001.ts'
    (tmp_path / name).write_bytes(b'compressed-video')
    session = SimpleNamespace(directory=tmp_path, playlist=AsyncMock(return_value=f'#EXTM3U\n#EXTINF:2,\n{name}\n'))
    base = f'/api/v1/printers/{p.id}/camera/hls/'
    with (patch.object(camera.settings, 'camera_hls_enabled', True),
          patch.object(camera, 'get_ffmpeg_path', return_value='/ffmpeg'),
          patch.object(camera.hls_manager, 'get', return_value=session),
          patch.object(camera.hls_manager, 'ensure', AsyncMock(return_value=session))):
        response = await async_client.get(base + 'index.m3u8', params={'token': logged_in})
        assert response.status_code == 200, response.text
        assert f'{name}?token={logged_in}' in response.text
        assert response.headers['cache-control'] == 'private, no-store'
        assert (await async_client.get(base + name)).status_code == 401
        fragment = await async_client.get(base + name, params={'token': logged_in})
        assert fragment.content == b'compressed-video'
        assert fragment.headers['content-type'] == 'video/mp2t'
        assert (await async_client.get(base + 'preview.jpg', params={'token': logged_in})).status_code == 404
        assert (await async_client.get(base + '123456abcdef_000000002.ts', params={'token': logged_in})).status_code == 404
        with patch.object(camera, 'stream_opener_still_allowed', AsyncMock(return_value=False)):
            assert (await async_client.get(base + name, params={'token': logged_in})).status_code == 403


async def test_hls_enforces_printer_scope(async_client, logged_in, printer_factory, db_session):
    from backend.app.models.api_key import APIKey
    p1 = await printer_factory(model='P2S')
    p2 = await printer_factory(model='X2D')
    key = APIKey(name='limited-hls', key_hash='hash', key_prefix='bb_hls', printer_ids=[p1.id])
    db_session.add(key)
    await db_session.commit()
    token = await auth.create_camera_stream_token(api_key_id=key.id)
    for suffix in ('playback', 'hls/index.m3u8', 'hls/123456abcdef_000000001.ts'):
        response = await async_client.get(f'/api/v1/printers/{p2.id}/camera/{suffix}', params={'token': token})
        assert response.status_code == 404


async def test_concurrent_viewers_share_session_and_cleanup(tmp_path):
    p = SimpleNamespace(id=97, ip_address='192.0.2.1', access_code='private', model='P2S')
    manager = HLSManager()
    async def idle(self):
        await asyncio.Event().wait()
    with patch.object(HLSSession, 'run', idle):
        sessions = await asyncio.gather(*(manager.ensure(p) for _ in range(10)))
        assert len({id(s) for s in sessions}) == 1
        directory = sessions[0].directory
        assert directory.exists()
        await manager.shutdown()
        assert not directory.exists()
        assert manager.get(p.id) is None


async def test_snapshots_reuse_hls_frame():
    session = SimpleNamespace(last_frame=b'jpeg')
    with patch.object(camera.hls_manager, 'get', return_value=session):
        assert camera.is_stream_active(99)
        assert camera.get_buffered_frame(99) == b'jpeg'
        assert camera.try_get_active_buffered_frame(99) == b'jpeg'


async def test_restart_playlist_declares_new_timeline_without_resetting_sequence():
    p = SimpleNamespace(id=98, ip_address='192.0.2.1', access_code='private', model='P2S')
    session = HLSSession(p)
    session.task = asyncio.create_task(asyncio.Event().wait())
    try:
        seq = session.sequence_start
        (session.directory / 'index.m3u8').write_text(f'#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:{seq}\n#EXTINF:2,\n123456abcdef_{seq}.ts\n')
        session.playlist_time = time.time()
        result = await session.playlist()
        assert f'#EXT-X-DISCONTINUITY-SEQUENCE:{seq}' in result
        assert f'#EXT-X-MEDIA-SEQUENCE:{seq}' in result
        session.started = time.time() - 120
        session.generation_started = time.time()
        assert session.status()['stalled'] is False
    finally:
        await session.close()


async def test_managed_updates_cannot_overwrite_patch(async_client):
    with patch.object(updates.settings, 'updates_disabled', True):
        result = await async_client.get('/api/v1/updates/check?force=true')
        assert result.json()['update_available'] is False
        result = await async_client.post('/api/v1/updates/apply')
        assert result.json()['success'] is False
