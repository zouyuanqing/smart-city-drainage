"""Flux injection regression tests for InfluxDB query construction."""

from __future__ import annotations

from app.services.influxdb_service import InfluxDBService


class _QueryApi:
    def __init__(self):
        self.calls = []

    def query(self, query, org=None, params=None):
        self.calls.append({"query": query, "org": org, "params": params or {}})
        return []


def _service() -> tuple[InfluxDBService, _QueryApi]:
    service = InfluxDBService()
    query_api = _QueryApi()
    service._query_api = query_api
    service._bucket = "sensor_data"
    service._org = "test-org"
    return service, query_api


def test_latest_device_filter_is_parameterized():
    service, query_api = _service()
    injection = 'bad"] |> range(start: 0)'

    assert service.get_latest_readings([injection]) == []
    call = query_api.calls[0]
    assert injection not in call["query"]
    assert call["params"]["deviceIds"] == [injection]
    assert "params.deviceIds" in call["query"]


def test_historical_device_filter_is_parameterized():
    service, query_api = _service()
    injection = 'bad"] |> yield(name: "escaped")'

    assert service.get_historical_readings(injection) == []
    call = query_api.calls[0]
    assert injection not in call["query"]
    assert call["params"]["deviceId"] == injection
    assert "params.deviceId" in call["query"]


def test_export_query_does_not_interpolate_filters():
    service, _ = _service()
    injection = 'x") |> from(bucket: "other")'

    query, params = service.build_sensor_export_query(
        device_id=injection,
        start_time='-1d") |> from(bucket: "other")',
        end_time="now()",
    )

    assert injection not in query
    assert params["deviceId"] == injection
    assert params["startTime"] == '-1d") |> from(bucket: "other")'
    assert "params.deviceId" in query
    assert "params.startTime" in query


def test_historical_query_rejects_unbounded_numeric_input():
    service, _ = _service()
    assert service.get_historical_readings("device-1", hours=0) == []
    assert service.get_historical_readings("device-1", interval_minutes=61) == []
