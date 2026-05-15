# 下载 YOLOv8 预训练模型权重 (Windows)

$ModelDir = if ($env:MODEL_DIR) { $env:MODEL_DIR } else { ".\models\weights\v1" }
$ModelName = if ($env:MODEL_NAME) { $env:MODEL_NAME } else { "yolov8n.pt" }
$ModelUrl = "https://github.com/ultralytics/assets/releases/download/v8.1.0/$ModelName"

New-Item -ItemType Directory -Force -Path $ModelDir | Out-Null

$TargetPath = Join-Path $ModelDir $ModelName

if (Test-Path $TargetPath) {
    Write-Host "Model $ModelName already exists at $ModelDir\"
    exit 0
}

Write-Host "Downloading $ModelName to $ModelDir\..."
Write-Host "URL: $ModelUrl"

Invoke-WebRequest -Uri $ModelUrl -OutFile $TargetPath -UseBasicParsing

Write-Host "Model downloaded successfully: $TargetPath"
