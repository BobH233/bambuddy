import { useCallback, useEffect, useRef, useState } from 'react';
import { Bell, BellOff, Loader2, Send } from 'lucide-react';
import { api, ApiError, type WebPushConfig } from '../api/client';
import { Card, CardContent, CardHeader } from './Card';
import { Button } from './Button';
import { applicationServerKey, pushEnvironment, pushRegistration, subscriptionHash } from '../utils/webPush';

const categories = [
  ['prints', '打印开始、完成、失败与停止'],
  ['errors', '打印机离线与打印异常'],
  ['progress', '首层完成、打印进度与热床冷却'],
  ['ams', '耗材与 AMS 提醒'],
  ['maintenance', '打印机维护提醒'],
  ['queue', '打印队列动态'],
];

export function WebPushSettings() {
  const [config, setConfig] = useState<WebPushConfig | null>(null);
  const [subscription, setSubscription] = useState<PushSubscription | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [selected, setSelected] = useState<string[]>(['prints', 'errors']);
  const [saved, setSaved] = useState<string[]>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const working = useRef(false);
  const unsupported = pushEnvironment();
  const denied = !unsupported && Notification.permission === 'denied';

  const refresh = useCallback(async () => {
    if (pushEnvironment()) { setBusy(false); return; }
    try {
      const next = await api.getWebPushConfig();
      const registration = await navigator.serviceWorker.getRegistration('/');
      const current = await registration?.pushManager.getSubscription() ?? null;
      const fingerprint = current ? await subscriptionHash(current) : '';
      const device = next.subscriptions.find((item) => item.endpoint_hash === fingerprint);
      setConfig(next);
      setSubscription(current);
      const currentKey = current?.options.applicationServerKey;
      const matchesKey = !!currentKey && Array.from(new Uint8Array(currentKey)).join() === Array.from(applicationServerKey(next.public_key)).join();
      setEnabled(!!device && matchesKey && Notification.permission === 'granted');
      setSelected(device?.categories ?? next.default_categories);
      setSaved(device?.categories ?? []);
      setError('');
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : '无法读取通知设置，请稍后重试。');
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const visible = () => { if (document.visibilityState === 'visible' && !working.current) void refresh(); };
    document.addEventListener('visibilitychange', visible);
    return () => document.removeEventListener('visibilitychange', visible);
  }, [refresh]);

  const run = async (action: () => Promise<void>) => {
    working.current = true;
    setBusy(true); setError(''); setMessage('');
    try { await action(); }
    catch (failure) {
      const detail = failure instanceof Error ? failure.message : '';
      setError(/[\u4e00-\u9fff]/.test(detail) ? detail
        : failure instanceof DOMException && failure.name === 'NotAllowedError'
          ? '通知权限未开启，请在系统设置中允许 Bambuddy 发送通知。'
          : '无法连接系统推送服务，请检查网络后重试。');
    } finally { working.current = false; setBusy(false); }
  };

  const enable = () => {
    const permission = Notification.requestPermission();
    void run(async () => {
      if (await permission !== 'granted') throw new Error('通知权限未开启，请在系统设置中允许 Bambuddy 发送通知。');
      if (!config) throw new Error('通知配置尚未就绪，请刷新后重试。');
      const registration = await pushRegistration();
      let current = await registration.pushManager.getSubscription();
      const expectedKey = applicationServerKey(config.public_key);
      if (current) {
        const currentKey = current.options.applicationServerKey;
        if (!enabled || !currentKey || Array.from(new Uint8Array(currentKey)).join() !== Array.from(expectedKey).join()) {
          await api.disableWebPush(current.endpoint);
          await current.unsubscribe();
          current = null;
        }
      }
      let created = false;
      if (!current) {
        current = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: expectedKey });
        created = true;
      }
      try {
        await api.subscribeWebPush(current.toJSON(), selected);
      } catch (failure) {
        if (created) await current.unsubscribe();
        throw failure;
      }
      setSubscription(current); setEnabled(true); setSaved([...selected]);
      setMessage('已在此设备开启通知');
    });
  };

  const disable = () => void run(async () => {
    if (subscription) {
      await api.disableWebPush(subscription.endpoint);
      await subscription.unsubscribe();
    }
    setSubscription(null); setEnabled(false); setSaved([]);
    setMessage('已关闭此设备的通知');
  });

  const changed = [...selected].sort().join() !== [...saved].sort().join();
  return (
    <Card id="card-web-push">
      <CardHeader>
        <h2 className="text-lg font-semibold text-white flex items-center gap-2"><Bell className="w-5 h-5 text-bambu-green" />此设备的通知</h2>
      </CardHeader>
      <CardContent className="space-y-4">
        {unsupported ? <p className="text-sm text-bambu-gray">{unsupported}</p> : <>
          <p className="text-sm text-bambu-gray">{enabled ? '已开启' : denied ? '已被系统阻止，请在系统设置中允许通知。' : '未开启'}</p>
          <fieldset disabled={busy} className="space-y-3">
            <legend className="text-sm text-white mb-3">接收哪些通知</legend>
            {categories.map(([value, label]) => (
              <label key={value} className="flex gap-3 items-center text-sm text-white cursor-pointer">
                <input type="checkbox" checked={selected.includes(value)} onChange={(event) => setSelected((previous) => event.target.checked ? [...previous, value] : previous.filter((item) => item !== value))} className="accent-bambu-green w-4 h-4 shrink-0" />
                {label}
              </label>
            ))}
          </fieldset>
          <div className="flex flex-wrap gap-2">
            {enabled ? <>
              <Button size="sm" variant="secondary" disabled={busy} onClick={disable}><BellOff className="w-4 h-4" />关闭通知</Button>
              <Button size="sm" disabled={busy || !selected.length || !changed} onClick={() => void run(async () => {
                if (!subscription) return;
                await api.subscribeWebPush(subscription.toJSON(), selected);
                setSaved([...selected]); setMessage('通知类型已保存');
              })}>保存通知类型</Button>
              <Button size="sm" variant="secondary" disabled={busy} onClick={() => void run(async () => {
                if (!subscription) return;
                await api.testWebPush(subscription.endpoint); setMessage('测试通知已发送');
              })}><Send className="w-4 h-4" />发送测试通知</Button>
            </> : <Button size="sm" disabled={busy || denied || !config || !selected.length} onClick={enable}>{busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Bell className="w-4 h-4" />}开启此设备通知</Button>}
            {!config && !busy && <Button size="sm" variant="secondary" onClick={() => { setBusy(true); void refresh(); }}>重试</Button>}
          </div>
        </>}
        {error && <p role="alert" className="text-sm text-red-400">{error}</p>}
        {message && <p role="status" className="text-sm text-bambu-green">{message}</p>}
      </CardContent>
    </Card>
  );
}
