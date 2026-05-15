#!/bin/bash
# 下载 YOLOv8 预训练模型权重

MODEL_DIR=${MODEL_DIR:-./models/weights/v1}
MODEL_NAME=${MODEL_NAME:-yolov8n.pt}
MODEL_URL="https://github.com/ultralytics/assets/releases/download/v8.1.0/${MODEL_NAME}"

mkdir -p "$MODEL_DIR"

if [ -f "$MODEL_DIR/$MODEL_NAME" ]; then
    echo "Model $MODEL_NAME already exists at $MODEL_DIR/"
    exit 0
fi

echo "Downloading $MODEL_NAME to $MODEL_DIR/..."
echo "URL: $MODEL_URL"

if command -v wget &> /dev/null; then
    wget -O "$MODEL_DIR/$MODEL_NAME" "$MODEL_URL"
elif command -v curl &> /dev/null; then
    curl -L -o "$MODEL_DIR/$MODEL_NAME" "$MODEL_URL"
else
    echo "Error: wget or curl is required"
    exit 1
fi

echo "Model downloaded successfully: $MODEL_DIR/$MODEL_NAME"
echo ""
echo "Available models:"
echo "  yolov8n.pt  - Nano (6.3MB)   - Fastest, lowest accuracy"
echo "  yolov8s.pt  - Small (22.5MB) - Good balance"
echo "  yolov8m.pt  - Medium (52.0MB) - Higher accuracy"
echo "  yolov8l.pt  - Large (87.7MB) - High accuracy"
echo "  yolov8x.pt  - XLarge (136.7MB) - Highest accuracy"
echo ""
echo "To download a different model:"
echo "  MODEL_NAME=yolov8s.pt bash scripts/download-model.sh"
