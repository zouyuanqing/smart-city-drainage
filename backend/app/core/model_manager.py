"""
AI 模型管理器 (ModelManager) — 核心热切换模块
-----------------------------------------------
实现工业级的模型热切换 (Hot-Switching)，支持零停机更新 YOLO 模型。

架构设计:
  - 单例模式，全局唯一
  - 后台线程预加载新模型 → 验证完整性 → 原子替换全局指针 → 卸载旧模型
  - 全程加锁保护，保证推理服务不中断
  - 维护模型版本仓库，记录部署历史

Author: Smart City Neural Endpoints Team
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


# ============================================================
# 类型定义
# ============================================================


class ModelStatus(str, Enum):
    """模型加载状态枚举"""

    LOADING = "loading"  # 正在加载
    ACTIVE = "active"  # 已激活，正在使用
    UNLOADING = "unloading"  # 正在卸载
    ERROR = "error"  # 加载/验证失败
    STANDBY = "standby"  # 已加载但未激活 (预加载完成)


@dataclass
class ModelMetadata:
    """模型元数据"""

    version: str
    file_path: str
    file_size_bytes: int = 0
    sha256_hash: str = ""
    model_type: str = "yolov8"
    status: ModelStatus = ModelStatus.LOADING
    loaded_at: Optional[float] = None
    inference_count: int = 0
    avg_inference_ms: float = 0.0
    metrics: dict[str, Any] = field(default_factory=dict)


# ============================================================
# ModelManager — 单例核心类
# ============================================================


class ModelManager:
    """
    AI 模型管理器 (线程安全单例)

    职责:
      1. 维护模型仓库 (磁盘上的 .pt/.onnx 文件)
      2. 预加载 + 原子切换
      3. 旧模型显存回收
      4. 版本历史记录

    使用示例:
        manager = ModelManager.get_instance()
        manager.load_model("v2")  # 预加载 v2 但不激活
        manager.switch_model("v2")  # 原子切换到 v2
        results = manager.predict(frame)
    """

    _instance: Optional["ModelManager"] = None
    _lock: threading.Lock = threading.Lock()
    _instance_lock: threading.Lock = threading.Lock()

    def __init__(self) -> None:
        """私有构造 — 请使用 get_instance()"""
        if not hasattr(self, "_initialized"):
            self._initialized = False

    @classmethod
    def get_instance(cls) -> "ModelManager":
        """
        获取全局单例实例 (双重检查锁定)

        Returns:
            ModelManager 全局唯一实例
        """
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    instance = cls.__new__(cls)
                    object.__setattr__(instance, "_initialized", False)
                    instance._initialize()
                    cls._instance = instance
        return cls._instance

    def _initialize(self) -> None:
        """初始化管理器内部状态"""
        if self._initialized:
            return

        # ---- 内部状态 ----
        self._model: Any = None  # 当前活跃的 YOLO 模型对象
        self._standby_model: Any = None  # 预加载的备用模型
        self._active_version: str = ""  # 当前活跃版本名
        self._standby_version: str = ""  # 预加载版本名
        self._model_registry: dict[str, ModelMetadata] = {}  # 版本注册表
        self._switch_lock = threading.RLock()  # 切换专用可重入锁
        self._inference_lock = threading.RLock()  # 推理互斥锁

        # 回调列表
        self._on_switch_callbacks: list[Callable[[str, str], None]] = []

        # 标记初始化完成
        object.__setattr__(self, "_initialized", True)

        logger.info(
            "🚀 ModelManager 单例初始化完成 | 设备: %s", settings.MODEL_INFERENCE_DEVICE
        )

        # 启动时自动加载默认版本
        try:
            default_version = settings.DEFAULT_MODEL_VERSION
            self.load_and_activate(default_version)
        except Exception as exc:
            logger.error(
                "❌ 默认模型 %s 加载失败: %s", settings.DEFAULT_MODEL_VERSION, exc
            )
            logger.warning("⚠️  系统将在无模型状态下运行，推理接口将返回 503")

    # ================================================================
    # 模型仓库扫描
    # ================================================================

    def scan_repository(self) -> dict[str, ModelMetadata]:
        """
        扫描模型仓库目录，发现所有可用模型版本

        Returns:
            {version_name: ModelMetadata} 映射
        """
        storage = settings.model_storage_dir
        discovered: dict[str, ModelMetadata] = {}

        for ext in ("*.pt", "*.onnx", "*.engine"):
            for file_path in storage.glob(ext):
                version = file_path.stem  # 文件名作为版本名
                file_stat = file_path.stat()

                metadata = ModelMetadata(
                    version=version,
                    file_path=str(file_path.resolve()),
                    file_size_bytes=file_stat.st_size,
                    model_type=self._guess_model_type(file_path),
                )

                # 计算 SHA256
                try:
                    metadata.sha256_hash = self._compute_sha256(file_path)
                except Exception:
                    metadata.sha256_hash = "unavailable"

                discovered[version] = metadata

        self._model_registry.update(discovered)
        logger.info("📁 扫描模型仓库: 发现 %d 个模型版本", len(discovered))
        for v, m in discovered.items():
            logger.info(
                "   └─ %s (%s, %.1f MB)", v, m.model_type, m.file_size_bytes / 1e6
            )

        return discovered

    # ================================================================
    # 模型加载
    # ================================================================

    def load_model(self, version: str) -> Any:
        """
        加载指定版本的模型到内存 (不作为活跃模型)

        此方法线程安全，可在后台线程中调用进行预加载。

        Args:
            version: 模型版本名 (如 "v1", "v2", "yolov10s")

        Returns:
            加载的模型对象

        Raises:
            FileNotFoundError: 模型文件不存在
            RuntimeError: 模型加载失败
        """
        file_path = self._resolve_model_path(version)
        if file_path is None:
            raise FileNotFoundError(
                f"模型版本 '{version}' 不在仓库中，请先放入 {settings.model_storage_dir}"
            )

        logger.info("🔄 [%s] 开始加载模型: %s", version, file_path)

        # 更新注册表状态
        if version in self._model_registry:
            self._model_registry[version].status = ModelStatus.LOADING

        try:
            model = self._load_yolo_model(file_path)
            logger.info("✅ [%s] 模型加载成功", version)

            if version in self._model_registry:
                meta = self._model_registry[version]
                meta.status = ModelStatus.STANDBY
                meta.loaded_at = time.time()

            return model

        except Exception as exc:
            if version in self._model_registry:
                self._model_registry[version].status = ModelStatus.ERROR
            logger.error("❌ [%s] 模型加载失败: %s", version, exc)
            raise RuntimeError(f"模型 '{version}' 加载失败") from exc

    def load_and_activate(self, version: str) -> None:
        """
        加载模型并直接激活 (系统启动时使用，无旧模型需卸载)

        Args:
            version: 模型版本名
        """
        model = self.load_model(version)
        with self._switch_lock:
            self._model = model
            self._active_version = version
            if version in self._model_registry:
                self._model_registry[version].status = ModelStatus.ACTIVE
        logger.info("🎯 [%s] 已激活为当前推理模型", version)

    # ================================================================
    # 核心: 模型热切换 (Hot-Switching)
    # ================================================================

    def switch_model(self, target_version: str, verify: bool = True) -> bool:
        """
        **原子热切换**: 不中断推理服务，将活跃模型切换到目标版本。

        流程:
          1. 在后台线程预加载目标模型 → 验证完整性
          2. 加全局写锁 → 更新全局指针 → 释放锁
          3. 卸载旧模型释放显存
          4. 触发切换回调

        Args:
            target_version: 目标模型版本名
            verify: 是否在切换前验证 (跑一次空推理确认模型可用)

        Returns:
            切换是否成功

        Raises:
            ValueError: 目标版本与当前版本相同
            RuntimeError: 预加载或验证失败
        """
        if target_version == self._active_version:
            raise ValueError(
                f"目标版本 '{target_version}' 与当前活跃版本相同，无需切换"
            )

        logger.info(
            "🔥 开始模型热切换: %s → %s", self._active_version or "(无)", target_version
        )

        # --- Phase 1: 预加载目标模型 (不阻塞推理) ---
        try:
            standby_model = self.load_model(target_version)
        except Exception as exc:
            logger.error("❌ 预加载目标模型失败，切换中止: %s", exc)
            return False

        # --- Phase 2: 验证 (可选) ---
        if verify:
            logger.info("🔍 正在验证目标模型...")
            try:
                self._verify_model(standby_model)
                logger.info("✅ 目标模型验证通过")
            except Exception as exc:
                logger.error("❌ 目标模型验证失败，切换中止: %s", exc)
                self._unload_model(standby_model)
                return False

        # --- Phase 3: 原子替换 (加锁) ---
        old_model = None
        old_version = ""

        with self._switch_lock:
            old_model = self._model
            old_version = self._active_version

            # 原子替换全局指针
            self._model = standby_model
            self._active_version = target_version
            self._standby_model = None
            self._standby_version = ""

            # 更新状态
            if target_version in self._model_registry:
                self._model_registry[target_version].status = ModelStatus.ACTIVE
            if old_version and old_version in self._model_registry:
                self._model_registry[old_version].status = ModelStatus.UNLOADING

            logger.info(
                "⚡ 原子切换完成! 活跃模型: %s → %s", old_version, target_version
            )

        # --- Phase 4: 卸载旧模型 (锁外执行，耗时的显存回收) ---
        if old_model is not None:
            logger.info("🗑️  正在卸载旧模型 '%s' 并释放显存...", old_version)
            self._unload_model(old_model)
            if old_version in self._model_registry:
                self._model_registry[old_version].status = (
                    ModelStatus.ERROR
                )  # 标记已卸载
            logger.info("✅ 旧模型已卸载")

        # --- Phase 5: 触发回调 ---
        self._fire_switch_callbacks(old_version, target_version)

        return True

    def switch_model_async(self, target_version: str) -> threading.Thread:
        """
        在后台线程中异步执行热切换，立即返回

        Args:
            target_version: 目标模型版本名

        Returns:
            后台线程对象
        """
        thread = threading.Thread(
            target=self.switch_model,
            args=(target_version,),
            daemon=True,
            name=f"model-switch-{target_version}",
        )
        thread.start()
        logger.info("🚀 已启动后台线程执行模型热切换: → %s", target_version)
        return thread

    # ================================================================
    # 推理接口
    # ================================================================

    def predict(self, image: Any, **kwargs: Any) -> list[dict[str, Any]]:
        """
        对单张图像执行推理 (线程安全)

        Args:
            image: 图像数据 (numpy ndarray BGR/RGB, PIL Image, 或文件路径)
            **kwargs: 传递给 YOLO model.predict() 的额外参数

        Returns:
            检测结果列表: [{"class": "person", "confidence": 0.92, "bbox": [x1,y1,x2,y2]}, ...]
        """
        if self._model is None:
            raise RuntimeError("没有活跃的模型可用，请先加载模型")

        with self._inference_lock:
            start = time.perf_counter()

            results = self._model.predict(
                image,
                conf=kwargs.get("conf", settings.MODEL_CONFIDENCE_THRESHOLD),
                iou=kwargs.get("iou", settings.MODEL_IOU_THRESHOLD),
                device=kwargs.get("device", settings.MODEL_INFERENCE_DEVICE),
                verbose=False,
            )

            elapsed_ms = (time.perf_counter() - start) * 1000

        # 更新统计
        if self._active_version in self._model_registry:
            meta = self._model_registry[self._active_version]
            meta.inference_count += 1
            # 指数移动平均
            alpha = 0.1
            meta.avg_inference_ms = (
                alpha * elapsed_ms + (1 - alpha) * meta.avg_inference_ms
                if meta.avg_inference_ms > 0
                else elapsed_ms
            )

        # 解析结果
        detections = self._parse_results(results)

        logger.debug(
            "🎯 推理完成 | 耗时: %.1fms | 检测数: %d", elapsed_ms, len(detections)
        )
        return detections

    # ================================================================
    # 状态查询
    # ================================================================

    @property
    def active_version(self) -> str:
        """当前活跃模型版本"""
        return self._active_version

    @property
    def is_ready(self) -> bool:
        """模型是否就绪可用"""
        return self._model is not None

    def get_status(self) -> dict[str, Any]:
        """获取模型管理器完整状态"""
        return {
            "active_version": self._active_version,
            "is_ready": self.is_ready,
            "device": settings.MODEL_INFERENCE_DEVICE,
            "registry": {
                v: {
                    "status": m.status.value,
                    "model_type": m.model_type,
                    "file_size_mb": round(m.file_size_bytes / 1e6, 1),
                    "sha256": m.sha256_hash[:16] + "..." if m.sha256_hash else "",
                    "inference_count": m.inference_count,
                    "avg_inference_ms": round(m.avg_inference_ms, 1),
                }
                for v, m in self._model_registry.items()
            },
        }

    def on_switch(self, callback: Callable[[str, str], None]) -> None:
        """
        注册模型切换回调

        Args:
            callback: 回调函数签名 callback(old_version: str, new_version: str)
        """
        self._on_switch_callbacks.append(callback)

    # ================================================================
    # 私有辅助方法
    # ================================================================

    def _load_yolo_model(self, file_path: str) -> Any:
        """从文件加载 YOLO 模型"""
        from ultralytics import YOLO

        model = YOLO(file_path)
        # 预热
        model.to(settings.MODEL_INFERENCE_DEVICE)
        return model

    def _unload_model(self, model: Any) -> None:
        """安全卸载模型，释放 GPU 显存"""
        try:
            import gc

            import torch

            del model
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.synchronize()
        except Exception as exc:
            logger.warning("模型卸载时出现警告: %s", exc)

    def _verify_model(self, model: Any) -> None:
        """验证模型: 使用空白的测试图像跑一次推理"""
        import numpy as np

        dummy = np.zeros((640, 640, 3), dtype=np.uint8)
        model.predict(
            dummy, conf=0.9, verbose=False, device=settings.MODEL_INFERENCE_DEVICE
        )

    def _resolve_model_path(self, version: str) -> Optional[str]:
        """根据版本名解析模型文件路径"""
        # 先检查已注册的
        if version in self._model_registry:
            path = self._model_registry[version].file_path
            if Path(path).exists():
                return path

        # 扫描仓库
        storage = settings.model_storage_dir
        for ext in (".pt", ".onnx", ".engine"):
            candidate = storage / f"{version}{ext}"
            if candidate.exists():
                return str(candidate.resolve())

        return None

    def _guess_model_type(self, file_path: Path) -> str:
        """根据后缀猜测模型类型"""
        suffix = file_path.suffix.lower()
        if suffix == ".pt":
            return "yolov8"  # Ultralytics 默认
        elif suffix == ".onnx":
            return "onnx"
        elif suffix == ".engine":
            return "tensorrt"
        return "unknown"

    def _compute_sha256(self, file_path: Path) -> str:
        """计算文件 SHA256"""
        sha = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha.update(chunk)
        return sha.hexdigest()

    def _parse_results(self, results: Any) -> list[dict[str, Any]]:
        """将 YOLO results 对象解析为标准化检测列表"""
        detections: list[dict[str, Any]] = []

        for result in results:
            if result.boxes is None:
                continue

            boxes = (
                result.boxes.xyxy.cpu().numpy() if hasattr(result.boxes, "xyxy") else []
            )
            confs = (
                result.boxes.conf.cpu().numpy() if hasattr(result.boxes, "conf") else []
            )
            cls_ids = (
                result.boxes.cls.cpu().numpy() if hasattr(result.boxes, "cls") else []
            )

            for i in range(len(boxes)):
                cls_id = int(cls_ids[i])
                class_name = (
                    result.names.get(cls_id, f"class_{cls_id}")
                    if hasattr(result, "names")
                    else f"class_{cls_id}"
                )

                detections.append(
                    {
                        "class_id": cls_id,
                        "class_name": class_name,
                        "confidence": round(float(confs[i]), 4),
                        "bbox": [round(float(x), 1) for x in boxes[i]],
                        "bbox_normalized": False,
                    }
                )

        return detections

    def _fire_switch_callbacks(self, old_version: str, new_version: str) -> None:
        """触发所有注册的切换回调"""
        for cb in self._on_switch_callbacks:
            try:
                cb(old_version, new_version)
            except Exception as exc:
                logger.error("切换回调执行异常: %s", exc)


# ============================================================
# 便捷函数
# ============================================================


def get_model_manager() -> ModelManager:
    """获取全局 ModelManager 实例"""
    return ModelManager.get_instance()
