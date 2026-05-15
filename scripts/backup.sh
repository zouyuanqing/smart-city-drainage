#!/bin/bash
# Smart City Drainage - 数据备份脚本

set -e

BACKUP_DIR=${BACKUP_DIR:-./backups}
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_PATH="${BACKUP_DIR}/${TIMESTAMP}"

mkdir -p "${BACKUP_PATH}"

echo "=== Smart City Drainage Backup ==="
echo "Backup directory: ${BACKUP_PATH}"
echo ""

# Backup PostgreSQL
echo "[1/3] Backing up PostgreSQL..."
docker exec scn-postgres pg_dump -U drainage_admin smart_drainage \
  > "${BACKUP_PATH}/postgres_backup.sql" 2>/dev/null
echo "  PostgreSQL backup: ${BACKUP_PATH}/postgres_backup.sql"

# Backup InfluxDB
echo "[2/3] Backing up InfluxDB..."
docker exec scn-influxdb influx backup /tmp/influx_backup \
  --token scn-influx-token-2024-secure \
  --org smart-city 2>/dev/null
docker cp scn-influxdb:/tmp/influx_backup "${BACKUP_PATH}/influx_backup"
docker exec scn-influxdb rm -rf /tmp/influx_backup
echo "  InfluxDB backup: ${BACKUP_PATH}/influx_backup/"

# Backup Redis
echo "[3/3] Backing up Redis..."
docker exec scn-redis redis-cli BGSAVE 2>/dev/null
sleep 1
docker cp scn-redis:/data/dump.rdb "${BACKUP_PATH}/redis_backup.rdb"
echo "  Redis backup: ${BACKUP_PATH}/redis_backup.rdb"

# Compress
echo ""
echo "Compressing backup..."
tar -czf "${BACKUP_DIR}/${TIMESTAMP}.tar.gz" -C "${BACKUP_DIR}" "${TIMESTAMP}"
rm -rf "${BACKUP_PATH}"

echo ""
echo "=== Backup Complete ==="
echo "Archive: ${BACKUP_DIR}/${TIMESTAMP}.tar.gz"
echo "Size: $(du -h ${BACKUP_DIR}/${TIMESTAMP}.tar.gz | cut -f1)"
