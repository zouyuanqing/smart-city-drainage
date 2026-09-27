"""
核心配置模块
--------------
使用 pydantic-settings 管理所有环境变量和配置项。
支持 .env 文件加载，提供类型安全和验证。

要点：
- dotenv 查找锚定到项目根目录（``SCN_ENV_FILE`` 可覆盖），与进程工作目录无关；
- 资源/重试上限按冻结契约强制校验，越界值直接拒绝启动；
- JWT 只接受 ``JWT_ALGORITHMS`` allowlist 内的算法，签发与验证共用该来源；
- EPA SWMM 是可选外部可执行程序，availability 由实际 probe 决定。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

# backend/app/core/config.py -> backend/app/core -> backend/app -> backend
BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT_DIR = BACKEND_DIR.parent
PROJECT_ROOT_ENV_FILE = PROJECT_ROOT_DIR / ".env"
BACKEND_ENV_FILE = BACKEND_DIR / ".env"
ENV_FILE_OVERRIDE_VARIABLE = "SCN_ENV_FILE"

# Algorithms this service is allowed to sign and/or verify. `none`, unknown and
# asymmetric algorithms that need key material we do not model are not accepted.
SUPPORTED_JWT_ALGORITHMS = frozenset(
    {"HS256", "HS384", "HS512", "RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}
)

# Frozen contract ceilings (PRD_SUPPLEMENT.md D-07/D-08/D-09). Values may be
# tuned downwards only; larger values MUST be rejected by Settings.
SIM_ATTEMPT_TIMEOUT_MAX_SECONDS = 600
SIM_CANCEL_POLL_MAX_SECONDS = 1
SIM_MAX_ATTEMPTS_LIMIT = 3
SIM_GLOBAL_CONCURRENCY_CONTRACT = 2
NETWORK_JOB_MAX_ATTEMPTS_LIMIT = 3
NETWORK_JOB_ATTEMPT_TIMEOUT_MAX_SECONDS = 600
NETWORK_JOB_GLOBAL_CONCURRENCY_CEILING = 2
SIM_INPUT_MAX_BYTES_CEILING = 20 * 1024 * 1024
SIM_WORKSPACE_MAX_BYTES_CEILING = 2 * 1024**3
SIMULATION_FRAME_MAX_BYTES_CEILING = 64 * 1024**2
SIMULATION_OUTPUT_MAX_BYTES_CEILING = 8 * 1024**3

_EPA_SWMM_VERSION_PATTERN = re.compile(r"^\d+(\.\d+){1,3}([.-]?(alpha|beta|rc)\d*)?$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
HYDRAULIC_SCHEMA_HEAD = "0008_hydraulic_retention_seed"


def resolve_env_file() -> Optional[Path]:
    """Resolve the dotenv file without depending on the process working directory.

    ``.env.example`` documents a project-root ``.env``; anchoring the lookup to
    the repository layout keeps ``cd backend && uvicorn app.main:app`` working.
    ``SCN_ENV_FILE`` overrides the search for operators that mount config
    elsewhere.
    """
    override = os.environ.get(ENV_FILE_OVERRIDE_VARIABLE)
    if override:
        candidate = Path(override).expanduser()
        # An operator-provided relative path is part of the project
        # configuration contract, not a process-cwd lookup.
        return (
            candidate
            if candidate.is_absolute()
            else (PROJECT_ROOT_DIR / candidate).resolve()
        )
    if PROJECT_ROOT_ENV_FILE.is_file():
        return PROJECT_ROOT_ENV_FILE
    if BACKEND_ENV_FILE.is_file():
        return BACKEND_ENV_FILE
    return None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


_COMPOSE_ONLY_ENV_KEYS = frozenset(
    {
        "DEBIAN_MIRROR",
        "GRAFANA_ADMIN_PASSWORD",
        "INFLUXDB_PASSWORD",
        "NODE_IMAGE",
        "NPM_REGISTRY",
        "PIP_INDEX_URL",
        "POSTGRES_PASSWORD",
        "REDIS_PASSWORD",
    }
)


class _FilteredDotEnvSource(PydanticBaseSettingsSource):
    """Keep Compose-only secrets out of application Settings validation."""

    def __init__(self, source: PydanticBaseSettingsSource) -> None:
        super().__init__(source.settings_cls)
        self._source = source

    def __call__(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in self._source().items()
            if key.upper() not in _COMPOSE_ONLY_ENV_KEYS
        }

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return self._source.get_field_value(field, field_name)


class Settings(BaseSettings):
    """应用全局配置单例"""

    model_config = SettingsConfigDict(
        env_file=None,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="forbid",
    )

    def __init__(self, **values: Any) -> None:
        # Resolved per instantiation so tests and long-lived processes can move
        # the dotenv file; an explicit ``_env_file`` (including ``None``) wins.
        values.setdefault("_env_file", resolve_env_file())
        super().__init__(**values)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            _FilteredDotEnvSource(dotenv_settings),
            file_secret_settings,
        )

    # ============ 应用基础 ============
    APP_NAME: str = "Smart City Neural Endpoints"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = Field(default=False, description="调试模式")
    LOG_LEVEL: str = Field(default="INFO", description="日志级别")

    # ============ 数据库 ============
    DATABASE_URL: str = Field(
        default=(
            "postgresql+asyncpg://drainage_admin:"
            "Dr@inage_S3cur3_2024!@localhost:5432/smart_drainage"
        ),
        description="异步数据库连接串 (asyncpg)",
    )
    DATABASE_URL_SYNC: str = Field(
        default=(
            "postgresql://drainage_admin:"
            "Dr@inage_S3cur3_2024!@localhost:5432/smart_drainage"
        ),
        description="同步数据库连接串 (psycopg2)",
    )
    DB_POOL_SIZE: int = Field(default=20, description="数据库连接池大小")
    DB_MAX_OVERFLOW: int = Field(default=10, description="最大溢出连接数")

    # ============ InfluxDB ============
    INFLUXDB_URL: str = Field(default="http://localhost:8086")
    INFLUXDB_TOKEN: str = Field(default="scn-influx-token-2024-secure")
    INFLUXDB_ORG: str = Field(default="smart-city")
    INFLUXDB_BUCKET: str = Field(default="sensor_data")

    # ============ Redis ============
    REDIS_URL: str = Field(default="redis://:R3d1s_S3cur3_2024!@localhost:6379/0")
    REDIS_CHANNEL_ALERTS: str = "scn:alerts"
    REDIS_CHANNEL_SENSOR: str = "scn:sensor_data"
    REDIS_CHANNEL_MODEL: str = "scn:model_status"

    # ============ JWT 认证 ============
    JWT_SECRET_KEY: str = Field(
        default="scn-jwt-secret-key-change-in-production-2024", repr=False
    )
    # Asymmetric key material is kept separate from the symmetric secret so an
    # RS256 deployment can never accidentally pass a short HMAC secret to
    # ``python-jose``. Values are PEM strings supplied by a secret manager or
    # mounted as environment variables; they are never written to the template.
    JWT_PRIVATE_KEY: str = Field(default="", repr=False)
    JWT_PUBLIC_KEY: str = Field(default="", repr=False)
    # Legacy single-value signer. It is only honoured when it is contained in
    # JWT_ALGORITHMS; both signing and verification read the same allowlist.
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(
        default=1440, description="Token 过期时间 (分钟), 默认24小时"
    )

    # ============ YOLO 模型 ============
    MODEL_STORAGE_PATH: str = Field(default="./models/weights")
    DEFAULT_MODEL_VERSION: str = Field(default="v1", description="默认加载的模型版本")
    MODEL_INFERENCE_DEVICE: str = Field(
        default="cuda:0", description="推理设备: 'cuda:0' 或 'cpu'"
    )
    MODEL_CONFIDENCE_THRESHOLD: float = Field(
        default=0.45, description="检测置信度阈值"
    )
    MODEL_IOU_THRESHOLD: float = Field(default=0.45, description="NMS IoU 阈值")

    # ============ 视频流 ============
    FFMPEG_PATH: str = Field(default="ffmpeg", description="FFmpeg 可执行文件路径")
    FFPROBE_PATH: str = Field(default="ffprobe", description="FFprobe 可执行文件路径")
    HLS_OUTPUT_DIR: str = Field(default="./backend/media/hls")
    SCREENSHOT_DIR: str = Field(default="./backend/media/screenshots")
    HLS_SEGMENT_TIME: int = Field(default=4, description="HLS 分片时长 (秒)")
    HLS_LIST_SIZE: int = Field(default=6, description="HLS 播放列表保留分片数")
    MAX_ACTIVE_STREAMS: int = Field(default=4, ge=1, le=32)
    MAX_ACTIVE_INFERENCE_WORKERS: int = Field(default=4, ge=1, le=32)
    MEDIA_CONNECT_TIMEOUT_SECONDS: int = Field(default=5, ge=1, le=30)
    MEDIA_READ_TIMEOUT_SECONDS: int = Field(default=10, ge=1, le=60)
    STREAM_HEARTBEAT_INTERVAL: int = Field(
        default=30, description="流健康检查间隔 (秒)"
    )

    # ============ CORS ============
    CORS_ORIGINS: list[str] = Field(
        default=["http://localhost:5173", "http://localhost:3000"],
        description="允许的跨域来源 (JSON 数组)",
    )

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: str | list) -> list[str]:
        """解析 CORS 来源列表"""
        if isinstance(v, list):
            return v
        try:
            parsed = json.loads(v)
            if isinstance(parsed, list) and all(
                isinstance(origin, str) for origin in parsed
            ):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
        return [origin.strip() for origin in v.split(",") if origin.strip()]

    # ============ 水力仿真与任务 ============
    HYDRAULIC_SPATIAL_BACKEND: Literal["postgis", "portable"] = "portable"
    # 0003 closed the event_outbox / worker_slots / notification_deliveries gap
    # and 0004-0008 close the remaining /readyz table gap: network imports and
    # validation, the observation trust/ingest/sample hypertable, the forecast
    # alert window, the A/B comparison record, and the observation retention
    # seed.  A single approved head keeps the readiness comparison against
    # alembic_version and schema_metadata fail-closed.
    HYDRAULIC_SCHEMA_REVISION: Literal["0008_hydraulic_retention_seed"] = (
        "0008_hydraulic_retention_seed"
    )
    HYDRAULIC_IMPORT_ROOT: str = "./backend/hydraulic/import"
    HYDRAULIC_SANDBOX_ROOT: str = "./backend/hydraulic/sandbox"
    HYDRAULIC_EXPORT_ROOT: str = "./backend/hydraulic/export"
    HYDRAULIC_ARTIFACT_ROOT: str = "./backend/hydraulic/artifacts"

    # D-09 fixes the global solve slots at 2; ge/le (not Literal) keeps the
    # string form used by .env files coercible.
    SIM_GLOBAL_CONCURRENCY: Literal[2] = SIM_GLOBAL_CONCURRENCY_CONTRACT
    SIM_NOWCAST_RESERVED_SLOTS: Literal[1] = 1
    SIM_ATTEMPT_TIMEOUT_SECONDS: int = Field(
        default=600, gt=0, le=SIM_ATTEMPT_TIMEOUT_MAX_SECONDS
    )
    SIM_MAX_ATTEMPTS: int = Field(default=3, ge=2, le=SIM_MAX_ATTEMPTS_LIMIT)
    SIM_RETRY_BACKOFF_SECONDS: str | list[int] = [10, 60]
    SIM_LEASE_SECONDS: int = Field(default=30, gt=0)
    SIM_HEARTBEAT_SECONDS: int = Field(default=10, gt=0)
    SIM_CANCEL_POLL_SECONDS: int = Field(
        default=1, gt=0, le=SIM_CANCEL_POLL_MAX_SECONDS
    )
    SIM_INPUT_MAX_BYTES: int = Field(
        default=SIM_INPUT_MAX_BYTES_CEILING, gt=0, le=SIM_INPUT_MAX_BYTES_CEILING
    )
    SIM_WORKSPACE_MAX_BYTES: int = Field(
        default=SIM_WORKSPACE_MAX_BYTES_CEILING,
        gt=0,
        le=SIM_WORKSPACE_MAX_BYTES_CEILING,
    )
    SIMULATION_FRAME_MAX_BYTES: int = Field(
        default=SIMULATION_FRAME_MAX_BYTES_CEILING,
        gt=0,
        le=SIMULATION_FRAME_MAX_BYTES_CEILING,
    )
    SIMULATION_OUTPUT_MAX_BYTES: int = Field(
        default=SIMULATION_OUTPUT_MAX_BYTES_CEILING,
        gt=0,
        le=SIMULATION_OUTPUT_MAX_BYTES_CEILING,
    )
    SIMULATION_RESULT_RETENTION_DAYS: int = Field(default=90, gt=0)

    # ============ 求解器 ============
    SOLVER_DEFAULT_SELECTION: Literal["auto", "explicit"] = "auto"
    EPA_SWMM_EXECUTABLE: str = ""
    EPA_SWMM_VERSION: str = ""
    EPA_SWMM_SHA256: str = ""
    MAX_SUBSTEPS_PER_REPORT: int = Field(default=20, gt=0)
    COURANT_LIMIT: float = Field(default=0.5, gt=0, le=1)

    @field_validator(
        "SIM_GLOBAL_CONCURRENCY",
        "SIM_NOWCAST_RESERVED_SLOTS",
        "NOWCAST_STEP_MINUTES",
        mode="before",
    )
    @classmethod
    def parse_fixed_integer(cls, value: Any) -> Any:
        """Parse integer-valued env strings before Literal validation."""
        if isinstance(value, str):
            return int(value.strip())
        return value

    @field_validator("JWT_ALGORITHMS", mode="before")
    @classmethod
    def parse_jwt_algorithms(cls, value: Any) -> Any:
        """Accept a native list, a JSON array or a comma-separated string."""
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        "JWT_ALGORITHMS must be a JSON array or comma separated"
                    ) from exc
                if not isinstance(parsed, list):
                    raise ValueError("JWT_ALGORITHMS must be a JSON array")
                return [str(item).strip() for item in parsed if str(item).strip()]
            return [item.strip() for item in text.split(",") if item.strip()]
        return value

    @field_validator("JWT_ALGORITHMS")
    @classmethod
    def validate_jwt_algorithms(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("JWT_ALGORITHMS must not be empty")
        unsupported = [alg for alg in value if alg not in SUPPORTED_JWT_ALGORITHMS]
        if unsupported:
            raise ValueError(
                "JWT_ALGORITHMS contains unsupported algorithms: "
                f"{sorted(unsupported)}; allowed: {sorted(SUPPORTED_JWT_ALGORITHMS)}"
            )
        if len(set(value)) != len(value):
            raise ValueError("JWT_ALGORITHMS must not contain duplicates")
        return value

    @field_validator("EPA_SWMM_VERSION")
    @classmethod
    def validate_epa_swmm_version(cls, value: str) -> str:
        if value and _EPA_SWMM_VERSION_PATTERN.fullmatch(value) is None:
            raise ValueError("EPA_SWMM_VERSION must be a dotted version such as 5.2.4")
        return value

    @field_validator(
        "SIM_RETRY_BACKOFF_SECONDS",
        "NOWCAST_RETRY_BACKOFF_SECONDS",
        "NETWORK_JOB_RETRY_BACKOFF_SECONDS",
        mode="before",
    )
    @classmethod
    def parse_retry_backoff(cls, value: str | list[int]) -> list[int]:
        """Accept native JSON arrays and the documented comma-separated form."""
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            return [int(part.strip()) for part in value.split(",") if part.strip()]
        raise TypeError("retry backoff must be an integer list")

    @field_validator(
        "SIM_RETRY_BACKOFF_SECONDS",
        "NOWCAST_RETRY_BACKOFF_SECONDS",
        "NETWORK_JOB_RETRY_BACKOFF_SECONDS",
    )
    @classmethod
    def validate_retry_backoff(cls, value: list[int]) -> list[int]:
        if any(seconds <= 0 for seconds in value):
            raise ValueError("retry backoff values must be positive integers")
        if any(current >= following for current, following in zip(value, value[1:])):
            raise ValueError("retry backoff values must be strictly increasing")
        return value

    @field_validator("EPA_SWMM_SHA256")
    @classmethod
    def validate_epa_swmm_sha256(cls, value: str) -> str:
        if value and _SHA256_PATTERN.fullmatch(value.lower()) is None:
            raise ValueError(
                "EPA_SWMM_SHA256 must be 64 lowercase hexadecimal characters"
            )
        return value.lower()

    # ============ Nowcast 与预报 ============
    NOWCAST_INTERVAL_MINUTES: int = Field(default=10, gt=0)
    NOWCAST_HORIZON_MINUTES: int = Field(default=120, ge=0, le=120)
    NOWCAST_STEP_MINUTES: Literal[5] = 5
    NOWCAST_DEADLINE_SECONDS: int = Field(default=180, gt=0, le=180)
    NOWCAST_MAX_RETRIES: int = Field(default=2, ge=0)
    NOWCAST_RETRY_BACKOFF_SECONDS: str | list[int] = [5, 15]
    NOWCAST_RESERVED_SLOT_BORROWING: bool = False
    FORECAST_EVENT_WINDOW_MINUTES: int = Field(default=30, gt=0)
    FORECAST_FRESH_MAX_AGE_SECONDS: int = Field(default=900, gt=0)
    FORECAST_REPLAY_RETENTION_HOURS: int = Field(default=168, gt=0)
    FORECAST_SSE_QUEUE_SIZE: int = Field(default=256, gt=0)
    FORECAST_POLICY_CONFIG_PATH: str = ""

    # ============ Network jobs ============
    NETWORK_JOB_GLOBAL_CONCURRENCY: int = Field(
        default=2, gt=0, le=NETWORK_JOB_GLOBAL_CONCURRENCY_CEILING
    )
    NETWORK_JOB_ATTEMPT_TIMEOUT_SECONDS: int = Field(
        default=600, gt=0, le=NETWORK_JOB_ATTEMPT_TIMEOUT_MAX_SECONDS
    )
    NETWORK_JOB_LEASE_SECONDS: int = Field(default=30, gt=0)
    NETWORK_JOB_HEARTBEAT_SECONDS: int = Field(default=10, gt=0)
    NETWORK_JOB_MAX_ATTEMPTS: int = Field(
        default=3, ge=2, le=NETWORK_JOB_MAX_ATTEMPTS_LIMIT
    )
    NETWORK_JOB_RETRY_BACKOFF_SECONDS: str | list[int] = [10, 60]
    NETWORK_INPUT_MAX_BYTES: int = Field(
        default=SIM_INPUT_MAX_BYTES_CEILING, gt=0, le=SIM_INPUT_MAX_BYTES_CEILING
    )

    # ============ 生产安全与限流 ============
    DEPLOYMENT_ENVIRONMENT: Literal["development", "test", "staging", "production"] = (
        "development"
    )
    JWT_ALGORITHMS: list[str] = ["HS256", "RS256"]
    JWT_ISSUER: str = ""
    JWT_AUDIENCE: str = ""
    AUTH_FALLBACK_ADMIN_ENABLED: bool = False
    RATE_LIMIT_FORECAST_READ_PER_MINUTE: int = Field(default=120, gt=0)
    RATE_LIMIT_FORECAST_TRANSITION_PER_MINUTE: int = Field(default=30, gt=0)
    RATE_LIMIT_SIMULATION_SUBMIT_PER_MINUTE: int = Field(default=20, gt=0)
    RATE_LIMIT_NETWORK_JOB_SUBMIT_PER_MINUTE: int = Field(default=10, gt=0)
    RATE_LIMIT_SSE_CONNECT_PER_MINUTE: int = Field(default=30, gt=0)
    NOTIFICATION_MAX_ATTEMPTS: int = Field(default=5, gt=0)

    @model_validator(mode="after")
    def validate_simulation_contract(self) -> "Settings":
        if self.SIM_NOWCAST_RESERVED_SLOTS >= self.SIM_GLOBAL_CONCURRENCY:
            raise ValueError(
                "SIM_NOWCAST_RESERVED_SLOTS must be less than SIM_GLOBAL_CONCURRENCY"
            )
        if self.NOWCAST_HORIZON_MINUTES % self.NOWCAST_STEP_MINUTES != 0:
            raise ValueError(
                "NOWCAST_HORIZON_MINUTES must be divisible by NOWCAST_STEP_MINUTES"
            )
        if self.NOWCAST_RESERVED_SLOT_BORROWING:
            raise ValueError("NOWCAST_RESERVED_SLOT_BORROWING must remain false")
        if len(self.SIM_RETRY_BACKOFF_SECONDS) != self.SIM_MAX_ATTEMPTS - 1:
            raise ValueError(
                "SIM_RETRY_BACKOFF_SECONDS length must equal SIM_MAX_ATTEMPTS - 1"
            )
        if len(self.NOWCAST_RETRY_BACKOFF_SECONDS) != self.NOWCAST_MAX_RETRIES:
            raise ValueError(
                "NOWCAST_RETRY_BACKOFF_SECONDS length must equal NOWCAST_MAX_RETRIES"
            )
        if (
            len(self.NETWORK_JOB_RETRY_BACKOFF_SECONDS)
            != self.NETWORK_JOB_MAX_ATTEMPTS - 1
        ):
            raise ValueError(
                "NETWORK_JOB_RETRY_BACKOFF_SECONDS length must equal "
                "NETWORK_JOB_MAX_ATTEMPTS - 1"
            )
        if self.SIM_LEASE_SECONDS <= self.SIM_HEARTBEAT_SECONDS:
            raise ValueError(
                "SIM_HEARTBEAT_SECONDS must be less than SIM_LEASE_SECONDS"
            )
        if self.NETWORK_JOB_LEASE_SECONDS <= self.NETWORK_JOB_HEARTBEAT_SECONDS:
            raise ValueError(
                "NETWORK_JOB_HEARTBEAT_SECONDS must be less than "
                "NETWORK_JOB_LEASE_SECONDS"
            )
        if self.SIMULATION_FRAME_MAX_BYTES > self.SIM_WORKSPACE_MAX_BYTES:
            raise ValueError(
                "SIMULATION_FRAME_MAX_BYTES must not exceed SIM_WORKSPACE_MAX_BYTES"
            )
        if self.SIM_WORKSPACE_MAX_BYTES > self.SIMULATION_OUTPUT_MAX_BYTES:
            raise ValueError(
                "SIM_WORKSPACE_MAX_BYTES must not exceed SIMULATION_OUTPUT_MAX_BYTES"
            )
        if self.JWT_ALGORITHM not in self.JWT_ALGORITHMS:
            raise ValueError(
                f"JWT_ALGORITHM ({self.JWT_ALGORITHM}) must be included in "
                f"JWT_ALGORITHMS ({self.JWT_ALGORITHMS})"
            )

        swmm_values = (
            self.EPA_SWMM_EXECUTABLE,
            self.EPA_SWMM_VERSION,
            self.EPA_SWMM_SHA256,
        )
        if any(swmm_values) and not all(swmm_values):
            raise ValueError(
                "EPA_SWMM_SHA256, EPA_SWMM_VERSION and EPA_SWMM_EXECUTABLE "
                "must be configured together"
            )
        if self.SOLVER_DEFAULT_SELECTION == "explicit" and not self.epa_swmm_available:
            probe = self.probe_epa_swmm()
            raise ValueError(
                "EPA_SWMM_EXECUTABLE, EPA_SWMM_VERSION and EPA_SWMM_SHA256 "
                "are required when SOLVER_DEFAULT_SELECTION=explicit "
                f"(probe: {probe['reason']})"
            )

        if self.AUTH_FALLBACK_ADMIN_ENABLED and self.DEPLOYMENT_ENVIRONMENT not in {
            "development",
            "test",
        }:
            raise ValueError(
                "AUTH_FALLBACK_ADMIN_ENABLED is only allowed in development/test"
            )
        if self.DEPLOYMENT_ENVIRONMENT == "production":
            if not self.JWT_ISSUER or not self.JWT_AUDIENCE:
                raise ValueError(
                    "JWT_ISSUER and JWT_AUDIENCE are required in production"
                )
            if self.JWT_ALGORITHMS != ["RS256"] or self.JWT_ALGORITHM != "RS256":
                raise ValueError(
                    "production JWT_ALGORITHMS and JWT_ALGORITHM must be exactly RS256"
                )
            if not self.JWT_PRIVATE_KEY:
                raise ValueError(
                    "JWT_PRIVATE_KEY is required for production RS256 signing"
                )
            if not self.JWT_PUBLIC_KEY:
                raise ValueError(
                    "JWT_PUBLIC_KEY is required for production RS256 verification"
                )
            weak_values = {
                "INFLUXDB_TOKEN": (
                    "scn-influx-token-2024-secure",
                    "your-influxdb-token",
                ),
                "DATABASE_URL": ("Dr@inage_S3cur3_2024!", "YOUR_PASSWORD"),
                "DATABASE_URL_SYNC": ("Dr@inage_S3cur3_2024!", "YOUR_PASSWORD"),
                "REDIS_URL": ("R3d1s_S3cur3_2024!", "YOUR_PASSWORD"),
            }
            for name, weak_values_for_field in weak_values.items():
                value = str(getattr(self, name))
                if any(weak_value in value for weak_value in weak_values_for_field):
                    raise ValueError(f"{name} must not use a repository default")
        return self

    @property
    def auth_fallback_admin_allowed(self) -> bool:
        """Runtime gate for the fixed demo admin identity.

        The fixed credential is a development/test-only escape hatch.  Keep the
        runtime check independent of validation so a monkey-patched or stale
        Settings object cannot mint an admin token in staging/production.
        """
        return self.AUTH_FALLBACK_ADMIN_ENABLED and self.DEPLOYMENT_ENVIRONMENT in {
            "development",
            "test",
        }

    @property
    def epa_swmm_configured(self) -> bool:
        """Whether the three EPA SWMM settings are present (no I/O)."""
        return bool(
            self.EPA_SWMM_EXECUTABLE and self.EPA_SWMM_VERSION and self.EPA_SWMM_SHA256
        )

    def probe_epa_swmm(self) -> dict[str, Any]:
        """Verify the optional external EPA SWMM engine (path/version/SHA-256).

        Availability is a fact about the machine, not about configuration:
        a missing, non-runnable or tampered executable reports ``available``
        false with a reason so callers can fall back to the pure-Python solver.
        """
        result: dict[str, Any] = {
            "configured": self.epa_swmm_configured,
            "available": False,
            "reason": "not_configured",
            "executable": self.EPA_SWMM_EXECUTABLE,
            "version": self.EPA_SWMM_VERSION,
            "sha256": self.EPA_SWMM_SHA256,
        }
        if self.epa_swmm_configured:
            candidate = Path(os.path.expandvars(self.EPA_SWMM_EXECUTABLE)).expanduser()
            if not candidate.is_absolute():
                candidate = (PROJECT_ROOT_DIR / candidate).resolve()
            result["executable"] = str(candidate)
            if not candidate.is_file():
                result["reason"] = "executable_not_found"
            elif os.name != "nt" and not os.access(candidate, os.X_OK):
                result["reason"] = "executable_not_runnable"
            elif _EPA_SWMM_VERSION_PATTERN.fullmatch(self.EPA_SWMM_VERSION) is None:
                result["reason"] = "invalid_version"
            else:
                try:
                    digest = _file_sha256(candidate)
                except OSError:
                    result["reason"] = "executable_unreadable"
                else:
                    result["observed_sha256"] = digest
                    if digest != self.EPA_SWMM_SHA256:
                        result["reason"] = "sha256_mismatch"
                    else:
                        result["available"] = True
                        result["reason"] = "verified"
        return dict(result)

    @property
    def epa_swmm_available(self) -> bool:
        """Whether a verified EPA SWMM executable is actually usable."""
        return bool(self.probe_epa_swmm()["available"])

    def resolve_auto_solver(self, requested_solver_id: str | None = None) -> str:
        """Freeze the auto-selection policy without relying on SWMM imports."""
        if requested_solver_id in {"swmm_kw", "swmm_dw"}:
            return requested_solver_id if self.epa_swmm_available else "py_mw"
        if requested_solver_id in {"py_mw", "py_dw", "py_ponding"}:
            return requested_solver_id
        if requested_solver_id is not None:
            raise ValueError(f"unsupported solver_id: {requested_solver_id}")
        return "swmm_kw" if self.epa_swmm_available else "py_mw"

    # ============ 安全 ============
    RATE_LIMIT_PER_MINUTE: int = Field(default=60, description="每分钟请求限制")
    MAX_UPLOAD_SIZE_MB: int = Field(default=100, description="最大上传大小 (MB)")

    # ============ SMTP ============
    SMTP_ENABLED: bool = False
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USE_TLS: bool = True
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "noreply@smartcity.local"

    # ============ Webhook ============
    WEBHOOK_ENABLED: bool = False
    ALERT_WEBHOOK_URLS: list[str] = []

    # ============ Alert Notification ============
    ALERT_EMAIL_RECIPIENTS: list[str] = []

    @property
    def model_storage_dir(self) -> Path:
        """模型存储目录 Path 对象"""
        p = Path(self.MODEL_STORAGE_PATH)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def hls_output_dir(self) -> Path:
        """HLS 输出目录 Path 对象"""
        p = Path(self.HLS_OUTPUT_DIR)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def screenshot_dir(self) -> Path:
        """截图输出目录 Path 对象"""
        p = Path(self.SCREENSHOT_DIR)
        p.mkdir(parents=True, exist_ok=True)
        return p


# 全局单例
settings = Settings()
