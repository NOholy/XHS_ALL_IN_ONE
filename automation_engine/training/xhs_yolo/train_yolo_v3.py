"""
XHS UI Element Detection - YOLOv8 Training Script V2 (Multi-Device)
====================================================================
- YOLOv8s (11.2M params) for good accuracy/speed balance
- Image size 1280 to preserve small UI button details on HD screenshots
- CUDA GPU acceleration (auto-fallback to CPU)
- Hyperparameters optimized for:
  * Mobile screenshot detection (no rotation/flip/mosaic)
  * Small-to-medium dataset with offline augmentation
  * 19 UI element classes with class imbalance
"""

import os
import torch
from ultralytics import YOLO


def get_device():
    """Auto-detect best available GPU."""
    if torch.cuda.is_available():
        gpu_name = torch.cuda.get_device_name(0)
        gpu_mem = torch.cuda.get_device_properties(0).total_memory / (1024**3)
        print(f"✅ NVIDIA CUDA GPU detected: {gpu_name} ({gpu_mem:.1f} GB)")
        return "0"
    elif hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
        print("✅ Apple MPS GPU detected.")
        return "mps"
    else:
        print("⚠️  No GPU found, falling back to CPU.")
        return "cpu"


# ============================================================
# Paths
# ============================================================
DATASET_YAML = os.path.abspath(
    os.path.join(os.path.dirname(__file__),
                 "../../data/xhs_dataset/augmented_yolo_v3/dataset.yaml")
)
PROJECT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "runs")
)

# ============================================================
# Training hyperparameters
# ============================================================
MODEL = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../models/xhs_ui_yolo_v2/best.pt"))
DEVICE = get_device()
EPOCHS = 200               # More epochs for convergence with larger dataset
IMGSZ = 1280               # High res for 1080p+ mobile screenshots
BATCH = 4                  # RTX 3060 Laptop 6GB VRAM, imgsz=1280 needs small batch
PATIENCE = 30              # Early stopping patience
WORKERS = 4


def train():
    print(f"\n{'=' * 60}")
    print(f"XHS UI Detection - YOLO Training V2")
    print(f"{'=' * 60}")
    print(f"Dataset:  {DATASET_YAML}")
    print(f"Project:  {PROJECT_DIR}")
    print(f"Model:    {MODEL}")
    print(f"Device:   {DEVICE}")
    print(f"Epochs:   {EPOCHS}")
    print(f"ImgSize:  {IMGSZ}")
    print(f"Batch:    {BATCH}")
    print(f"{'=' * 60}\n")

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
        name="xhs_ui_detect_v3",
        exist_ok=True,

        # Optimizer
        optimizer="AdamW",
        lr0=0.001,
        lrf=0.01,
        warmup_epochs=5,
        weight_decay=0.0005,

        # Loss weights — boost classification loss for imbalanced classes
        cls=1.5,

        # Online augmentation — CONSERVATIVE since we already augmented offline
        # Key: NO rotation, NO flip, NO mosaic for mobile screenshots
        hsv_h=0.01,          # Very mild hue jitter
        hsv_s=0.2,           # Mild saturation
        hsv_v=0.2,           # Mild value/brightness
        degrees=0.0,          # NO rotation (screenshots always upright)
        translate=0.05,       # Very slight translation
        scale=0.15,           # Slight scale
        fliplr=0.0,           # NO horizontal flip (text would mirror)
        flipud=0.0,           # NO vertical flip
        mosaic=0.0,           # NO mosaic (breaks UI layout context)
        mixup=0.0,            # NO mixup (breaks UI semantics)
        copy_paste=0.0,       # NO copy-paste

        # Learning rate schedule
        cos_lr=True,

        # Other
        verbose=True,
    )

    best_path = os.path.join(PROJECT_DIR, "xhs_ui_detect_v2", "weights", "best.pt")
    print(f"\n{'=' * 60}")
    print(f"✅ Training complete!")
    print(f"   Best model: {best_path}")
    print(f"{'=' * 60}")

    return results


if __name__ == "__main__":
    train()
