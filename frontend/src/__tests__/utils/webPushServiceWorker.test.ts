import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { describe, expect, it, vi } from 'vitest';

function worker() {
  const handlers: Record<string, (event: unknown) => void> = {};
  const showNotification = vi.fn().mockResolvedValue(undefined);
  const openWindow = vi.fn().mockResolvedValue(undefined);
  const self = {
    addEventListener: (name: string, callback: (event: unknown) => void) => { handlers[name] = callback; },
    location: { origin: 'https://bambuddy.example' },
    registration: { showNotification },
    clients: { matchAll: async () => [], openWindow },
  };
  runInNewContext(readFileSync('public/sw.js', 'utf8'), { self, URL, console });
  async function dispatch(name: string, event: object) {
    let completion: Promise<void> | undefined;
    handlers[name]({ ...event, waitUntil: (promise: Promise<void>) => { completion = promise; } });
    await completion;
  }
  return { showNotification, openWindow, dispatch, self };
}

describe('Web Push service worker', () => {
  it('displays a push even when no web page is open', async () => {
    const sw = worker();
    await sw.dispatch('push', { data: { json: () => ({ title: '打印完成', body: '模型已完成', url: '/', tag: 'completed' }) } });
    expect(sw.showNotification).toHaveBeenCalledWith('打印完成', expect.objectContaining({ body: '模型已完成', tag: 'completed', data: { url: '/' } }));
  });
  it('always displays a visible fallback for an empty or malformed payload', async () => {
    const sw = worker();
    await sw.dispatch('push', { data: { json: () => { throw new Error('invalid'); } } });
    expect(sw.showNotification).toHaveBeenCalledWith('Bambuddy 通知', expect.objectContaining({ body: expect.any(String) }));
  });
  it.each([null, false, 0, 'invalid'])('displays a fallback for non-object JSON: %s', async (data) => {
    const sw = worker();
    await sw.dispatch('push', { data: { json: () => data } });
    expect(sw.showNotification).toHaveBeenCalledWith('Bambuddy 通知', expect.objectContaining({ body: expect.any(String) }));
  });
  it('keeps notification clicks on the same origin', async () => {
    const sw = worker();
    const close = vi.fn();
    await sw.dispatch('notificationclick', { notification: { close, data: { url: 'https://evil.example/phish' } } });
    expect(close).toHaveBeenCalledOnce();
    expect(sw.openWindow).toHaveBeenCalledWith('https://bambuddy.example/');
  });
  it('opens the requested local destination', async () => {
    const sw = worker();
    await sw.dispatch('notificationclick', { notification: { close: vi.fn(), data: { url: '/queue' } } });
    expect(sw.openWindow).toHaveBeenCalledWith('https://bambuddy.example/queue');
  });
});
