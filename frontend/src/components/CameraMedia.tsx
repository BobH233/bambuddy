import { useEffect, useRef, useState, type ImgHTMLAttributes, type ReactElement, type MouseEventHandler } from 'react';
import Hls from 'hls.js';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';

interface Props {
  src: string | undefined;
  image: ReactElement<ImgHTMLAttributes<HTMLImageElement>>;
  onLoad?: () => void;
  onMouseDown?: MouseEventHandler<HTMLVideoElement>;
  onError?: () => void;
  onTransport?: (transport: 'hls' | 'mjpeg') => void;
}

export function CameraMedia({ src: initialSrc, image, onLoad, onError, onMouseDown, onTransport }: Props) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [restart, setRestart] = useState(0);
  const [needsGesture, setNeedsGesture] = useState(false);
  const retries = useRef(0);
  useEffect(() => { retries.current = 0; }, [initialSrc]);
  const [replacement, setReplacement] = useState<{ original: string; url: string } | null>(null);
  const src = replacement && replacement.original === initialSrc ? replacement.url : initialSrc;
  const sourceRef = useRef(src);
  sourceRef.current = src;
  useEffect(() => {
    const refresh = (event: Event) => {
      const { previousToken, token } = (event as CustomEvent<{ previousToken: string; token: string }>).detail;
      const current = sourceRef.current;
      if (!current || !initialSrc) return;
      const url = new URL(current, window.location.origin);
      if (url.searchParams.get('token') !== previousToken) return;
      url.searchParams.set('token', token);
      setReplacement({ original: initialSrc, url: url.pathname + url.search });
    };
    window.addEventListener('bambuddy-camera-token', refresh);
    return () => window.removeEventListener('bambuddy-camera-token', refresh);
  }, [initialSrc]);
  const [transport, setTransport] = useState<{ src: string; kind: 'hls' | 'mjpeg' } | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const callbacks = useRef({ onLoad, onError, onTransport });
  callbacks.current = { onLoad, onError, onTransport };
  const live = !!src && /\/camera\/stream\?/.test(src);
  const kind = live && transport?.src === src ? transport.kind : null;

  useEffect(() => {
    if (!live || !src) return;
    const controller = new AbortController();
    fetch(src.replace('/camera/stream?', '/camera/playback?'), {
      signal: controller.signal, cache: 'no-store',
    }).then(async response => {
      if (response.status === 404) return { transport: 'mjpeg' };
      if (!response.ok) {
        if (response.status === 401) void queryClient.invalidateQueries({ queryKey: ['camera-stream-token'] });
        throw new Error('Camera access failed');
      }
      return response.json();
    }).then(data => {
      const kind = data.transport === 'hls' ? 'hls' : 'mjpeg';
      setTransport({ src, kind });
      callbacks.current.onTransport?.(kind);
    }).catch(error => {
      if (error.name !== 'AbortError') callbacks.current.onError?.();
    });
    return () => controller.abort();
  }, [src, live, queryClient]);

  useEffect(() => {
    const video = videoRef.current;
    if (kind !== 'hls' || !src || !video) return;
    const url = src.replace(/\/camera\/stream\?/, '/camera/hls/index.m3u8?');
    let hls: Hls | undefined;
    let failed = false;
    let disposed = false;
    let recoveryTimer: ReturnType<typeof setTimeout> | undefined;
    let mediaRecoveryUsed = false;
    let hasPlayed = false;
    let gestureRequired = false;
    let lastTime = -1;
    let lastAdvance = Date.now();
    const fail = () => {
      if (!failed) {
        failed = true;
        hls?.stopLoad();
        if (retries.current < 3) {
          retries.current += 1;
          recoveryTimer = setTimeout(() => setRestart(value => value + 1), 1000);
        } else {
          callbacks.current.onError?.();
        }
      }
    };
    const play = () => {
      void video.play().catch(error => {
        if (disposed || error.name === 'AbortError') return;
        if (error.name === 'NotAllowedError') {
          gestureRequired = true;
          setNeedsGesture(true);
        }
        else fail();
      });
    };
    const playing = () => {
      hasPlayed = true;
      lastTime = video.currentTime;
      lastAdvance = Date.now();
      retries.current = 0;
      gestureRequired = false;
      setNeedsGesture(false);
      callbacks.current.onLoad?.();
    };
    const visible = () => {
      if (document.hidden) return;
      lastAdvance = Date.now();
      if (hls?.liveSyncPosition != null && video.currentTime < hls.liveSyncPosition - 8) {
        video.currentTime = hls.liveSyncPosition;
      }
      play();
    };
    video.muted = true;
    video.defaultMuted = true;
    video.playsInline = true;
    video.addEventListener('error', fail);
    video.addEventListener('loadedmetadata', play);
    video.addEventListener('loadeddata', play);
    video.addEventListener('playing', playing);
    document.addEventListener('visibilitychange', visible);
    if (Hls.isSupported()) {
      hls = new Hls({
        enableWorker: false, lowLatencyMode: false, liveSyncDurationCount: 2,
        liveMaxLatencyDurationCount: 5, maxLiveSyncPlaybackRate: 1.1,
        backBufferLength: 8, maxBufferLength: 10,
        manifestLoadPolicy: { default: {
          ...Hls.DefaultConfig.manifestLoadPolicy.default,
          maxLoadTimeMs: 45000,
        } },
        fragLoadPolicy: { default: {
          ...Hls.DefaultConfig.fragLoadPolicy.default,
          maxLoadTimeMs: 15000,
          errorRetry: { maxNumRetry: 2, retryDelayMs: 500, maxRetryDelayMs: 1500 },
        } },
      });
      hls.on(Hls.Events.ERROR, (_event, data) => {
        if (data.response?.code === 401) void queryClient.invalidateQueries({ queryKey: ['camera-stream-token'] });
        if (!data.fatal) return;
        if (data.type === Hls.ErrorTypes.MEDIA_ERROR && !mediaRecoveryUsed) {
          mediaRecoveryUsed = true;
          hls?.recoverMediaError();
        } else fail();
      });
      hls.on(Hls.Events.MANIFEST_PARSED, play);
      hls.loadSource(url);
      hls.attachMedia(video);
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = url;
    } else {
      fail();
    }
    const timer = window.setInterval(() => {
      if (document.hidden || failed || gestureRequired) return;
      if (video.currentTime !== lastTime) {
        lastTime = video.currentTime;
        lastAdvance = Date.now();
      } else if (Date.now() - lastAdvance > (hasPlayed ? 12000 : 45000)) {
        fail();
      }
    }, 3000);
    return () => {
      disposed = true;
      if (recoveryTimer) clearTimeout(recoveryTimer);
      window.clearInterval(timer);
      video.removeEventListener('error', fail);
      video.removeEventListener('loadedmetadata', play);
      video.removeEventListener('loadeddata', play);
      video.removeEventListener('playing', playing);
      document.removeEventListener('visibilitychange', visible);
      hls?.destroy();
      video.pause();
      video.removeAttribute('src');
      video.load();
    };
  }, [src, kind, queryClient, restart]);

  if (!live || kind === 'mjpeg') return image;
  if (kind !== 'hls') return null;
  return (
    <>
    <video
      ref={videoRef}
      autoPlay muted playsInline
      className={image.props.className}
      style={image.props.style}
      aria-label={image.props.alt}
      onMouseDown={onMouseDown}
      draggable={false}
    />
    {needsGesture && <button className="absolute z-20 rounded bg-bambu-green px-4 py-2 text-white" onClick={() => {
      void videoRef.current?.play().then(() => setNeedsGesture(false)).catch(() => {});
    }}>{t('camera.playVideo', '点击播放画面')}</button>}
    </>
  );
}
