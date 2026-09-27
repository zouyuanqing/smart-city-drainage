"""
视频流处理服务
---------------
管理 FFmpeg 子进程，将 RTSP 流转码为 HLS。
支持进程健康监控和自动重启。
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.core.security import media_protocol_allowlist, resolve_outbound_url
from app.workers.inference_worker import MediaResourceLimitExceeded, WorkerPool

logger = logging.getLogger(__name__)


@dataclass
class StreamProcess:
    """视频流转码进程包装"""

    camera_id: str
    source_url: str
    connection_url: str
    host_header: str
    hls_output_dir: Path
    ffmpeg_process: Optional[subprocess.Popen] = None
    started_at: float = field(default_factory=time.time)
    restart_count: int = 0
    is_healthy: bool = False
    error_message: Optional[str] = None

    @property
    def hls_playlist_path(self) -> str:
        """HLS 播放列表路径"""
        return str(self.hls_output_dir / "index.m3u8")

    @property
    def uptime_seconds(self) -> float:
        """运行时长"""
        return time.time() - self.started_at


class StreamService:
    """
    视频流管理服务

    功能:
      - 启动 FFmpeg 子进程: RTSP → HLS
      - 进程健康监控
      - 自动重启 (exponential backoff)
      - 优雅关闭
    """

    MAX_RESTART_DELAY = 60  # 最大重启延迟 (秒)
    HEALTH_CHECK_INTERVAL = 10  # 健康检查间隔

    def __init__(self) -> None:
        self._streams: dict[str, StreamProcess] = {}
        self._start_lock = asyncio.Lock()
        self._health_task: Optional[asyncio.Task] = None
        self.worker_pool = WorkerPool()

    # ----- 生命周期 -----

    async def start_stream(
        self,
        camera_id: str,
        rtsp_url: str,
        *,
        source_url: str | None = None,
    ) -> StreamProcess:
        """Start a connection-pinned stream within the media budget."""
        async with self._start_lock:
            if (
                camera_id not in self._streams
                and len(self._streams) >= settings.MAX_ACTIVE_STREAMS
            ):
                raise MediaResourceLimitExceeded("active stream limit reached")

            target = resolve_outbound_url(
                rtsp_url,
                allowed_schemes={"rtsp", "rtsps", "rtmp", "rtmps"},
            )
            original_url = source_url or rtsp_url

            hls_dir = settings.hls_output_dir / camera_id
            hls_dir.mkdir(parents=True, exist_ok=True)

            if camera_id in self._streams:
                await self.stop_stream(camera_id)

            stream = StreamProcess(
                camera_id=camera_id,
                source_url=original_url,
                connection_url=target.connection_url,
                host_header=target.host_header,
                hls_output_dir=hls_dir,
            )

            try:
                stream.ffmpeg_process = self._launch_ffmpeg(
                    rtsp_url=target.connection_url,
                    output_dir=hls_dir,
                    camera_id=camera_id,
                    host_header=target.host_header,
                )
                stream.is_healthy = True
                logger.info(
                    "✅ [%s] RTSP→HLS 转码已启动: %s",
                    camera_id,
                    stream.hls_playlist_path,
                )
            except Exception as exc:
                stream.error_message = str(exc)
                stream.is_healthy = False
                logger.error("❌ [%s] 启动转码失败: %s", camera_id, exc)

            self._streams[camera_id] = stream

            try:
                await self.worker_pool.add_worker(
                    camera_id=camera_id,
                    stream_url=target.connection_url,
                    host_header=target.host_header,
                    inference_interval=2.0,
                    confidence_threshold=0.45,
                )
            except MediaResourceLimitExceeded:
                await self.stop_stream(camera_id)
                raise
            except Exception as exc:
                logger.warning("启动推理 Worker 失败: %s", exc)

            return stream

    async def stop_stream(self, camera_id: str) -> None:
        """停止转码进程"""
        stream = self._streams.pop(camera_id, None)
        if stream is None:
            return

        if stream.ffmpeg_process:
            try:
                # 发送 SIGTERM
                stream.ffmpeg_process.send_signal(signal.SIGTERM)
                try:
                    stream.ffmpeg_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    # 强制杀死
                    stream.ffmpeg_process.kill()
                    stream.ffmpeg_process.wait()
            except Exception as exc:
                logger.warning("⚠️  [%s] 停止 FFmpeg 进程时异常: %s", camera_id, exc)

        # 清理 HLS 文件
        try:
            shutil.rmtree(str(stream.hls_output_dir), ignore_errors=True)
        except Exception:
            pass

        try:
            await self.worker_pool.remove_worker(camera_id)
        except Exception:
            pass

        logger.info("🛑 [%s] 转码已停止", camera_id)

    async def stop_all(self) -> None:
        """停止所有转码进程"""
        for camera_id in list(self._streams.keys()):
            await self.stop_stream(camera_id)

        if self._health_task and not self._health_task.done():
            self._health_task.cancel()

        await self.worker_pool.stop_all()

    # ----- 健康监控 -----

    async def start_health_monitor(self) -> None:
        """启动后台健康检查协程"""
        if self._health_task and not self._health_task.done():
            return

        self._health_task = asyncio.create_task(self._health_loop())
        logger.info("🏥 流健康监控已启动 (间隔 %ds)", self.HEALTH_CHECK_INTERVAL)

    async def _health_loop(self) -> None:
        """健康检查主循环"""
        while True:
            await asyncio.sleep(self.HEALTH_CHECK_INTERVAL)

            for camera_id, stream in list(self._streams.items()):
                try:
                    healthy = await self._check_process_health(stream)
                    if not healthy and stream.is_healthy:
                        logger.warning("⚠️  [%s] 转码进程异常，尝试重启...", camera_id)
                        await self._restart_stream(camera_id)
                except Exception as exc:
                    logger.error("❌ [%s] 健康检查异常: %s", camera_id, exc)

    async def _check_process_health(self, stream: StreamProcess) -> bool:
        """检查单个进程健康状态"""
        proc = stream.ffmpeg_process
        if proc is None:
            return False

        # 检查进程是否存活
        if proc.poll() is not None:
            stream.error_message = f"FFmpeg 进程退出, 返回码: {proc.returncode}"
            return False

        # 检查 HLS 文件是否更新
        playlist_path = stream.hls_output_dir / "index.m3u8"
        if playlist_path.exists():
            mtime = playlist_path.stat().st_mtime
            if time.time() - mtime < settings.HLS_SEGMENT_TIME * 3:
                return True

        return False

    async def _restart_stream(self, camera_id: str) -> None:
        """重启转码 (带指数退避)"""
        stream = self._streams.get(camera_id)
        if stream is None:
            return

        # 停止旧进程
        if stream.ffmpeg_process:
            try:
                stream.ffmpeg_process.kill()
                stream.ffmpeg_process.wait()
            except Exception:
                pass

        # 指数退避
        delay = min(2**stream.restart_count, self.MAX_RESTART_DELAY)
        stream.restart_count += 1
        logger.info(
            "⏳ [%s] 等待 %ds 后重启 (第 %d 次)",
            camera_id,
            delay,
            stream.restart_count,
        )

        await asyncio.sleep(delay)

        # 重新解析并固定到新的公网地址，避免重连时发生 DNS rebinding。
        try:
            target = resolve_outbound_url(
                stream.source_url,
                allowed_schemes={"rtsp", "rtsps", "rtmp", "rtmps"},
            )
            stream.connection_url = target.connection_url
            stream.host_header = target.host_header
            stream.ffmpeg_process = self._launch_ffmpeg(
                rtsp_url=target.connection_url,
                output_dir=stream.hls_output_dir,
                camera_id=camera_id,
                host_header=target.host_header,
            )
            stream.is_healthy = True
            stream.started_at = time.time()
            stream.error_message = None
            logger.info("✅ [%s] 重启成功", camera_id)
        except Exception as exc:
            stream.error_message = str(exc)
            stream.is_healthy = False
            logger.error("❌ [%s] 重启失败: %s", camera_id, exc)

    # ----- FFmpeg 进程管理 -----

    def _launch_ffmpeg(
        self,
        rtsp_url: str,
        output_dir: Path,
        camera_id: str,
        *,
        host_header: str | None = None,
    ) -> subprocess.Popen:
        """
        启动 FFmpeg 子进程，将 RTSP 转为 HLS

        FFmpeg 命令示例:
          ffmpeg -rtsp_transport tcp -i rtsp://... \
            -c:v libx264 -preset veryfast -crf 28 \
            -hls_time 4 -hls_list_size 6 -hls_flags delete_segments \
            -f hls output/index.m3u8
        """
        ffmpeg_path = settings.FFMPEG_PATH
        playlist_path = output_dir / "index.m3u8"

        timeout_us = settings.MEDIA_READ_TIMEOUT_SECONDS * 1_000_000
        cmd = [
            ffmpeg_path,
            "-nostdin",
            "-rw_timeout",
            str(timeout_us),
            "-protocol_whitelist",
            media_protocol_allowlist(rtsp_url),
            "-threads",
            "1",
            "-rtsp_transport",
            "tcp",
            "-stimeout",
            str(timeout_us),
        ]
        if host_header:
            cmd.extend(["-headers", f"Host: {host_header}\r\n"])
        cmd.extend(
            [
                "-i",
                rtsp_url,
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "28",
                "-r",
                "25",
                "-g",
                "50",
                "-threads",
                "1",
                "-reorder_queue_size",
                "1024",
                "-hls_time",
                str(settings.HLS_SEGMENT_TIME),
                "-hls_list_size",
                str(settings.HLS_LIST_SIZE),
                "-hls_flags",
                "delete_segments+append_list",
                "-hls_segment_filename",
                str(output_dir / "segment_%03d.ts"),
                "-f",
                "hls",
                str(playlist_path),
            ]
        )

        logger.debug("🚀 [%s] 启动 FFmpeg: %s", camera_id, " ".join(cmd))

        # 启动子进程 (不等待)
        preexec_fn = getattr(os, "setsid", None) if os.name != "nt" else None
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            preexec_fn=preexec_fn,  # type: ignore[attr-defined]
        )

        # 给 FFmpeg 一点启动时间
        time.sleep(1.5)

        if process.poll() is not None:
            stderr_output = ""
            try:
                if process.stderr is not None:
                    stderr_output = process.stderr.read().decode(
                        "utf-8", errors="replace"
                    )[-500:]
            except Exception:
                pass
            raise RuntimeError(
                "FFmpeg 进程启动后立即退出 "
                f"(code={process.returncode}): {stderr_output}"
            )

        return process

    # ----- 状态查询 -----

    def get_stream(self, camera_id: str) -> Optional[StreamProcess]:
        """Return one active stream without exposing the mutable registry."""
        return self._streams.get(camera_id)

    def get_stream_status(self, camera_id: str) -> Optional[dict]:
        """获取指定流的状态"""
        stream = self._streams.get(camera_id)
        if stream is None:
            return None

        playlist = stream.hls_playlist_path if stream.is_healthy else None
        return {
            "camera_id": stream.camera_id,
            "is_healthy": stream.is_healthy,
            "uptime_seconds": stream.uptime_seconds,
            "restart_count": stream.restart_count,
            "hls_playlist": playlist,
            "error": stream.error_message,
            "inference_worker": (
                self.worker_pool.get_status()
                .get(camera_id, {})
                .get("is_running", False)
            ),
        }

    def get_all_streams_status(self) -> list[dict]:
        """获取所有流状态"""
        return [
            status
            for camera_id in self._streams
            if (status := self.get_stream_status(camera_id)) is not None
        ]


# 单例
stream_service = StreamService()
