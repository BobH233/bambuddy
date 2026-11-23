"""Shared, authenticated-by-router H.264 HLS camera sessions.

Only compressed video is sent to viewers. A local one-fps JPEG output lets
existing snapshots and print monitoring reuse the same printer connection.
"""

import asyncio
import contextlib
import logging
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from backend.app.services.camera import close_tls_proxy, create_tls_proxy, get_camera_port, get_ffmpeg_path, wait_for_camera_capture
from backend.app.services.camera_profiles import get_camera_profile
from backend.app.utils.ffmpeg_output import refuse_rtsp_redirect, rtsp_log_event, summarize_ffmpeg_stderr

logger = logging.getLogger(__name__)
IDLE_SECONDS = 25
STARTUP_SECONDS = 40


class HLSSession:
    def __init__(self, printer):
        self.printer_id = printer.id
        self.identity = (printer.ip_address, printer.access_code, printer.model)
        self.directory = Path(tempfile.mkdtemp(prefix=f"bambuddy-hls-{printer.id}-"))
        self.process = None
        self.task = None
        self.started = time.time()
        self.generation_started = self.started
        self.sequence_start = int(self.started * 1000)
        self.touched = time.monotonic()
        self.last_frame = None
        self.frame_time = None
        self.playlist_time = None
        self.generation = ""
        self.error = None
        self.stopping_process = False

    async def start(self):
        self.task = asyncio.create_task(self.run(), name=f"camera-hls-{self.printer_id}")

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        shutil.rmtree(self.directory, ignore_errors=True)

    async def _read_log(self, process, code):
        lines = []
        while line := await process.stderr.readline():
            text = line.decode(errors="replace").replace(code, "<redacted>")
            if rtsp_log_event(line) == "redirected":
                refuse_rtsp_redirect(process, line)
            lines.append(text)
            lines = lines[-24:]
        if not self.stopping_process and process.returncode not in (None, 0) and lines:
            logger.warning("HLS camera %s: %s", self.printer_id, summarize_ffmpeg_stderr("".join(lines)))

    async def run(self):
        failures = 0
        try:
            while time.monotonic() - self.touched < IDLE_SECONDS:
                host, code, model = self.identity
                proxy = None
                process = None
                log_task = None
                had_frames = False
                try:
                    await wait_for_camera_capture(host)
                    port, proxy = await create_tls_proxy(host, get_camera_port(model))
                    profile = get_camera_profile(model)
                    self.generation = uuid.uuid4().hex[:12]
                    self.generation_started = time.time()
                    self.sequence_start = max(self.sequence_start + 1, int(self.generation_started * 1000))
                    for old in self.directory.iterdir():
                        old.unlink(missing_ok=True)
                    self.playlist_time = None
                    self.last_frame = None
                    self.frame_time = None
                    self.stopping_process = False
                    args = [
                        get_ffmpeg_path(), "-hide_banner", "-loglevel", "info",
                        "-rtsp_transport", "tcp", "-timeout", "10000000",
                        "-probesize", "1000000", "-analyzeduration", "1000000",
                        *profile.extra_ffmpeg_input_args,
                        "-i", f"rtsp://bblp:{code}@127.0.0.1:{port}/streaming/live/1",
                        "-map", "0:v:0", "-an", "-c:v", "copy",
                        "-f", "hls", "-hls_time", "2", "-hls_list_size", "6",
                        "-hls_delete_threshold", "4",
                        "-start_number", str(self.sequence_start),
                        "-hls_flags", "delete_segments+temp_file+independent_segments+omit_endlist",
                        "-hls_segment_filename", str(self.directory / f"{self.generation}_%09d.ts"),
                        str(self.directory / "index.m3u8"),
                        "-map", "0:v:0", "-an", "-vf", "fps=1", "-q:v", "5",
                        "-f", "image2", "-update", "1", "-atomic_writing", "1",
                        str(self.directory / "preview.jpg"),
                    ]
                    process = await asyncio.create_subprocess_exec(
                        *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
                    )
                    self.process = process
                    logger.info("H.264 HLS camera started (printer=%s, pid=%s)", self.printer_id, process.pid)
                    log_task = asyncio.create_task(self._read_log(process, code))
                    spawn = time.monotonic()
                    preview_stamp = None
                    while process.returncode is None and time.monotonic() - self.touched < IDLE_SECONDS:
                        preview = self.directory / "preview.jpg"
                        with contextlib.suppress(FileNotFoundError):
                            stamp = preview.stat().st_mtime_ns
                            if stamp != preview_stamp:
                                frame = preview.read_bytes()
                                if frame.startswith(b"\xff\xd8") and frame.endswith(b"\xff\xd9"):
                                    self.last_frame = frame
                                    self.frame_time = time.time()
                                    preview_stamp = stamp
                                    had_frames = True
                                    self.error = None
                        with contextlib.suppress(FileNotFoundError):
                            self.playlist_time = (self.directory / "index.m3u8").stat().st_mtime
                        age = time.time() - self.playlist_time if self.playlist_time else time.monotonic() - spawn
                        if age > STARTUP_SECONDS:
                            self.error = "Camera stream timed out"
                            break
                        await asyncio.sleep(0.5)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.error = "Camera stream could not start"
                    logger.warning("HLS camera %s could not start", self.printer_id)
                finally:
                    self.stopping_process = True
                    if process and process.returncode is None:
                        with contextlib.suppress(ProcessLookupError):
                            process.terminate()
                        try:
                            await asyncio.wait_for(process.wait(), 2)
                        except TimeoutError:
                            with contextlib.suppress(ProcessLookupError):
                                process.kill()
                            await process.wait()
                    if log_task:
                        await log_task
                    self.process = None
                    if proxy:
                        await close_tls_proxy(proxy)
                failures = 0 if had_frames else failures + 1
                if failures >= 5:
                    self.error = "Camera stream unavailable"
                    break
                if time.monotonic() - self.touched < IDLE_SECONDS:
                    await asyncio.sleep(min(2 ** failures, 5))
        finally:
            logger.info("H.264 HLS camera stopped (printer=%s)", self.printer_id)

    async def playlist(self):
        self.touched = time.monotonic()
        deadline = time.monotonic() + STARTUP_SECONDS
        while time.monotonic() < deadline:
            self.touched = time.monotonic()
            if self.task.done():
                raise RuntimeError(self.error or "Camera stream ended")
            path = self.directory / "index.m3u8"
            if path.exists() and self.playlist_time and time.time() - self.playlist_time < 10:
                text = path.read_text()
                if "#EXTINF:" in text:
                    return text.replace("#EXTM3U\n", f"#EXTM3U\n#EXT-X-DISCONTINUITY-SEQUENCE:{self.sequence_start}\n", 1)
            await asyncio.sleep(0.2)
        raise RuntimeError(self.error or "Camera stream is still starting")

    def status(self):
        active = self.task is not None and not self.task.done()
        age = time.time() - self.frame_time if self.frame_time else None
        return {
            "active": active, "has_frames": self.last_frame is not None,
            "seconds_since_frame": age, "stream_uptime": time.time() - self.started,
            "stalled": active and time.time() - self.generation_started > STARTUP_SECONDS and (age is None or age > 15),
            "transport": "hls", "codec": "h264",
        }


class HLSManager:
    def __init__(self):
        self.sessions = {}
        self.lock = asyncio.Lock()

    def get(self, printer_id):
        session = self.sessions.get(printer_id)
        return session if session and session.task and not session.task.done() else None

    def active_pids(self):
        return {s.process.pid for s in self.sessions.values() if s.process and s.process.returncode is None}

    async def ensure(self, printer):
        async with self.lock:
            existing = self.sessions.get(printer.id)
            if existing and (existing.identity != (printer.ip_address, printer.access_code, printer.model) or existing.task.done()):
                await existing.close()
                self.sessions.pop(printer.id, None)
                existing = None
            if existing is None:
                existing = HLSSession(printer)
                self.sessions[printer.id] = existing
                await existing.start()
            existing.touched = time.monotonic()
            return existing

    async def forget(self, printer_id):
        async with self.lock:
            if session := self.sessions.pop(printer_id, None):
                await session.close()

    async def cleanup(self):
        async with self.lock:
            for pid, session in list(self.sessions.items()):
                if session.task.done():
                    await session.close()
                    self.sessions.pop(pid, None)

    async def shutdown(self):
        for pid in list(self.sessions):
            await self.forget(pid)


hls_manager = HLSManager()
