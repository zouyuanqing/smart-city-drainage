-- ============================================================
-- Smart City Neural Endpoints — Database Initialization
-- ============================================================

-- Enable TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Enum types
DO $$ BEGIN
    CREATE TYPE device_status AS ENUM ('online', 'offline', 'fault', 'maintenance');
EXCEPTION WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE alert_level AS ENUM ('critical', 'warning', 'info');
EXCEPTION WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE alert_type AS ENUM (
        'water_accumulation', 'manhole_anomaly', 'intrusion',
        'illegal_parking', 'water_level_high', 'flow_anomaly',
        'device_offline', 'system_error'
    );
EXCEPTION WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE stream_protocol AS ENUM ('rtsp', 'hls', 'webrtc', 'local');
EXCEPTION WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE model_status AS ENUM ('loading', 'active', 'unloading', 'error');
EXCEPTION WHEN duplicate_object THEN null;
END $$;

-- Users table
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    username VARCHAR(64) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    full_name VARCHAR(128),
    role VARCHAR(32) DEFAULT 'operator',
    is_active BOOLEAN DEFAULT true,
    last_login TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Devices table
CREATE TABLE IF NOT EXISTS devices (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    device_code VARCHAR(64) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    device_type VARCHAR(64) NOT NULL DEFAULT 'manhole_cover',
    status device_status DEFAULT 'offline',
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    altitude DOUBLE PRECISION DEFAULT 0,
    address TEXT,
    district VARCHAR(128),
    install_date DATE,
    firmware_version VARCHAR(32),
    battery_level DOUBLE PRECISION DEFAULT 100.0,
    signal_strength INTEGER DEFAULT 100,
    metadata JSONB DEFAULT '{}',
    last_heartbeat TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Create spatial index for devices
CREATE INDEX IF NOT EXISTS idx_devices_location ON devices USING gist (
    ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
);

-- Camera streams table
CREATE TABLE IF NOT EXISTS camera_streams (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    device_id UUID REFERENCES devices(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    stream_url TEXT NOT NULL,
    protocol stream_protocol DEFAULT 'rtsp',
    hls_url TEXT,
    webrtc_url TEXT,
    username VARCHAR(128),
    password_encrypted VARCHAR(255),
    is_active BOOLEAN DEFAULT true,
    resolution_width INTEGER DEFAULT 1920,
    resolution_height INTEGER DEFAULT 1080,
    fps INTEGER DEFAULT 25,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Sensor data (TimescaleDB hypertable)
CREATE TABLE IF NOT EXISTS sensor_readings (
    time TIMESTAMPTZ NOT NULL,
    device_id UUID NOT NULL,
    water_level_mm DOUBLE PRECISION,
    flow_rate_m3h DOUBLE PRECISION,
    water_quality_ph DOUBLE PRECISION,
    water_quality_turbidity DOUBLE PRECISION,
    temperature_c DOUBLE PRECISION,
    humidity_pct DOUBLE PRECISION,
    battery_voltage DOUBLE PRECISION,
    signal_strength INTEGER,
    extra JSONB DEFAULT '{}'
);

-- Convert to hypertable
SELECT create_hypertable('sensor_readings', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_sensor_device_time ON sensor_readings (device_id, time DESC);

-- Alerts table
CREATE TABLE IF NOT EXISTS alerts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    device_id UUID REFERENCES devices(id) ON DELETE SET NULL,
    alert_type alert_type NOT NULL,
    level alert_level DEFAULT 'info',
    title VARCHAR(255) NOT NULL,
    description TEXT,
    snapshot_url TEXT,
    bbox_coordinates JSONB,
    detection_confidence DOUBLE PRECISION,
    is_acknowledged BOOLEAN DEFAULT false,
    acknowledged_by UUID REFERENCES users(id),
    acknowledged_at TIMESTAMPTZ,
    is_resolved BOOLEAN DEFAULT false,
    resolved_at TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_device ON alerts (device_id, created_at DESC);

-- Model versions table
CREATE TABLE IF NOT EXISTS model_versions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    version_name VARCHAR(32) UNIQUE NOT NULL,
    file_path VARCHAR(512) NOT NULL,
    file_size_bytes BIGINT,
    sha256_hash VARCHAR(64),
    model_type VARCHAR(64) DEFAULT 'yolov8',
    status model_status DEFAULT 'loading',
    metrics JSONB DEFAULT '{}',
    deployed_at TIMESTAMPTZ,
    deployed_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Inference results
CREATE TABLE IF NOT EXISTS inference_results (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    camera_id UUID REFERENCES camera_streams(id) ON DELETE CASCADE,
    model_version VARCHAR(32),
    inference_time_ms DOUBLE PRECISION,
    detections JSONB NOT NULL DEFAULT '[]',
    frame_timestamp TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Create admin user (password: Admin@123456)
INSERT INTO users (username, email, hashed_password, full_name, role) VALUES
    ('admin', 'admin@smartcity.local',
     '$2b$12$LJ3m4ys3GZfnYMz8kVsKaOTSxGhvzRq1ZQ0nOMWhbFPEHsGj5TJv.',
     '系统管理员', 'admin')
ON CONFLICT (username) DO NOTHING;

-- Insert sample devices
INSERT INTO devices (id, device_code, name, device_type, status, latitude, longitude, address, district) VALUES
    ('a0000001-0000-0000-0000-000000000001', 'MH-001', '中山路1号井盖', 'manhole_cover', 'online', 31.2304, 121.4737, '中山路100号', '黄浦区'),
    ('a0000001-0000-0000-0000-000000000002', 'MH-002', '南京路2号井盖', 'manhole_cover', 'online', 31.2350, 121.4750, '南京路200号', '黄浦区'),
    ('a0000001-0000-0000-0000-000000000003', 'MH-003', '陆家嘴3号井盖', 'manhole_cover', 'online', 31.2400, 121.5000, '陆家嘴环路300号', '浦东新区'),
    ('a0000001-0000-0000-0000-000000000004', 'MH-004', '徐家汇4号井盖', 'manhole_cover', 'fault', 31.1950, 121.4370, '虹桥路400号', '徐汇区'),
    ('a0000001-0000-0000-0000-000000000005', 'MH-005', '五角场5号井盖', 'manhole_cover', 'online', 31.3000, 121.5150, '淞沪路500号', '杨浦区'),
    ('a0000001-0000-0000-0000-000000000006', 'MH-006', '静安寺6号井盖', 'manhole_cover', 'online', 31.2250, 121.4480, '南京西路600号', '静安区'),
    ('a0000001-0000-0000-0000-000000000007', 'MH-007', '虹桥7号井盖', 'manhole_cover', 'online', 31.2050, 121.4000, '虹桥路700号', '长宁区'),
    ('a0000001-0000-0000-0000-000000000008', 'MH-008', '张江8号井盖', 'manhole_cover', 'online', 31.2100, 121.5900, '张江路800号', '浦东新区'),
    ('a0000001-0000-0000-0000-000000000009', 'CAM-001', '中山路摄像头A', 'camera', 'online', 31.2305, 121.4738, '中山路100号', '黄浦区'),
    ('a0000001-0000-0000-0000-000000000010', 'CAM-002', '陆家嘴摄像头B', 'camera', 'online', 31.2401, 121.5001, '陆家嘴环路300号', '浦东新区')
ON CONFLICT (device_code) DO NOTHING;
