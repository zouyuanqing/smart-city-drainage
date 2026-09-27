"""
AI 推理 Worker
===============
独立异步工作进程，对视频流帧执行 YOLO 推理。
"""

from app.workers import inference_worker as _inference_worker

InferenceWorker = _inference_worker.InferenceWorker
WorkerPool = _inference_worker.WorkerPool
worker_pool = _inference_worker.worker_pool

__all__ = ["InferenceWorker", "WorkerPool", "worker_pool"]
