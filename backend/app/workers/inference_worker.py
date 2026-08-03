"""
推理 Worker 核心模块
===================
对视频流帧执行 YOLO 推理，将检测结果推送到 Redis 和数据库。
检测到异常时通过 SSE 广播告警。

职责:
  1. 从视频流拉取帧 (RTSP / HLS / 本地摄像头)
  2. 执行 YOLO 目标检测
  3. 检测到异常时通过 SSE 广播告警
  4. 定期生成带标注的快照
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

from app.core.config import settings
from app.core.model_manager import get_model_manager
from app.services.notification_service import notification_service
from app.services.sse_manager import SSEManager

logger = logging.getLogger(__name__)

# 需要触发告警的类别及阈值
ALERT_CLASSES = {
    "water_accumulation": {"threshold": 0.55, "level": "critical"},
    "missing_manhole": {"threshold": 0.55, "level": "critical"},
    "damaged_manhole": {"threshold": 0.50, "level": "warning"},
    "shifted_manhole": {"threshold": 0.50, "level": "warning"},
    "intruder": {"threshold": 0.60, "level": "critical"},
    "illegal_parking": {"threshold": 0.55, "level": "warning"},
}


class InferenceWorker:
    """
    推理工作器 — 对单个视频流执行持续 AI 推理

    使用示例:
        worker = InferenceWorker(camera_id="cam-001", stream_url="rtsp://...")
        await worker.start()
        # ... 运行中 ...
        await worker.stop()
    """

    def __init__(
        self,
        camera_id: str,
        stream_url: str,
        inference_interval: float = 2.0,
        snapshot_interval: float = 30.0,
        confidence_threshold: float = 0.45,
    ) -> None:
        self.camera_id = camera_id
        self.stream_url = stream_url
        self.inference_interval = inference_interval
        self.snapshot_interval = snapshot_interval
        self.confidence_threshold = confidence_threshold

        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._cap: Optional[cv2.VideoCapture] = None
        self._last_snapshot_time = 0.0
        self._frame_count = 0
        self._inference_count = 0
        self._alert_count = 0
        self._sse = SSEManager.get_instance()

    async def start(self) -> None:
        """启动推理循环"""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("🎬 [%s] 推理 Worker 已启动", self.camera_id)

    async def stop(self) -> None:
        """停止推理"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        if self._cap and self._cap.isOpened():
            self._cap.release()
        logger.info(
            "🛑 [%s] 推理 Worker 已停止 | 帧: %d | 推理: %d | 告警: %d",
            self.camera_id,
            self._frame_count,
            self._inference_count,
            self._alert_count,
        )

    async def _run_loop(self) -> None:
        """主推理循环"""
        retry_delay = 1

        while self._running:
            try:
                if self._cap is None or not self._cap.isOpened():
                    self._cap = cv2.VideoCapture(self.stream_url)
                    if not self._cap.isOpened():
                        logger.warning(
                            "⚠️  [%s] 无法打开视频流，%ds 后重试",
                            self.camera_id,
                            retry_delay,
                        )
                        await asyncio.sleep(retry_delay)
                        retry_delay = min(retry_delay * 2, 30)
                        continue

                    retry_delay = 1
                    self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

                ret, frame = self._cap.read()
                if not ret or frame is None:
                    logger.warning("⚠️  [%s] 读取帧失败，尝试重连", self.camera_id)
                    self._cap.release()
                    self._cap = None
                    await asyncio.sleep(1)
                    continue

                self._frame_count += 1

                # 按间隔执行推理
                frames_per_infer = max(1, int(self.inference_interval * 25))
                if self._frame_count % frames_per_infer == 1:
                    await self._process_frame(frame)

                # 定期保存快照
                now = time.time()
                if now - self._last_snapshot_time >= self.snapshot_interval:
                    self._save_snapshot(frame)
                    self._last_snapshot_time = now

                await asyncio.sleep(0.01)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("❌ [%s] 推理循环异常: %s", self.camera_id, exc)
                await asyncio.sleep(5)

    async def _process_frame(self, frame: np.ndarray) -> None:
        """对单帧执行推理并检查告警"""
        manager = get_model_manager()
        if not manager.is_ready:
            return

        try:
            detections = manager.predict(
                frame,
                conf=self.confidence_threshold,
                iou=settings.MODEL_IOU_THRESHOLD,
            )
            self._inference_count += 1

            if not detections:
                return

            alerts_triggered = []
            for det in detections:
                class_name = det.get("class_name", "")
                confidence = det.get("confidence", 0)

                alert_cfg = ALERT_CLASSES.get(class_name)
                if alert_cfg and confidence >= alert_cfg["threshold"]:
                    alerts_triggered.append(
                        {
                            "class_name": class_name,
                            "confidence": confidence,
                            "level": alert_cfg["level"],
                            "bbox": det.get("bbox", []),
                        }
                    )

            if alerts_triggered:
                await self._fire_alerts(alerts_triggered, frame)

        except Exception as exc:
            logger.error("❌ [%s] 推理失败: %s", self.camera_id, exc)

    async def _fire_alerts(
        self,
        triggered: list[dict[str, Any]],
        frame: np.ndarray,
    ) -> None:
        """触发告警并广播"""
        snapshot_path = self._save_snapshot(frame, prefix="alert_")

        for item in triggered:
            self._alert_count += 1
            alert_data = {
                "alert_id": str(uuid.uuid4()),
                "alert_type": item["class_name"],
                "level": item["level"],
                "title": f"[{self.camera_id}] {item['class_name']} 检测",
                "description": f"AI 检测到 {item['class_name']}，置信度: {item['confidence']:.2f}",
                "camera_id": self.camera_id,
                "snapshot_url": str(snapshot_path) if snapshot_path else None,
                "detection_confidence": round(item["confidence"], 4),
                "bbox_coordinates": item.get("bbox", []),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            await self._sse.broadcast_alert(alert_data)
            try:
                from app.core.redis_client import redis_client

                if redis_client.is_connected:
                    await redis_client.publish_alert(alert_data)
            except Exception:
                pass
            await notification_service.notify_alert(alert_data)
            logger.warning(
                "🚨 [%s] 告警: %s (置信度: %.2f)",
                self.camera_id,
                item["class_name"],
                item["confidence"],
            )

    def _save_snapshot(
        self,
        frame: np.ndarray,
        prefix: str = "snapshot_",
    ) -> Optional[Path]:
        """保存快照到磁盘"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            filename = f"{prefix}{self.camera_id}_{timestamp}.jpg"
            filepath = settings.screenshot_dir / filename
            cv2.imwrite(str(filepath), frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            return filepath
        except Exception as exc:
            logger.error("保存快照失败: %s", exc)
            return None


class WorkerPool:
    """推理 Worker 池 — 管理多个摄像头的推理任务"""

    def __init__(self) -> None:
        self._workers: dict[str, InferenceWorker] = {}

    async def add_worker(
        self,
        camera_id: str,
        stream_url: str,
        **kwargs: Any,
    ) -> InferenceWorker:
        """添加并启动一个 Worker"""
        if camera_id in self._workers:
            await self.remove_worker(camera_id)

        worker = InferenceWorker(camera_id=camera_id, stream_url=stream_url, **kwargs)
        self._workers[camera_id] = worker
        await worker.start()
        return worker

    async def remove_worker(self, camera_id: str) -> None:
        """移除并停止 Worker"""
        worker = self._workers.pop(camera_id, None)
        if worker:
            await worker.stop()

    async def stop_all(self) -> None:
        """停止所有 Worker"""
        for camera_id in list(self._workers.keys()):
            await self.remove_worker(camera_id)

    def get_status(self) -> dict[str, dict[str, Any]]:
        """获取所有 Worker 状态"""
        return {
            cid: {
                "running": w._running,
                "frame_count": w._frame_count,
                "inference_count": w._inference_count,
                "alert_count": w._alert_count,
            }
            for cid, w in self._workers.items()
        }

    @property
    def active_workers(self) -> int:
        return sum(1 for w in self._workers.values() if w._running)


# 全局单例
worker_pool = WorkerPool()
