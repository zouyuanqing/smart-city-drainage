"""
YOLO 推理服务
--------------
封装 ModelManager 的推理能力，提供图像预处理、后处理、以及异步推理接口。
支持 URL 图像拉取、Base64 解码、以及本地文件读取。
"""

from __future__ import annotations

import base64
import io
import logging
import time
from typing import Any, Optional

import numpy as np
from PIL import Image

from app.core.model_manager import get_model_manager

logger = logging.getLogger(__name__)


class InferenceService:
    """YOLO 推理服务 (无状态)"""

    # COCO 类别映射 (YOLOv8 默认)
    COCO_CLASSES: dict[int, str] = {
        0: "person", 1: "bicycle", 2: "car", 3: "motorcycle",
        4: "airplane", 5: "bus", 6: "train", 7: "truck",
        8: "boat", 9: "traffic light", 10: "fire hydrant",
        11: "stop sign", 12: "parking meter", 13: "bench",
        14: "bird", 15: "cat", 16: "dog", 17: "horse",
        18: "sheep", 19: "cow", 20: "elephant", 21: "bear",
        22: "zebra", 23: "giraffe", 24: "backpack", 25: "umbrella",
        26: "handbag", 27: "tie", 28: "suitcase", 29: "frisbee",
        30: "skis", 31: "snowboard", 32: "sports ball", 33: "kite",
        34: "baseball bat", 35: "baseball glove", 36: "skateboard",
        37: "surfboard", 38: "tennis racket", 39: "bottle",
        40: "wine glass", 41: "cup", 42: "fork", 43: "knife",
        44: "spoon", 45: "bowl", 46: "banana", 47: "apple",
        48: "sandwich", 49: "orange", 50: "broccoli", 51: "carrot",
        52: "hot dog", 53: "pizza", 54: "donut", 55: "cake",
        56: "chair", 57: "couch", 58: "potted plant", 59: "bed",
        60: "dining table", 61: "toilet", 62: "tv", 63: "laptop",
        64: "mouse", 65: "remote", 66: "keyboard", 67: "cell phone",
        68: "microwave", 69: "oven", 70: "toaster", 71: "sink",
        72: "refrigerator", 73: "book", 74: "clock", 75: "vase",
        76: "scissors", 77: "teddy bear", 78: "hair drier", 79: "toothbrush",
    }

    # 自定义类别 (排水系统专用)
    CUSTOM_CLASSES: dict[int, str] = {
        80: "water_accumulation",   # 积水
        81: "missing_manhole",      # 井盖缺失
        82: "damaged_manhole",      # 井盖破损
        83: "shifted_manhole",      # 井盖移位
        84: "intruder",             # 非法闯入
        85: "illegal_parking",      # 违停车辆
    }

    @classmethod
    def get_all_classes(cls) -> dict[int, str]:
        """合并标准与自定义类别"""
        return {**cls.COCO_CLASSES, **cls.CUSTOM_CLASSES}

    async def infer_from_base64(
        self,
        image_b64: str,
        confidence_threshold: float = 0.45,
        iou_threshold: float = 0.45,
        return_annotated: bool = False,
    ) -> dict[str, Any]:
        """
        从 Base64 编码图像执行推理

        Args:
            image_b64: Base64 编码的图像 (可包含 data:image/...;base64, 前缀)
            confidence_threshold: 置信度阈值
            iou_threshold: IoU 阈值
            return_annotated: 是否返回标注后图像的 Base64

        Returns:
            {"detections": [...], "inference_time_ms": float, ...}
        """
        # 解码 Base64
        if image_b64.startswith("data:"):
            # 去除 data URL 前缀
            image_b64 = image_b64.split(",", 1)[1]

        try:
            image_bytes = base64.b64decode(image_b64)
        except Exception as exc:
            raise ValueError(f"Base64 解码失败: {exc}")

        image = Image.open(io.BytesIO(image_bytes))
        image_np = np.array(image)

        return await self._run_inference(
            image_np, confidence_threshold, iou_threshold,
            return_annotated, image,
        )

    async def infer_from_url(
        self,
        image_url: str,
        confidence_threshold: float = 0.45,
        iou_threshold: float = 0.45,
        return_annotated: bool = False,
    ) -> dict[str, Any]:
        """
        从 URL 拉取图像并执行推理

        Args:
            image_url: 图像 URL
            confidence_threshold: 置信度阈值
            iou_threshold: IoU 阈值
            return_annotated: 是否返回标注后图像

        Returns:
            {"detections": [...], "inference_time_ms": float, ...}
        """
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(image_url)
            response.raise_for_status()

        image = Image.open(io.BytesIO(response.content))
        image_np = np.array(image)

        return await self._run_inference(
            image_np, confidence_threshold, iou_threshold,
            return_annotated, image,
        )

    async def infer_from_frame(
        self,
        frame: np.ndarray,
        confidence_threshold: float = 0.45,
        iou_threshold: float = 0.45,
        return_annotated: bool = False,
    ) -> dict[str, Any]:
        """
        从 numpy 帧数据执行推理 (视频流管道使用)

        Args:
            frame: numpy ndarray (H, W, 3) BGR 或 RGB
            confidence_threshold: 置信度阈值
            iou_threshold: 置信度阈值
            return_annotated: 是否返回标注后图像

        Returns:
            {"detections": [...], "inference_time_ms": float, ...}
        """
        return await self._run_inference(
            frame, confidence_threshold, iou_threshold, return_annotated,
        )

    async def _run_inference(
        self,
        image: np.ndarray,
        confidence_threshold: float,
        iou_threshold: float,
        return_annotated: bool,
        pil_image: Optional[Image.Image] = None,
    ) -> dict[str, Any]:
        """执行核心推理逻辑"""
        manager = get_model_manager()

        if not manager.is_ready:
            raise RuntimeError("推理服务未就绪: 没有活跃的模型")

        start_time = time.perf_counter()

        try:
            detections = manager.predict(
                image,
                conf=confidence_threshold,
                iou=iou_threshold,
            )
        except Exception as exc:
            logger.error("推理执行失败: %s", exc)
            raise RuntimeError(f"推理失败: {exc}")

        elapsed_ms = (time.perf_counter() - start_time) * 1000

        result: dict[str, Any] = {
            "detections": detections,
            "inference_time_ms": round(elapsed_ms, 1),
            "model_version": manager.active_version,
            "image_width": image.shape[1],
            "image_height": image.shape[0],
        }

        # 可选: 生成标注图像
        if return_annotated and detections:
            annotated = self._draw_boxes(image, detections)
            buffered = io.BytesIO()
            Image.fromarray(annotated).save(buffered, format="JPEG", quality=85)
            result["annotated_image_base64"] = base64.b64encode(buffered.getvalue()).decode()

        return result

    def _draw_boxes(
        self,
        image: np.ndarray,
        detections: list[dict[str, Any]],
    ) -> np.ndarray:
        """在图像上绘制检测框 (OpenCV)"""
        import cv2

        img = image.copy()
        if img.dtype != np.uint8:
            img = img.astype(np.uint8)

        # 颜色映射
        color_map = {
            "water_accumulation": (0, 255, 255),   # 黄色
            "missing_manhole": (0, 0, 255),        # 红色
            "damaged_manhole": (0, 165, 255),      # 橙色
            "shifted_manhole": (0, 140, 255),      # 深橙色
            "intruder": (255, 0, 0),               # 蓝色
            "illegal_parking": (255, 0, 255),      # 品红色
            "person": (255, 0, 0),                 # 蓝色
            "car": (0, 255, 0),                    # 绿色
            "truck": (0, 200, 0),                  # 深绿色
        }

        for det in detections:
            bbox = [int(v) for v in det["bbox"]]
            class_name = det["class_name"]
            confidence = det["confidence"]

            color = color_map.get(class_name, (128, 128, 128))

            # 绘制矩形
            cv2.rectangle(img, (bbox[0], bbox[1]), (bbox[2], bbox[3]), color, 2)

            # 绘制标签
            label = f"{class_name} {confidence:.2f}"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(img, (bbox[0], bbox[1] - th - 4), (bbox[0] + tw, bbox[1]), color, -1)
            cv2.putText(img, label, (bbox[0], bbox[1] - 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

        return img


# 单例
inference_service = InferenceService()
