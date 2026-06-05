"""
XHS UI Element Detection - YOLOv8 Training Script
Trains a YOLOv8 nano model to detect XHS app UI elements from mobile screenshots.
"""

import os
import torch
from ultralytics import YOLO

# Detect best available device for Apple Silicon
def get_device():
    if torch.backends.mps.is_available():
        print("✅ Apple M2 MPS (Metal) GPU detected! Using GPU acceleration.")
        return "mps"
    else:
        print("⚠️  MPS not available, falling back to CPU.")
        return "cpu"

# Paths
DATASET_YAML = os.path.abspath(
    os.path.join(os.path.dirname(__file__), 
                 "../../data/xhs_dataset/augmented_yolo/dataset.yaml")
)
PROJECT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "runs")
)

# Training hyperparameters
MODEL = "yolov8n.pt"     # Nano model - fast inference, suitable for mobile UI
DEVICE = get_device()    # Auto-detect MPS / CPU
EPOCHS = 100
IMGSZ = 640              # Standard YOLO input size
BATCH = 16               # Adjust based on available memory
PATIENCE = 20            # Early stopping patience
WORKERS = 4


def train():
    print(f"Dataset config: {DATASET_YAML}")
    print(f"Project dir: {PROJECT_DIR}")
    print(f"Model: {MODEL}")
    print(f"Device: {DEVICE}")
    print(f"Epochs: {EPOCHS}, ImgSize: {IMGSZ}, Batch: {BATCH}")
    print("-" * 60)

    # Load pretrained YOLOv8n
    model = YOLO(MODEL)

    # Train
    results = model.train(
        data=DATASET_YAML,
        device=DEVICE,
        epochs=EPOCHS,
        imgsz=IMGSZ,
        batch=BATCH,
        patience=PATIENCE,
        workers=WORKERS,
        project=PROJECT_DIR,
        name="xhs_ui_detect",
        exist_ok=True,
        # Augmentation settings aligned with our offline augmentation strategy
        # We already did heavy augmentation offline, so keep online augmentation mild
        hsv_h=0.015,        # Mild hue augmentation
        hsv_s=0.3,          # Mild saturation augmentation  
        hsv_v=0.3,          # Mild value augmentation
        degrees=0.0,        # NO rotation (mobile screenshots are always upright)
        translate=0.05,     # Very slight translation (already done offline)
        scale=0.1,          # Slight scale variation
        fliplr=0.0,         # NO horizontal flip (text would be mirrored)
        flipud=0.0,         # NO vertical flip (UI layout is fixed)
        mosaic=0.3,         # Mild mosaic (can help with small objects)
        mixup=0.0,          # No mixup (UI screenshots shouldn't be blended)
        verbose=True,
    )

    print("\n" + "=" * 60)
    print("Training complete!")
    print(f"Best model saved to: {PROJECT_DIR}/xhs_ui_detect/weights/best.pt")
    print(f"Results: {results}")

    return results


if __name__ == "__main__":
    train()
