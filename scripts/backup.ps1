# Smart City Drainage - 数据备份脚本 (Windows)

$BackupDir = if ($env:BACKUP_DIR) { $env:BACKUP_DIR } else { ".\backups" }
$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$BackupPath = Join-Path $BackupDir $Timestamp

New-Item -ItemType Directory -Force -Path $BackupPath | Out-Null

Write-Host "=== Smart City Drainage Backup ==="
Write-Host "Backup directory: $BackupPath"
Write-Host ""

Write-Host "[1/3] Backing up PostgreSQL..."
$pgEnv = "C:\Program Files\Docker\Docker\resources\bin;" + $env:PATH
$process = Start-Process -FilePath "docker" -ArgumentList "exec","scn-postgres","pg_dump","-U","drainage_admin","smart_drainage" -RedirectStandardOutput "$BackupPath\postgres_backup.sql" -NoNewWindow -Wait -PassThru
Write-Host "  PostgreSQL backup: $BackupPath\postgres_backup.sql"

Write-Host "[2/3] Backing up InfluxDB..."
docker exec scn-influxdb influx backup /tmp/influx_backup --token scn-influx-token-2024-secure --org smart-city 2>$null
docker cp "scn-influxdb:/tmp/influx_backup" "$BackupPath\influx_backup"
docker exec scn-influxdb rm -rf /tmp/influx_backup
Write-Host "  InfluxDB backup: $BackupPath\influx_backup\"

Write-Host "[3/3] Backing up Redis..."
docker exec scn-redis redis-cli BGSAVE 2>$null
Start-Sleep -Seconds 1
docker cp "scn-redis:/data/dump.rdb" "$BackupPath\redis_backup.rdb"
Write-Host "  Redis backup: $BackupPath\redis_backup.rdb"

Write-Host ""
Write-Host "=== Backup Complete ==="
