import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { WebPushSettings } from '../../components/WebPushSettings';

const mock = vi.hoisted(() => ({
  config: vi.fn(), subscribe: vi.fn(), disable: vi.fn(), test: vi.fn(), register: vi.fn(),
  current: null as PushSubscription | null,
  newSubscription: null as PushSubscription | null,
  ready: vi.fn(), permission: vi.fn(),
}));
vi.mock('../../api/client', async (importOriginal) => ({
  ...await importOriginal<object>(),
  api: { getWebPushConfig: mock.config, subscribeWebPush: mock.subscribe, disableWebPush: mock.disable, testWebPush: mock.test },
}));
vi.mock('../../utils/webPush', async (importOriginal) => ({
  ...await importOriginal<object>(),
  subscriptionHash: async () => 'fingerprint',
  pushRegistration: mock.ready,
}));

function device() {
  return { endpoint: 'https://web.push.apple.com/test', options: { applicationServerKey: new Uint8Array([4]).buffer }, unsubscribe: vi.fn().mockResolvedValue(true), toJSON: () => ({ endpoint: 'https://web.push.apple.com/test', keys: { p256dh: 'dummy', auth: 'dummy' } }) } as unknown as PushSubscription;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal('isSecureContext', true);
  vi.stubGlobal('PushManager', class {});
  vi.stubGlobal('Notification', { permission: 'default', requestPermission: mock.permission });
  vi.stubGlobal('matchMedia', () => ({ matches: false }));
  Object.defineProperty(navigator, 'userAgent', { configurable: true, value: 'Desktop' });
  Object.defineProperty(navigator, 'serviceWorker', { configurable: true, value: { getRegistration: mock.register } });
  mock.current = null;
  mock.newSubscription = device();
  mock.register.mockImplementation(async () => ({ pushManager: { getSubscription: async () => mock.current } }));
  mock.ready.mockResolvedValue({ pushManager: { getSubscription: async () => mock.current, subscribe: mock.subscribe } });
  mock.config.mockResolvedValue({ public_key: 'BA', default_categories: ['prints', 'errors'], subscriptions: [] });
  mock.subscribe.mockResolvedValue(mock.newSubscription);
  mock.disable.mockResolvedValue({ success: true });
  mock.permission.mockResolvedValue('granted');
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('personal Web Push settings', () => {
  it('shows iOS Home Screen instructions without requesting permission or fetching secrets', () => {
    Object.defineProperty(navigator, 'userAgent', { configurable: true, value: 'iPhone' });
    render(<WebPushSettings />);
    expect(screen.getByText(/添加到主屏幕/)).toBeInTheDocument();
    expect(mock.config).not.toHaveBeenCalled();
    expect(mock.permission).not.toHaveBeenCalled();
  });

  it('does not offer notification permission on insecure HTTP', () => {
    vi.stubGlobal('isSecureContext', false);
    render(<WebPushSettings />);
    expect(screen.getByText(/HTTPS/)).toBeInTheDocument();
    expect(mock.permission).not.toHaveBeenCalled();
  });

  it('requests permission only from the explicit enable action in an iOS installed app', async () => {
    Object.defineProperty(navigator, 'userAgent', { configurable: true, value: 'iPhone' });
    vi.stubGlobal('matchMedia', () => ({ matches: true }));
    render(<WebPushSettings />);
    const button = await screen.findByRole('button', { name: '开启此设备通知' });
    await waitFor(() => expect(button).toBeEnabled());
    expect(mock.permission).not.toHaveBeenCalled();
    fireEvent.click(button);
    expect(mock.permission).toHaveBeenCalledOnce();
    await screen.findByText('已在此设备开启通知');
    expect(mock.subscribe).toHaveBeenCalledWith(expect.objectContaining({ endpoint: 'https://web.push.apple.com/test' }), ['prints', 'errors']);
  });

  it('does not repeatedly prompt when permission has been denied', async () => {
    vi.stubGlobal('Notification', { permission: 'denied', requestPermission: mock.permission });
    render(<WebPushSettings />);
    await screen.findByText(/已被系统阻止/);
    expect(screen.getByRole('button', { name: '开启此设备通知' })).toBeDisabled();
    expect(mock.permission).not.toHaveBeenCalled();
  });

  it('undoes a newly created browser subscription when the server cannot save it', async () => {
    mock.subscribe.mockImplementationOnce(async () => mock.newSubscription).mockRejectedValueOnce(new Error('保存失败'));
    render(<WebPushSettings />);
    await waitFor(() => expect(screen.getByRole('button', { name: '开启此设备通知' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: '开启此设备通知' }));
    await screen.findByRole('alert');
    expect(mock.newSubscription?.unsubscribe).toHaveBeenCalledOnce();
    expect(screen.getByText('保存失败')).toBeInTheDocument();
  });

  it('disables just this device and removes its browser subscription', async () => {
    mock.current = device();
    mock.config.mockResolvedValue({ public_key: 'BA', default_categories: ['prints', 'errors'], subscriptions: [{ endpoint_hash: 'fingerprint', categories: ['prints'] }] });
    vi.stubGlobal('Notification', { permission: 'granted', requestPermission: mock.permission });
    render(<WebPushSettings />);
    fireEvent.click(await screen.findByRole('button', { name: '关闭通知' }));
    await screen.findByText('已关闭此设备的通知');
    expect(mock.disable).toHaveBeenCalledWith(mock.current.endpoint);
    expect(mock.current.unsubscribe).toHaveBeenCalledOnce();
  });

  it('replaces a subscription created for an old server key', async () => {
    mock.current = device();
    mock.config.mockResolvedValue({ public_key: 'BQ', default_categories: ['prints', 'errors'], subscriptions: [{ endpoint_hash: 'fingerprint', categories: ['prints'] }] });
    vi.stubGlobal('Notification', { permission: 'granted', requestPermission: mock.permission });
    render(<WebPushSettings />);
    await waitFor(() => expect(screen.getByRole('button', { name: '开启此设备通知' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: '开启此设备通知' }));
    await screen.findByText('已在此设备开启通知');
    expect(mock.current.unsubscribe).toHaveBeenCalledOnce();
    expect(mock.subscribe).toHaveBeenCalledWith(expect.objectContaining({ applicationServerKey: new Uint8Array([5]) }));
  });

  it('replaces a browser subscription that belongs to another account', async () => {
    mock.current = device();
    vi.stubGlobal('Notification', { permission: 'granted', requestPermission: mock.permission });
    render(<WebPushSettings />);
    await waitFor(() => expect(screen.getByRole('button', { name: '开启此设备通知' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: '开启此设备通知' }));
    await screen.findByText('已在此设备开启通知');
    expect(mock.disable).toHaveBeenCalledWith(mock.current.endpoint);
    expect(mock.current.unsubscribe).toHaveBeenCalledOnce();
  });
});
