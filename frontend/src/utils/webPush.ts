import { api } from '../api/client';

export function pushEnvironment(): string | null {
  const ios = /iPad|iPhone|iPod/.test(navigator.userAgent)
    || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const standalone = window.matchMedia('(display-mode: standalone)').matches
    || (navigator as Navigator & { standalone?: boolean }).standalone === true;
  if (!window.isSecureContext) return '请使用 HTTPS 地址开启通知。';
  if (ios && !standalone) return '在 Safari 中点击“分享”→“添加到主屏幕”，再从主屏幕打开 Bambuddy 开启通知。';
  if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) {
    return ios ? '此设备暂不支持通知，请升级到 iOS / iPadOS 16.4 或更高版本。' : '此浏览器不支持推送通知。';
  }
  return null;
}

export function applicationServerKey(value: string): Uint8Array<ArrayBuffer> {
  const raw = atob(value.replace(/-/g, '+').replace(/_/g, '/'));
  return Uint8Array.from(raw, (char) => char.charCodeAt(0));
}

export async function subscriptionHash(subscription: PushSubscription): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(subscription.endpoint));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
}

export async function pushRegistration(): Promise<ServiceWorkerRegistration> {
  await navigator.serviceWorker.register('/sw.js', { updateViaCache: 'none' });
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      navigator.serviceWorker.ready,
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error('通知组件尚未就绪，请刷新页面后重试。')), 15000);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

export async function removeBrowserPush(): Promise<void> {
  if (!('serviceWorker' in navigator)) return;
  const registration = await navigator.serviceWorker.getRegistration('/');
  const subscription = await registration?.pushManager?.getSubscription();
  if (!subscription) return;
  try {
    await api.disableWebPush(subscription.endpoint);
  } finally {
    await subscription.unsubscribe();
  }
}
