import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, waitFor, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { CameraMedia } from '../../components/CameraMedia';
import { setStreamToken } from '../../api/client';

const mocks = vi.hoisted(() => ({ supported: true, instances: [] as Array<{
  source?: string; destroy: ReturnType<typeof vi.fn>; recoverMediaError: ReturnType<typeof vi.fn>;
  handlers: Record<string, (_event: string, data: unknown) => void>;
}> }));
vi.mock('hls.js', () => ({ default: class {
  static isSupported = () => mocks.supported;
  static Events = { ERROR: 'error', MANIFEST_PARSED: 'parsed' };
  static ErrorTypes = { MEDIA_ERROR: 'mediaError', NETWORK_ERROR: 'networkError' };
  static DefaultConfig = { manifestLoadPolicy: { default: {} }, fragLoadPolicy: { default: {} } };
  source?: string;
  destroy = vi.fn();
  recoverMediaError = vi.fn();
  handlers: Record<string, (_event: string, data: unknown) => void> = {};
  constructor() { mocks.instances.push(this); }
  on(event: string, callback: (_event: string, data: unknown) => void) { this.handlers[event] = callback; }
  loadSource(source: string) { this.source = source; }
  attachMedia() {}
  stopLoad() {}
} }));

function viewer(src: string) {
  return <QueryClientProvider client={new QueryClient()}>
    <CameraMedia src={src} image={<img alt="camera" src={src} />} />
  </QueryClientProvider>;
}

beforeEach(() => {
  mocks.instances.length = 0;
  mocks.supported = true;
  vi.spyOn(document, 'hidden', 'get').mockReturnValue(false);
  vi.spyOn(global, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ transport: 'hls' })));
  vi.spyOn(HTMLMediaElement.prototype, 'canPlayType').mockReturnValue('');
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {});
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
});
afterEach(() => { cleanup(); setStreamToken(null); vi.useRealTimers(); vi.restoreAllMocks(); });

describe('CameraMedia', () => {
  it('uses compressed video without starting the MJPEG image and destroys its player', async () => {
    const view = render(viewer('/api/v1/printers/2/camera/stream?token=old'));
    await waitFor(() => expect(mocks.instances.length).toBe(1));
    expect(view.container.querySelector('img')).toBeNull();
    expect(view.container.querySelector('video')).not.toBeNull();
    expect(mocks.instances[0].source).toContain('/camera/hls/index.m3u8?token=old');
    view.unmount();
    expect(mocks.instances[0].destroy).toHaveBeenCalledOnce();
  });

  it('refreshes an internal MSE source when its login token rotates', async () => {
    setStreamToken('old');
    render(viewer('/api/v1/printers/2/camera/stream?token=old'));
    await waitFor(() => expect(mocks.instances.length).toBe(1));
    act(() => setStreamToken('new'));
    await waitFor(() => expect(mocks.instances.length).toBe(2));
    expect(mocks.instances[1].source).toContain('token=new');
    expect(mocks.instances[0].destroy).toHaveBeenCalledOnce();
  });

  it('keeps snapshot mode as an image', () => {
    const view = render(viewer('/api/v1/printers/2/camera/snapshot?token=x'));
    expect(view.container.querySelector('img')).not.toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('rebuilds a broken network player without reloading the page', async () => {
    render(viewer('/api/v1/printers/2/camera/stream?token=x'));
    await waitFor(() => expect(mocks.instances.length).toBe(1));
    vi.useFakeTimers();
    act(() => mocks.instances[0].handlers.error('error', {fatal: true, type: 'networkError'}));
    await act(async () => { await vi.advanceTimersByTimeAsync(1100); });
    expect(mocks.instances.length).toBe(2);
    expect(mocks.instances[0].destroy).toHaveBeenCalledOnce();
    expect(mocks.instances[1].source).toContain('/camera/hls/index.m3u8');
  });

  it('recovers a media decoding error before rebuilding the player', async () => {
    render(viewer('/api/v1/printers/2/camera/stream?token=x'));
    await waitFor(() => expect(mocks.instances.length).toBe(1));
    act(() => mocks.instances[0].handlers.error('error', {fatal: true, type: 'mediaError'}));
    expect(mocks.instances[0].recoverMediaError).toHaveBeenCalledOnce();
    expect(mocks.instances[0].destroy).not.toHaveBeenCalled();
  });

  it('recovers a frozen video after playback had started', async () => {
    vi.useFakeTimers();
    let view!: ReturnType<typeof render>;
    await act(async () => { view = render(viewer('/api/v1/printers/2/camera/stream?token=x')); });
    expect(mocks.instances.length).toBe(1);
    fireEvent.playing(view.container.querySelector('video')!);
    await act(async () => { await vi.advanceTimersByTimeAsync(17000); });
    expect(mocks.instances.length).toBe(2);
  });

  it('offers a play action if the browser blocks autoplay', async () => {
    vi.mocked(HTMLMediaElement.prototype.play).mockRejectedValueOnce({name: 'NotAllowedError'});
    const view = render(viewer('/api/v1/printers/2/camera/stream?token=x'));
    await waitFor(() => expect(mocks.instances.length).toBe(1));
    fireEvent.loadedMetadata(view.container.querySelector('video')!);
    const button = await screen.findByRole('button', {name:'点击播放画面'});
    fireEvent.click(button);
    await waitFor(() => expect(screen.queryByRole('button', {name:'点击播放画面'})).toBeNull());
  });
});
