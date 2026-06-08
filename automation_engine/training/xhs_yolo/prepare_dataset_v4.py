"""
XHS UI Element Detection - Dataset Preparation V2 (Multi-Device)
================================================================
- Reads annotated screenshots from ALL device folders under xhs_dataset/
- 19 unified classes (including 帖子封面, 返回)
- 10x augmentation per image (tuned for 81 base images → ~810 train images)
- Stratified train/val split ensuring max class coverage across devices
- Augmentation pipelines designed specifically for mobile screenshots:
  * NO rotation, NO flip (UI is always upright)
  * NO mosaic/mixup (breaks UI context)
  * YES: color jitter, dark-mode simulation, compression, noise, shift/scale
- Extensible: just drop new device folders into xhs_dataset/ to include them
"""

import os
import json
import glob
import cv2
import numpy as np
import random
import shutil
from collections import defaultdict
from tqdm import tqdm
import albumentations as A

# ============================================================
# Configuration
# ============================================================
# Base directory containing per-device subfolders
DATASET_BASE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../../data/xhs_dataset")
)
OUTPUT_DIR = os.path.join(DATASET_BASE_DIR, "augmented_yolo_v4")

AUGMENTATIONS_PER_IMAGE = 10   # 10x augmentation (81 base × 10 = ~810 train)
VAL_PER_DEVICE = 5             # 5 val images per device (~15 total, ~18%)

# Unified 19 classes (order = class index)
CLASSES = [
    '位置',          # 0
    '输入评论',      # 1
    '查看评论列表',  # 2
    '收藏',          # 3
    '首页',          # 4
    '消息',          # 5
    '赞',            # 6
    '发现',          # 7
    '我',            # 8
    '已关注',        # 9
    '发送',          # 10
    '市集',          # 11
    '视频帖子',      # 12
    '关注',          # 13
    '回复',          # 14
    '帖子封面',      # 15
    '返回',          # 16
]
CLASS_TO_IDX = {cls_name: i for i, cls_name in enumerate(CLASSES)}


# ============================================================
# Auto-discover device folders
# ============================================================
def discover_device_dirs(base_dir):
    """Find all device subfolders that contain .json annotation files."""
    device_dirs = []
    for entry in sorted(os.listdir(base_dir)):
        full_path = os.path.join(base_dir, entry)
        if os.path.isdir(full_path) and not entry.startswith("augmented_yolo"):
            jsons = glob.glob(os.path.join(full_path, "*.json"))
            if len(jsons) > 0:
                device_dirs.append(full_path)
    return device_dirs


