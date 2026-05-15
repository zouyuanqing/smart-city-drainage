#!/bin/bash
# Smart City Drainage - 数据恢复脚本

set -e

if [ -z "$1" ]; then
    echo "Usage: ./restore.sh <backup_file.tar.gz>"
    echo ""
    echo "Available backups:"
    ls -la backups/*.tar.gz 2>/dev/null || echo "  No backups found"
    exit 1
fi

BACKUP_FILE=$1
RESTORE_DIR=$(mktemp -d)

echo "=== Smart City Drainage Restore ==="
echo "Backup file: ${BACKUP_FILE}"
echo ""

# Extract
echo "Extracting backup..."
tar -xzf "${BACKUP_FILE}" -C "${RESTORE_DIR}"
BACKUP_SUBDIR=$(ls "${RESTORE_DIR}")
echo "  Extracted to: ${RESTORE_DIR}/${BACKUP_SUBDIR}"

# Restore PostgreSQL
echo "[1/3] Restoring PostgreSQL..."
docker exec -i scn-postgres psql -U drainage_admin smart_drainage \
  < "${RESTORE_DIR}/${BACKUP_SUBDIR}/postgres_backup.sql"
echo "  PostgreSQL restored"

# Restore InfluxDB
echo "[2/3] Restoring InfluxDB..."
docker cp "${RESTORE_DIR}/${BACKUP_SUBDIR}/influx_backup" scn-influxdb:/tmp/influx_backup
docker exec scn-influxdb influx restore /tmp/influx_backup \
  --token scn-influx-token-2024-secure \
  --org smart-city 2>/dev/null
docker exec scn-influxdb rm -rf /tmp/influx_backup
echo "  InfluxDB restored"

# Restore Redis
echo "[3/3] Restoring Redis..."
docker cp "${RESTORE_DIR}/${BACKUP_SUBDIR}/redis_backup.rdb" scn-redis:/data/dump.rdb
docker restart scn-redis
echo "  Redis restored (restart required)"

# Cleanup
rm -rf "${RESTORE_DIR}"

echo ""
echo "=== Restore Complete ==="
