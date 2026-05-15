"""
AI 推理 Worker
===============
独立异步工作进程，对视频流帧执行 YOLO 推理。
"""

from app.workers.inference_worker import InferenceWorker, WorkerPool, worker_pool

__all__ = ["InferenceWorker", "WorkerPool", "worker_pool"]
