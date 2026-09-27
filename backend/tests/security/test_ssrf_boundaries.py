"""SSRF regression tests for image and stream URL boundaries."""

from __future__ import annotations

import asyncio
import socket
import uuid

import httpx
import pytest

from app.api import routes
from app.core.security import OutboundURLRejected, validate_outbound_url
from app.models.db_models import RoleEnum
from app.services import stream_service as stream_service_module
from app.workers import inference_worker


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/image.png",
        "https://10.0.0.1/image.png",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/image.png",
        "https://[fe80::1]/image.png",
        "https://user:password@example.com/image.png",
        "file:///etc/passwd",
        "gopher://example.com/",
        "https://example.com:8443/image.png",
    ],
)
def test_outbound_url_rejects_unsafe_targets(url):
    with pytest.raises(OutboundURLRejected):
        validate_outbound_url(url, allowed_schemes={"http", "https"})


def test_outbound_url_rejects_private_dns_answer(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.20", 443))
        ],
    )
    with pytest.raises(OutboundURLRejected):
        validate_outbound_url("https://images.example/image.png")


def test_outbound_url_accepts_public_dns_answer(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    )
    assert (
        validate_outbound_url("https://images.example/image.png")
        == "https://images.example/image.png"
    )


@pytest.mark.asyncio
async def test_image_fetch_connects_to_the_validated_ip(monkeypatch):
    dns_calls = 0
    seen: list[httpx.Request] = []

    def resolve(*args, **kwargs):
        nonlocal dns_calls
        dns_calls += 1
        address = "93.184.216.34" if dns_calls == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"image-bytes")

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )

    content = await routes._fetch_public_image("https://images.example/image.png")

    assert content == b"image-bytes"
    assert dns_calls == 1
    assert str(seen[0].url) == "https://93.184.216.34/image.png"
    assert seen[0].headers["host"] == "images.example"


def test_stream_url_requires_network_scheme():
    with pytest.raises(OutboundURLRejected):
        validate_outbound_url(
            "file:///etc/passwd", allowed_schemes={"rtsp", "rtsps", "rtmp", "rtmps"}
        )


def test_ffmpeg_input_uses_protocol_allowlist_and_cpu_budget(tmp_path, monkeypatch):
    service = stream_service_module.StreamService()
    captured: dict[str, object] = {}

    class _Process:
        returncode = None

        def poll(self):
            return None

    def popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return _Process()

    monkeypatch.setattr(stream_service_module.subprocess, "Popen", popen)
    monkeypatch.setattr(stream_service_module.time, "sleep", lambda _seconds: None)

    service._launch_ffmpeg("rtsp://93.184.216.34/live", tmp_path, "camera-1")

    command = captured["command"]
    assert isinstance(command, list)
    whitelist_index = command.index("-protocol_whitelist")
    assert set(command[whitelist_index + 1].split(",")) == {"rtsp", "tcp", "tls"}
    assert command[command.index("-threads") + 1] == "1"
    assert "-an" in command


@pytest.mark.asyncio
async def test_stream_processes_share_the_validated_connection_target(
    tmp_path, monkeypatch
):
    service = stream_service_module.StreamService()
    captured: dict[str, object] = {}

    def resolve(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 554))]

    def launch(rtsp_url, output_dir, camera_id, host_header=None):
        captured["ffmpeg_url"] = rtsp_url
        captured["host_header"] = host_header
        return object()

    async def add_worker(**kwargs):
        captured["worker_url"] = kwargs["stream_url"]
        captured["worker_host"] = kwargs["host_header"]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(
        stream_service_module.settings,
        "HLS_OUTPUT_DIR",
        str(tmp_path / "hls"),
    )
    monkeypatch.setattr(service, "_launch_ffmpeg", launch)
    monkeypatch.setattr(service.worker_pool, "add_worker", add_worker)

    await service.start_stream("camera-1", "rtsp://camera.example/live")

    assert captured == {
        "ffmpeg_url": "rtsp://93.184.216.34/live",
        "host_header": "camera.example",
        "worker_url": "rtsp://93.184.216.34/live",
        "worker_host": "camera.example",
    }
    await service.stop_stream("camera-1")


@pytest.mark.asyncio
async def test_opencv_capture_uses_protocol_allowlist_and_timeouts(monkeypatch):
    calls: list[tuple[tuple[object, ...], str | None]] = []

    class _Capture:
        def __init__(self):
            self.opened = True

        def isOpened(self):
            return self.opened

        def read(self):
            return False, None

        def release(self):
            self.opened = False

    class _Cv2:
        CAP_FFMPEG = 1
        CAP_PROP_OPEN_TIMEOUT_MSEC = 2
        CAP_PROP_READ_TIMEOUT_MSEC = 3
        CAP_PROP_STREAM_OPEN_TIME_USEC = 4
        CAP_PROP_STREAM_READ_TIME_USEC = 5
        CAP_PROP_BUFFERSIZE = 6
        CAP_PROP_FFMPEG_CAPTURE_OPTIONS = 7

        @staticmethod
        def VideoCapture(*args):
            calls.append(
                (args, inference_worker.os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS"))
            )
            return _Capture()

    monkeypatch.setattr(inference_worker, "cv2", _Cv2)
    worker = inference_worker.InferenceWorker(
        camera_id="camera-1",
        stream_url="rtsp://93.184.216.34/live",
        host_header="camera.example",
    )
    await worker.start()
    await asyncio.sleep(0)
    await worker.stop()

    assert len(calls) == 1
    capture_args, options = calls[0]
    assert capture_args[0] == "rtsp://93.184.216.34/live"
    params = capture_args[2]
    assert options is not None
    assert "protocol_whitelist|rtsp,tcp,tls" in options
    assert "stimeout|10000000" in options
    assert "Host: camera.example" in options
    assert params[params.index(_Cv2.CAP_PROP_BUFFERSIZE) + 1] == 1


@pytest.mark.asyncio
async def test_stream_capacity_is_bounded(tmp_path, monkeypatch):
    service = stream_service_module.StreamService()
    service._streams["existing"] = object()
    monkeypatch.setattr(
        stream_service_module.settings, "MAX_ACTIVE_STREAMS", 1, raising=False
    )

    async def add_worker(**kwargs):
        return None

    monkeypatch.setattr(service, "_launch_ffmpeg", lambda *args: object())
    monkeypatch.setattr(service.worker_pool, "add_worker", add_worker)

    with pytest.raises(stream_service_module.MediaResourceLimitExceeded):
        await service.start_stream("camera-2", "rtsp://93.184.216.34/live")


@pytest.mark.asyncio
async def test_inference_requires_operator_authentication(client):
    response = await client.post(
        "/api/inference/detect", json={"image_url": "https://example.com/image.png"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_inference_rejects_private_url_before_fetch(client, monkeypatch):
    token = routes.create_access_token(
        subject=str(uuid.uuid4()), extra_claims={"role": RoleEnum.operator.value}
    )

    async def should_not_fetch(*args, **kwargs):
        raise AssertionError("unsafe URL reached the inference service")

    monkeypatch.setattr(routes.inference_service, "infer_from_base64", should_not_fetch)
    response = await client.post(
        "/api/inference/detect",
        json={"image_url": "http://127.0.0.1:8000/image.png"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400