# ============================================================
# Augmentation Pipelines (mobile-screenshot specific)
# ============================================================
def get_augmentation_pipelines():
    """
    Return diverse augmentation pipelines for mobile screenshots.
    
    Key principles:
    - NO rotation (screenshots are always upright)
    - NO flip (text would be mirrored/inverted)
    - Shift/scale to simulate scrolling and different screen densities
    - Color jitter to simulate dark mode, eye-comfort mode, different screens
    - Compression/noise to simulate varying capture quality (adb, scrcpy, etc.)
    """
    bbox_params = A.BboxParams(
        format='yolo', min_visibility=0.3, label_fields=['class_labels']
    )

    # Pipeline 1: Geometric — simulate scrolling and different screen sizes
    p1 = A.Compose([
        A.Affine(
            translate_percent={'x': (-0.08, 0.08), 'y': (-0.15, 0.15)},
            scale=(0.85, 1.15), rotate=0, p=0.9,
        ),
        A.RandomBrightnessContrast(brightness_limit=0.15, contrast_limit=0.15, p=0.5),
    ], bbox_params=bbox_params)

    # Pipeline 2: Color — simulate different screen color temperatures
    p2 = A.Compose([
        A.ColorJitter(brightness=0.25, contrast=0.25, saturation=0.3, hue=0.12, p=0.9),
        A.Affine(
            translate_percent={'x': (-0.05, 0.05), 'y': (-0.1, 0.1)},
            scale=(0.9, 1.1), rotate=0, p=0.5,
        ),
    ], bbox_params=bbox_params)

    # Pipeline 3: Noise & compression — simulate scrcpy/adb capture quality
    p3 = A.Compose([
        A.ImageCompression(quality_range=(40, 85), p=0.8),
        A.GaussNoise(std_range=(0.02, 0.08), p=0.5),
        A.GaussianBlur(blur_limit=(3, 7), p=0.4),
        A.Affine(
            translate_percent={'x': (-0.05, 0.05), 'y': (-0.08, 0.08)},
            scale=(0.95, 1.05), rotate=0, p=0.5,
        ),
    ], bbox_params=bbox_params)

    # Pipeline 4: Occlusion — simulate notifications, floating buttons, popups
    p4 = A.Compose([
        A.CoarseDropout(
            num_holes_range=(1, 8),
            hole_height_range=(30, 150),
            hole_width_range=(30, 200),
            p=0.8,
        ),
        A.RandomBrightnessContrast(brightness_limit=0.2, contrast_limit=0.2, p=0.6),
        A.Affine(
            translate_percent={'x': (-0.06, 0.06), 'y': (-0.12, 0.12)},
            scale=(0.9, 1.1), rotate=0, p=0.5,
        ),
    ], bbox_params=bbox_params)

    # Pipeline 5: Combined moderate — everything at lower intensity
    p5 = A.Compose([
        A.Affine(
            translate_percent={'x': (-0.1, 0.1), 'y': (-0.15, 0.15)},
            scale=(0.85, 1.15), rotate=0, p=0.8,
        ),
        A.ColorJitter(brightness=0.2, contrast=0.2, saturation=0.2, hue=0.08, p=0.7),
        A.CoarseDropout(
            num_holes_range=(1, 4),
            hole_height_range=(20, 80),
            hole_width_range=(20, 80),
            p=0.4,
        ),
        A.ImageCompression(quality_range=(50, 95), p=0.3),
        A.GaussianBlur(blur_limit=(3, 5), p=0.15),
    ], bbox_params=bbox_params)

    # Pipeline 6: Dark mode simulation — low brightness, shifted hue
    p6 = A.Compose([
        A.RandomBrightnessContrast(
            brightness_limit=(-0.4, -0.1), contrast_limit=0.2, p=0.9,
        ),
        A.HueSaturationValue(
            hue_shift_limit=10, sat_shift_limit=30,
            val_shift_limit=(-40, 0), p=0.7,
        ),
        A.Affine(
            translate_percent={'x': (-0.04, 0.04), 'y': (-0.08, 0.08)},
            scale=(0.92, 1.08), rotate=0, p=0.5,
        ),
    ], bbox_params=bbox_params)

    # Pipeline 7: High brightness — simulate outdoor / high-brightness screen
    p7 = A.Compose([
        A.RandomBrightnessContrast(
            brightness_limit=(0.1, 0.4), contrast_limit=(-0.1, 0.3), p=0.9,
        ),
        A.ColorJitter(brightness=0.1, contrast=0.1, saturation=0.15, hue=0.05, p=0.6),
        A.Affine(
            translate_percent={'x': (-0.05, 0.05), 'y': (-0.1, 0.1)},
            scale=(0.9, 1.1), rotate=0, p=0.5,
        ),
    ], bbox_params=bbox_params)

    return [p1, p2, p3, p4, p5, p6, p7]


# ============================================================
# Directory setup
# ============================================================
def setup_directories(output_dir):
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)

    for split in ['train', 'val']:
        os.makedirs(os.path.join(output_dir, 'images', split), exist_ok=True)
        os.makedirs(os.path.join(output_dir, 'labels', split), exist_ok=True)


# ============================================================
# Parse LabelMe JSON → YOLO format
# ============================================================
def parse_labelme_json(json_file):
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    bboxes = []
    class_labels = []

    img_height = data['imageHeight']
    img_width = data['imageWidth']

    for shape in data.get('shapes', []):
        label = shape['label']
        if label not in CLASS_TO_IDX:
            continue

        points = shape['points']
        x_coords = [p[0] for p in points]
        y_coords = [p[1] for p in points]

        x_min, x_max = min(x_coords), max(x_coords)
        y_min, y_max = min(y_coords), max(y_coords)

        # Clip to image boundaries
        x_min = max(0, x_min)
        y_min = max(0, y_min)
        x_max = min(img_width, x_max)
        y_max = min(img_height, y_max)

        if x_max <= x_min or y_max <= y_min:
            continue

        # YOLO format: x_center, y_center, width, height (normalized 0-1)
        x_center = ((x_min + x_max) / 2.0) / img_width
        y_center = ((y_min + y_max) / 2.0) / img_height
        width = (x_max - x_min) / img_width
        height = (y_max - y_min) / img_height

        bboxes.append([x_center, y_center, width, height])
        class_labels.append(CLASS_TO_IDX[label])

    return data['imagePath'], bboxes, class_labels


# ============================================================
# Save image + YOLO labels
# ============================================================
def save_yolo_data(image, bboxes, class_labels, split, base_name, output_dir):
    img_path = os.path.join(output_dir, 'images', split, f"{base_name}.jpg")
    label_path = os.path.join(output_dir, 'labels', split, f"{base_name}.txt")

    cv2.imwrite(img_path, image, [cv2.IMWRITE_JPEG_QUALITY, 95])

    with open(label_path, 'w', encoding='utf-8') as f:
        for bbox, label in zip(bboxes, class_labels):
            f.write(f"{int(label)} {' '.join(f'{v:.6f}' for v in bbox)}\n")


