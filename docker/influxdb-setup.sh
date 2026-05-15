#!/bin/bash
# InfluxDB 初始化脚本 - 配置 downsampling 任务

INFLUX_URL=${INFLUX_URL:-http://localhost:8086}
INFLUX_TOKEN=${INFLUX_TOKEN:-scn-influx-token-2024-secure}
INFLUX_ORG=${INFLUX_ORG:-smart-city}
INFLUX_BUCKET=${INFLUX_BUCKET:-sensor_data}

echo "Configuring InfluxDB downsampling tasks..."

# 创建 1 小时聚合数据的 bucket
influx bucket create \
  --name "sensor_data_1h" \
  --org "$INFLUX_ORG" \
  --token "$INFLUX_TOKEN" \
  --retention 365d \
  --host "$INFLUX_URL" 2>/dev/null || echo "Bucket sensor_data_1h already exists"

# 创建 downsampling 任务：每小时聚合一次原始数据
influx task create \
  --org "$INFLUX_ORG" \
  --token "$INFLUX_TOKEN" \
  --host "$INFLUX_URL" \
  --file /dev/stdin <<'FLUX'
option task = {name: "downsample-sensor-1h", every: 1h}

from(bucket: "sensor_data")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "sensor_readings")
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
  |> to(bucket: "sensor_data_1h", org: "smart-city")
FLUX

echo "InfluxDB setup complete!"
