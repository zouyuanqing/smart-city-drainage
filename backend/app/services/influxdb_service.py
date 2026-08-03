"""InfluxDB 传感器数据服务"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


class InfluxDBService:
    _instance: Optional["InfluxDBService"] = None

    def __init__(self) -> None:
        self._client = None
        self._write_api = None
        self._query_api = None
        self._bucket = settings.INFLUXDB_BUCKET
        self._org = settings.INFLUXDB_ORG

    @classmethod
    def get_instance(cls) -> "InfluxDBService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def connect(self) -> None:
        """初始化 InfluxDB 客户端连接"""
        try:
            from influxdb_client import InfluxDBClient

            self._client = InfluxDBClient(
                url=settings.INFLUXDB_URL,
                token=settings.INFLUXDB_TOKEN,
                org=settings.INFLUXDB_ORG,
            )
            self._write_api = self._client.write_api()
            self._query_api = self._client.query_api()
            ready = self._client.ping()
            if ready:
                logger.info("✅ InfluxDB 已连接: %s", settings.INFLUXDB_URL)
            else:
                logger.warning("⚠️  InfluxDB ping 失败")
        except Exception as exc:
            logger.warning("⚠️  InfluxDB 连接失败: %s", exc)
            self._client = None

    async def disconnect(self) -> None:
        """断开 InfluxDB 连接"""
        if self._write_api:
            self._write_api.close()
        if self._client:
            self._client.close()
            self._client = None

    @property
    def is_connected(self) -> bool:
        return self._client is not None

    async def ping(self) -> bool:
        """检查 InfluxDB 连接"""
        if not self._client:
            return False
        try:
            return self._client.ping()
        except Exception:
            return False

    def write_sensor_reading(self, reading: dict[str, Any]) -> None:
        """写入单条传感器数据到 InfluxDB"""
        if not self._write_api:
            return
        try:
            from influxdb_client import Point

            point = (
                Point("sensor_readings")
                .tag("device_id", reading.get("device_id", ""))
                .tag("device_code", reading.get("device_code", ""))
                .tag("device_name", reading.get("device_name", ""))
                .field("water_level_mm", float(reading.get("water_level_mm", 0)))
                .field("flow_rate_m3h", float(reading.get("flow_rate_m3h", 0)))
                .field("temperature_c", float(reading.get("temperature_c", 0)))
                .field("battery_level", float(reading.get("battery_level", 0)))
                .field("signal_strength", int(reading.get("signal_strength", 0)))
            )
            if reading.get("water_quality_ph") is not None:
                point.field("water_quality_ph", float(reading["water_quality_ph"]))
            if reading.get("humidity_pct") is not None:
                point.field("humidity_pct", float(reading["humidity_pct"]))
            if reading.get("timestamp"):
                ts = reading["timestamp"]
                if isinstance(ts, str):
                    point.time(ts)
            self._write_api.write(bucket=self._bucket, org=self._org, record=point)
        except Exception as exc:
            logger.error("InfluxDB 写入失败: %s", exc)

    def write_sensor_batch(self, readings: list[dict[str, Any]]) -> None:
        """批量写入传感器数据"""
        if not self._write_api:
            return
        try:
            from influxdb_client import Point

            points = []
            for reading in readings:
                point = (
                    Point("sensor_readings")
                    .tag("device_id", reading.get("device_id", ""))
                    .tag("device_code", reading.get("device_code", ""))
                    .field("water_level_mm", float(reading.get("water_level_mm", 0)))
                    .field("flow_rate_m3h", float(reading.get("flow_rate_m3h", 0)))
                    .field("temperature_c", float(reading.get("temperature_c", 0)))
                    .field("battery_level", float(reading.get("battery_level", 0)))
                    .field("signal_strength", int(reading.get("signal_strength", 0)))
                )
                if reading.get("timestamp"):
                    point.time(reading["timestamp"])
                points.append(point)
            self._write_api.write(bucket=self._bucket, org=self._org, record=points)
        except Exception as exc:
            logger.error("InfluxDB 批量写入失败: %s", exc)

    def get_latest_readings(
        self, device_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """查询每个设备最新一条传感器读数"""
        if not self._query_api:
            return []
        try:
            device_filter = ""
            if device_ids:
                ids = " OR ".join([f'r["device_id"] == "{did}"' for did in device_ids])
                device_filter = f"|> filter(fn: (r) => {ids})"

            query = f"""
            from(bucket: "{self._bucket}")
              |> range(start: -1h)
              |> filter(fn: (r) => r["_measurement"] == "sensor_readings")
              {device_filter}
              |> last()
              |> pivot(rowKey: ["device_id", "device_code"], columnKey: ["_field"], valueColumn: "_value")
            """
            tables = self._query_api.query(query, org=self._org)
            results = []
            for table in tables:
                for record in table.records:
                    results.append(
                        {
                            "device_id": record.values.get("device_id", ""),
                            "device_code": record.values.get("device_code", ""),
                            "water_level_mm": record.values.get("water_level_mm", 0),
                            "flow_rate_m3h": record.values.get("flow_rate_m3h", 0),
                            "temperature_c": record.values.get("temperature_c", 0),
                            "battery_level": record.values.get("battery_level", 0),
                            "signal_strength": record.values.get("signal_strength", 0),
                            "timestamp": (
                                record.get_time().isoformat()
                                if record.get_time()
                                else ""
                            ),
                        }
                    )
            return results
        except Exception as exc:
            logger.error("InfluxDB 查询最新数据失败: %s", exc)
            return []

    def get_historical_readings(
        self,
        device_id: str,
        hours: int = 24,
        interval_minutes: int = 5,
    ) -> list[dict[str, Any]]:
        """按时间范围和间隔聚合查询历史传感器数据"""
        if not self._query_api:
            return []
        try:
            query = f"""
            from(bucket: "{self._bucket}")
              |> range(start: -{hours}h)
              |> filter(fn: (r) => r["_measurement"] == "sensor_readings")
              |> filter(fn: (r) => r["device_id"] == "{device_id}")
              |> filter(fn: (r) => r["_field"] == "water_level_mm" or r["_field"] == "flow_rate_m3h")
              |> aggregateWindow(every: {interval_minutes}m, fn: mean, createEmpty: false)
              |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
            """
            tables = self._query_api.query(query, org=self._org)
            results = []
            for table in tables:
                for record in table.records:
                    results.append(
                        {
                            "time": (
                                record.get_time().isoformat()
                                if record.get_time()
                                else ""
                            ),
                            "water_level_mm": round(
                                record.values.get("water_level_mm", 0), 1
                            ),
                            "flow_rate_m3h": round(
                                record.values.get("flow_rate_m3h", 0), 1
                            ),
                        }
                    )
            return results
        except Exception as exc:
            logger.error("InfluxDB 查询历史数据失败: %s", exc)
            return []


influxdb_service = InfluxDBService.get_instance()