# ============================================================
# Stratified split — per-device, maximize class coverage in val
# ============================================================
def stratified_split_per_device(device_files_dict, val_per_device):
    """
    For each device, greedily pick val images to maximize class coverage.
    Returns (train_files, val_files) as flat lists of (device_dir, json_path).
    """
    all_train = []
    all_val = []
    total_covered = set()

    for device_dir, json_files in device_files_dict.items():
        device_name = os.path.basename(device_dir).split('_')[0]

        # Parse classes for each file
        file_classes = {}
        for jf in json_files:
            _, _, class_labels = parse_labelme_json(jf)
            file_classes[jf] = set(class_labels)

        # Greedy selection
        val_files = set()
        covered = set()
        for _ in range(min(val_per_device, len(json_files))):
            best_file = None
            best_new = -1
            best_total = -1
            for jf, classes in file_classes.items():
                if jf in val_files:
                    continue
                new = len(classes - covered)
                total = len(classes)
                if new > best_new or (new == best_new and total > best_total):
                    best_new = new
                    best_total = total
                    best_file = jf
            if best_file:
                val_files.add(best_file)
                covered.update(file_classes[best_file])

        total_covered.update(covered)

        for jf in json_files:
            entry = (device_dir, jf)
            if jf in val_files:
                all_val.append(entry)
            else:
                all_train.append(entry)

        print(f"   {device_name}: {len(json_files) - len(val_files)} train / {len(val_files)} val "
              f"(val covers {len(covered)} classes)")

    # Post-split fix: ensure every class has training samples
    # Count classes in training set
    train_class_counts = defaultdict(int)
    for device_dir, jf in all_train:
        _, _, class_labels = parse_labelme_json(jf)
        for cl in class_labels:
            train_class_counts[cl] += 1

    # Find classes with 0 training samples
    missing_classes = set()
    for i in range(len(CLASSES)):
        if train_class_counts[i] == 0:
            missing_classes.add(i)

    if missing_classes:
        missing_names = [CLASSES[i] for i in missing_classes]
        print(f"   ⚠️  Classes with 0 train samples: {missing_names}")
        print(f"   → Duplicating val images with these classes into training set")

        # Find val images that contain missing classes and add them to train too
        duplicated = 0
        for entry in list(all_val):
            device_dir, jf = entry
            _, _, class_labels = parse_labelme_json(jf)
            if missing_classes.intersection(set(class_labels)):
                all_train.append(entry)
                duplicated += 1
                # Update which classes are now covered
                missing_classes -= set(class_labels)
        print(f"   → Duplicated {duplicated} val images into training set")

    print(f"   Total val class coverage: {len(total_covered)}/{len(CLASSES)}")
    uncovered = set(range(len(CLASSES))) - total_covered
    if uncovered:
        names = [CLASSES[i] for i in uncovered]
        print(f"   ⚠️  Uncovered in val: {names}")
    else:
        print(f"   ✅ All classes covered in validation set!")

    return all_train, all_val


# ============================================================
# Rare-class oversampling: extra augmentations for under-represented classes
# ============================================================
RARE_CLASS_THRESHOLD = 15  # classes with <= this many annotations get extra augs
RARE_EXTRA_AUGS = 5        # extra augmentations for images containing rare classes


def has_rare_classes(class_labels, class_counts):
    """Check if any label in this image belongs to a rare class."""
    for cl in class_labels:
        cls_name = CLASSES[cl]
        if class_counts.get(cls_name, 0) <= RARE_CLASS_THRESHOLD:
            return True
    return False


