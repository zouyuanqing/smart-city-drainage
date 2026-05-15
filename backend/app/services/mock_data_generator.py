"""
模拟数据生成器 (Mock Data Generator)
-------------------------------------
用于在无硬件环境下演示系统效果。
生成逼真的传感器数据、告警事件和视频流状态。
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.sse_manager import SSEManager

logger = logging.getLogger(__name__)


# 预设设备
PRESET_DEVICES = [
    {"id": "a0000001-0000-0000-0000-000000000001", "code": "MH-001", "name": "中山路1号井盖", "lat": 31.2304, "lng": 121.4737, "district": "黄浦区"},
    {"id": "a0000001-0000-0000-0000-000000000002", "code": "MH-002", "name": "南京路2号井盖", "lat": 31.2350, "lng": 121.4750, "district": "黄浦区"},
    {"id": "a0000001-0000-0000-0000-000000000003", "code": "MH-003", "name": "陆家嘴3号井盖", "lat": 31.2400, "lng": 121.5000, "district": "浦东新区"},
    {"id": "a0000001-0000-0000-0000-000000000004", "code": "MH-004", "name": "徐家汇4号井盖", "lat": 31.1950, "lng": 121.4370, "district": "徐汇区"},
    {"id": "a0000001-0000-0000-0000-000000000005", "code": "MH-005", "name": "五角场5号井盖", "lat": 31.3000, "lng": 121.5150, "district": "杨浦区"},
    {"id": "a0000001-0000-0000-0000-000000000006", "code": "MH-006", "name": "静安寺6号井盖", "lat": 31.2250, "lng": 121.4480, "district": "静安区"},
    {"id": "a0000001-0000-0000-0000-000000000007", "code": "MH-007", "name": "虹桥7号井盖", "lat": 31.2050, "lng": 121.4000, "district": "长宁区"},
    {"id": "a0000001-0000-0000-0000-000000000008", "code": "MH-008", "name": "张江8号井盖", "lat": 31.2100, "lng": 121.5900, "district": "浦东新区"},
]

ALERT_TITLES = [
    ("water_accumulation", "critical", "积水深度超阈值"),
    ("water_accumulation", "warning", "路面轻微积水"),
    ("manhole_anomaly", "critical", "井盖缺失告警"),
    ("manhole_anomaly", "warning", "井盖移位检测"),
    ("manhole_anomaly", "info", "井盖倾斜预警"),
    ("intrusion", "critical", "非法闯入施工区域"),
    ("intrusion", "warning", "可疑人员徘徊"),
    ("illegal_parking", "warning", "车辆违停消防通道"),
    ("water_level_high", "critical", "液位超警戒线"),
    ("water_level_high", "warning", "液位上升过快"),
    ("device_offline", "info", "设备离线超时"),
    ("flow_anomaly", "warning", "流量异常波动"),
]


class MockDataGenerator:
    """模拟数据生成器 — 可配置告警频率和数量"""

    def __init__(self, sensor_interval: float = 4.0) -> None:
        self.sensor_interval = sensor_interval   # 传感器数据广播间隔
        self.alert_interval_seconds: float = 60   # 告警生成间隔 (秒)
        self.alert_probability: float = 0.6        # 每次触发时生成告警的概率
        self.alert_count_per_batch: int = 1        # 每次生成的告警数量
        self._running = False
        self._task: asyncio.Task | None = None
        self._sse = SSEManager.get_instance()
        self._last_alert_at: float = 0

    @property
    def is_running(self) -> bool:
        return self._running

    def get_config(self) -> dict[str, Any]:
        return {
            "sensor_interval": self.sensor_interval,
            "alert_interval_seconds": self.alert_interval_seconds,
            "alert_probability": self.alert_probability,
            "alert_count_per_batch": self.alert_count_per_batch,
        }

    async def start(self) -> None:
        """开始生成模拟数据"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._generate_loop())
        logger.info("🎭 模拟数据生成器已启动 (间隔: %.1fs)", self.sensor_interval)

    async def stop(self) -> None:
        """停止生成"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("🎭 模拟数据生成器已停止")

    async def _generate_loop(self) -> None:
        """主生成循环"""
        self._last_alert_at = loop_count = 0

        while self._running:
            try:
                # 1. 生成传感器数据
                readings = []
                now = datetime.now(timezone.utc)

                for device in PRESET_DEVICES:
                    reading = self._generate_sensor_reading(device, now)
                    readings.append(reading)

                try:
                    from app.services.influxdb_service import influxdb_service
                    if influxdb_service.is_connected:
                        influxdb_service.write_sensor_batch(readings)
                except Exception:
                    pass

                await self._sse.broadcast_sensor(readings)
                try:
                    from app.core.redis_client import redis_client
                    if redis_client.is_connected:
                        await redis_client.publish_sensor_data(readings)
                except Exception:
                    pass

                # 2. 按时间间隔生成告警
                elapsed = loop_count * self.sensor_interval
                if self.alert_interval_seconds > 0 and elapsed >= self.alert_interval_seconds:
                    if random.random() < self.alert_probability:
                        for _ in range(self.alert_count_per_batch):
                            alert = self._generate_alert()
                            await self._sse.broadcast_alert(alert)
                            try:
                                from app.core.redis_client import redis_client
                                if redis_client.is_connected:
                                    await redis_client.publish_alert(alert)
                            except Exception:
                                pass
                    loop_count = 0
                    self._last_alert_at = elapsed

                loop_count += 1
                await asyncio.sleep(self.sensor_interval)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("模拟数据生成异常: %s", exc)
                await asyncio.sleep(5)

    def _generate_sensor_reading(self, device: dict, timestamp: datetime) -> dict[str, Any]:
        """生成单个传感器读数"""
        # 模拟日常波动 + 随机峰值
        base_water = random.gauss(120, 30)  # 基准液位 120mm
        spike = random.random() < 0.1  # 10% 概率出现峰值
        water_level = max(0, base_water + (random.uniform(50, 150) if spike else 0))

        return {
            "device_id": device["id"],
            "device_code": device["code"],
            "device_name": device["name"],
            "water_level_mm": round(water_level, 1),
            "flow_rate_m3h": round(random.gauss(45, 10), 1),
            "water_quality_ph": round(random.gauss(7.2, 0.5), 1),
            "temperature_c": round(random.gauss(22, 3), 1),
            "humidity_pct": round(random.gauss(65, 10), 0),
            "battery_level": round(random.uniform(60, 100), 1),
            "signal_strength": random.randint(60, 100),
            "timestamp": timestamp.isoformat(),
        }

    def _generate_alert(self) -> dict[str, Any]:
        """生成模拟告警"""
        alert_type, level, title = random.choice(ALERT_TITLES)
        device = random.choice(PRESET_DEVICES)

        return {
            "alert_id": str(uuid.uuid4()),
            "alert_type": alert_type,
            "level": level,
            "title": f"[{device['name']}] {title}",
            "description": f"系统自动检测到 {title}，位置: {device['district']} {device['name']}",
            "device_id": device["id"],
            "device_name": device["name"],
            "latitude": device["lat"] + random.uniform(-0.0005, 0.0005),
            "longitude": device["lng"] + random.uniform(-0.0005, 0.0005),
            "snapshot_url": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def generate_historical_sensor_data(
        device_id: str,
        hours: int = 24,
        interval_minutes: int = 5,
    ) -> list[dict[str, Any]]:
        """生成历史传感器数据 (用于图表展示)"""
        data = []
        now = datetime.now(timezone.utc)

        for i in range((hours * 60) // interval_minutes):
            ts = now - timedelta(minutes=i * interval_minutes)
            data.append({
                "time": ts.isoformat(),
                "water_level_mm": round(random.gauss(120 + 20 * (1 + math.sin(i * 0.1)), 15), 1),
                "flow_rate_m3h": round(random.gauss(45, 8), 1),
            })

        return list(reversed(data))


# 单例
mock_generator = MockDataGenerator(sensor_interval=4.0)
