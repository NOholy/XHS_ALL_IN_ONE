"""
XHS UI Element Detection - YOLOv8 Training Script V2
Improvements:
- YOLOv8s (11.2M params) instead of YOLOv8n (3.2M)
- Image size 1280 to preserve small button details on mobile screenshots
- MPS (Apple Silicon) GPU acceleration
- Optimized hyperparameters for small dataset
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
                 "../../data/xhs_dataset/augmented_yolo_v2/dataset.yaml")
)
PROJECT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "runs")
)

# Training hyperparameters
MODEL = "yolov8s.pt"     # Small model - better accuracy than nano (11.2M vs 3.2M params)
DEVICE = get_device()
EPOCHS = 150              # More epochs for better convergence
IMGSZ = 1280              # Higher resolution to preserve small UI button details
BATCH = 8                 # Smaller batch for larger image size
PATIENCE = 30             # More patience for early stopping
WORKERS = 4


def train():
    print(f"Dataset config: {DATASET_YAML}")
    print(f"Project dir: {PROJECT_DIR}")
    print(f"Model: {MODEL}")
    print(f"Device: {DEVICE}")
    print(f"Epochs: {EPOCHS}, ImgSize: {IMGSZ}, Batch: {BATCH}")
    print("-" * 60)

    # Load pretrained YOLOv8s
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
        name="xhs_ui_detect_v2",
        exist_ok=True,
        # Optimizer
        optimizer="AdamW",
        lr0=0.001,           # Initial learning rate
        lrf=0.01,            # Final learning rate factor
        warmup_epochs=5,     # Longer warmup for small dataset
        weight_decay=0.0005,
        # Augmentation settings - conservative since we already augmented offline
        hsv_h=0.01,         # Very mild online hue
        hsv_s=0.2,          # Mild saturation
        hsv_v=0.2,          # Mild value
        degrees=0.0,         # NO rotation
        translate=0.05,      # Very slight translation
        scale=0.15,          # Slight scale
        fliplr=0.0,          # NO horizontal flip
        flipud=0.0,          # NO vertical flip
        mosaic=0.5,          # Moderate mosaic
        mixup=0.0,           # No mixup
        copy_paste=0.0,      # No copy-paste
        # Other
        cos_lr=True,         # Cosine learning rate scheduler
        close_mosaic=20,     # Disable mosaic for last 20 epochs for fine-tuning
        verbose=True,
    )

    print("\n" + "=" * 60)
    print("Training complete!")
    print(f"Best model saved to: {PROJECT_DIR}/xhs_ui_detect_v2/weights/best.pt")

    return results


if __name__ == "__main__":
    train()