# ============================================================
# Main processing
# ============================================================
def process_dataset():
    # Discover devices
    device_dirs = discover_device_dirs(DATASET_BASE_DIR)
    print(f"📱 Found {len(device_dirs)} device folders:")
    for d in device_dirs:
        print(f"   • {os.path.basename(d)}")

    # Collect all JSON files per device
    device_files = {}
    for dd in device_dirs:
        jsons = sorted(glob.glob(os.path.join(dd, "*.json")))
        device_files[dd] = jsons
        print(f"   {os.path.basename(dd)}: {len(jsons)} images")

    total_images = sum(len(v) for v in device_files.values())
    print(f"\n📊 Total: {total_images} annotated images")

    # Analyze class distribution across all devices
    print("\n📊 Class Distribution (all devices):")
    class_counts = defaultdict(int)
    for dd, jsons in device_files.items():
        for jf in jsons:
            _, _, class_labels = parse_labelme_json(jf)
            for cl in class_labels:
                class_counts[CLASSES[cl]] += 1

    for idx, cls_name in enumerate(CLASSES):
        count = class_counts.get(cls_name, 0)
        marker = "🔴" if count <= RARE_CLASS_THRESHOLD else ("🟡" if count <= 30 else "🟢")
        print(f"   {marker} [{idx:2d}] {cls_name}: {count}")

    # Setup output
    print(f"\n🔧 Setting up output in {OUTPUT_DIR}...")
    setup_directories(OUTPUT_DIR)

    # Stratified split
    print(f"\n📊 Stratified Split (val_per_device={VAL_PER_DEVICE}):")
    train_entries, val_entries = stratified_split_per_device(device_files, VAL_PER_DEVICE)
    print(f"   Train: {len(train_entries)} images")
    print(f"   Val:   {len(val_entries)} images")

    # Write classes.txt
    with open(os.path.join(OUTPUT_DIR, "classes.txt"), "w", encoding='utf-8') as f:
        for cls_name in CLASSES:
            f.write(f"{cls_name}\n")

    # Write dataset.yaml
    abs_output = os.path.abspath(OUTPUT_DIR)
    yaml_content = f"path: {abs_output}\n"
    yaml_content += "train: images/train\n"
    yaml_content += "val: images/val\n\n"
    yaml_content += f"nc: {len(CLASSES)}\n"
    yaml_content += "names:\n"
    for i, cls_name in enumerate(CLASSES):
        yaml_content += f"  {i}: {cls_name}\n"

    with open(os.path.join(OUTPUT_DIR, "dataset.yaml"), "w", encoding='utf-8') as f:
        f.write(yaml_content)

    # Augmentation pipelines
    pipelines = get_augmentation_pipelines()

    # Process training set
    train_count = 0
    print(f"\n🔧 Processing training set ({AUGMENTATIONS_PER_IMAGE}x augmentation, "
          f"+{RARE_EXTRA_AUGS}x for rare classes)...")

    for device_dir, json_file in tqdm(train_entries, desc="Train"):
        img_filename, bboxes, class_labels = parse_labelme_json(json_file)
        img_path = os.path.join(device_dir, img_filename)

        if not os.path.exists(img_path):
            print(f"⚠️  Image not found: {img_path}")
            continue

        image = cv2.imread(img_path)
        if image is None:
            continue

        # Use device+filename as unique base name
        device_tag = os.path.basename(device_dir).split('_')[0]
        file_stem = os.path.splitext(os.path.basename(img_path))[0]
        base_name = f"{device_tag}_{file_stem}"

        # Save original
        save_yolo_data(image, bboxes, class_labels, 'train', f"{base_name}_orig", OUTPUT_DIR)
        train_count += 1

        # Determine augmentation count (extra for rare classes)
        aug_count = AUGMENTATIONS_PER_IMAGE
        if has_rare_classes(class_labels, class_counts):
            aug_count += RARE_EXTRA_AUGS

        # Augmented versions
        for i in range(aug_count):
            try:
                pipeline = pipelines[i % len(pipelines)]
                transformed = pipeline(image=image, bboxes=bboxes, class_labels=class_labels)
                t_image = transformed['image']
                t_bboxes = transformed['bboxes']
                t_labels = transformed['class_labels']

                if len(t_bboxes) > 0:
                    save_yolo_data(t_image, t_bboxes, t_labels, 'train',
                                   f"{base_name}_aug_{i:02d}", OUTPUT_DIR)
                    train_count += 1
            except Exception as e:
                print(f"⚠️  Aug failed for {base_name} (pipeline {i % len(pipelines)}): {e}")

    # Process validation set (originals only, NO augmentation)
    val_count = 0
    print(f"\n🔧 Processing validation set (originals only)...")
    for device_dir, json_file in tqdm(val_entries, desc="Val"):
        img_filename, bboxes, class_labels = parse_labelme_json(json_file)
        img_path = os.path.join(device_dir, img_filename)

        if not os.path.exists(img_path):
            print(f"⚠️  Image not found: {img_path}")
            continue

        image = cv2.imread(img_path)
        if image is None:
            continue

        device_tag = os.path.basename(device_dir).split('_')[0]
        file_stem = os.path.splitext(os.path.basename(img_path))[0]
        base_name = f"{device_tag}_{file_stem}"

        save_yolo_data(image, bboxes, class_labels, 'val', f"{base_name}_orig", OUTPUT_DIR)
        val_count += 1

    print(f"\n{'=' * 60}")
    print(f"✅ Dataset preparation complete!")
    print(f"   Train images: {train_count} (originals + augmented)")
    print(f"   Val images:   {val_count} (originals only)")
    print(f"   Total:        {train_count + val_count}")
    print(f"   Classes:      {len(CLASSES)}")
    print(f"   Output:       {abs_output}")
    print(f"   YAML:         {os.path.join(abs_output, 'dataset.yaml')}")


if __name__ == "__main__":
    random.seed(42)
    np.random.seed(42)
    process_dataset()
