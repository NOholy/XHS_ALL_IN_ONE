"""
XHS UI Element Detection - Dataset Preparation V2
Improved version with:
- 15x augmentation per image (up from 5x)
- Stratified train/val split ensuring all classes appear in val
- More diverse augmentation pipeline
- Better image quality preservation
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

# Configuration
INPUT_DIR = "automation_engine/data/xhs_dataset/honor_EBG-AN00_2NSDU20526032516"
OUTPUT_DIR = "automation_engine/data/xhs_dataset/augmented_yolo_v2"

AUGMENTATIONS_PER_IMAGE = 15   # 15x augmentation (up from 5x)
VAL_COUNT = 6                   # Use 6 images for val (~22%), ensuring class coverage

# Classes observed in the dataset
CLASSES = [
    '位置', '已点赞', '输入评论', '查看评论列表', '收藏', '首页', '消息', 
    '已收藏', '赞', '发现', '我', '已关注', '发送', '市集', '视频帖子', 
    '关注', '回复'
]
CLASS_TO_IDX = {cls_name: i for i, cls_name in enumerate(CLASSES)}

# ============================================================
# Multiple augmentation pipelines for more diversity
# ============================================================
def get_augmentation_pipelines():
    """Return a list of different augmentation pipelines for maximum diversity."""
    bbox_params = A.BboxParams(format='yolo', min_visibility=0.3, label_fields=['class_labels'])
    
    # Pipeline 1: Geometric - shift and scale
    p1 = A.Compose([
        A.Affine(translate_percent={'x': (-0.08, 0.08), 'y': (-0.15, 0.15)}, scale=(0.85, 1.15), rotate=0, p=0.9),
        A.RandomBrightnessContrast(brightness_limit=0.15, contrast_limit=0.15, p=0.5),
    ], bbox_params=bbox_params)
    
    # Pipeline 2: Color - simulate different screen modes
    p2 = A.Compose([
        A.ColorJitter(brightness=0.25, contrast=0.25, saturation=0.3, hue=0.12, p=0.9),
        A.Affine(translate_percent={'x': (-0.05, 0.05), 'y': (-0.1, 0.1)}, scale=(0.9, 1.1), rotate=0, p=0.5),
    ], bbox_params=bbox_params)
    
    # Pipeline 3: Noise and compression - simulate scrcpy/low quality capture
    p3 = A.Compose([
        A.ImageCompression(quality_range=(40, 85), p=0.8),
        A.GaussNoise(std_range=(0.02, 0.08), p=0.5),
        A.GaussianBlur(blur_limit=(3, 7), p=0.4),
        A.Affine(translate_percent={'x': (-0.05, 0.05), 'y': (-0.08, 0.08)}, scale=(0.95, 1.05), rotate=0, p=0.5),
    ], bbox_params=bbox_params)
    
    # Pipeline 4: Occlusion - simulate notifications/floating buttons
    p4 = A.Compose([
        A.CoarseDropout(num_holes_range=(1, 8), hole_height_range=(30, 150), hole_width_range=(30, 200), p=0.8),
        A.RandomBrightnessContrast(brightness_limit=0.2, contrast_limit=0.2, p=0.6),
        A.Affine(translate_percent={'x': (-0.06, 0.06), 'y': (-0.12, 0.12)}, scale=(0.9, 1.1), rotate=0, p=0.5),
    ], bbox_params=bbox_params)
    
    # Pipeline 5: Combined heavy - all transforms at once
    p5 = A.Compose([
        A.Affine(translate_percent={'x': (-0.1, 0.1), 'y': (-0.15, 0.15)}, scale=(0.85, 1.15), rotate=0, p=0.8),
        A.ColorJitter(brightness=0.2, contrast=0.2, saturation=0.2, hue=0.08, p=0.7),
        A.CoarseDropout(num_holes_range=(1, 4), hole_height_range=(20, 80), hole_width_range=(20, 80), p=0.4),
        A.ImageCompression(quality_range=(50, 95), p=0.3),
        A.GaussianBlur(blur_limit=(3, 5), p=0.15),
    ], bbox_params=bbox_params)
    
    return [p1, p2, p3, p4, p5]


def setup_directories(output_dir):
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    
    for split in ['train', 'val']:
        os.makedirs(os.path.join(output_dir, 'images', split), exist_ok=True)
        os.makedirs(os.path.join(output_dir, 'labels', split), exist_ok=True)


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
        
        # Clip coordinates to image boundaries
        x_min = max(0, x_min)
        y_min = max(0, y_min)
        x_max = min(img_width, x_max)
        y_max = min(img_height, y_max)
        
        if x_max <= x_min or y_max <= y_min:
            continue
            
        # Convert to YOLO format: x_center, y_center, width, height (normalized)
        x_center = ((x_min + x_max) / 2.0) / img_width
        y_center = ((y_min + y_max) / 2.0) / img_height
        width = (x_max - x_min) / img_width
        height = (y_max - y_min) / img_height
        
        bboxes.append([x_center, y_center, width, height])
        class_labels.append(CLASS_TO_IDX[label])
        
    return data['imagePath'], bboxes, class_labels


def save_yolo_data(image, bboxes, class_labels, split, base_name, output_dir):
    img_path = os.path.join(output_dir, 'images', split, f"{base_name}.jpg")
    label_path = os.path.join(output_dir, 'labels', split, f"{base_name}.txt")
    
    # Save Image (higher quality JPEG)
    cv2.imwrite(img_path, image, [cv2.IMWRITE_JPEG_QUALITY, 95])
    
    # Save labels
    with open(label_path, 'w', encoding='utf-8') as f:
        for bbox, label in zip(bboxes, class_labels):
            f.write(f"{label} {' '.join(map(str, bbox))}\n")


def stratified_split(json_files):
    """
    Stratified split: select val images to maximize class coverage.
    Greedily pick images that cover the most uncovered classes.
    """
    # Parse all files and track which classes each image contains
    file_classes = {}
    for jf in json_files:
        _, _, class_labels = parse_labelme_json(jf)
        file_classes[jf] = set(class_labels)
    
    val_files = set()
    covered_classes = set()
    all_classes = set(range(len(CLASSES)))
    
    # Greedy: pick the image that covers the most new classes
    for _ in range(VAL_COUNT):
        best_file = None
        best_new_classes = -1
        
        for jf, classes in file_classes.items():
            if jf in val_files:
                continue
            new_classes = len(classes - covered_classes)
            total_classes = len(classes)
            # Prefer images with more new classes, tie-break by total classes
            if new_classes > best_new_classes or (new_classes == best_new_classes and total_classes > best_new_classes):
                best_new_classes = new_classes
                best_file = jf
        
        if best_file:
            val_files.add(best_file)
            covered_classes.update(file_classes[best_file])
    
    train_files = [jf for jf in json_files if jf not in val_files]
    
    print(f"\n📊 Stratified Split:")
    print(f"   Train: {len(train_files)} images")
    print(f"   Val:   {len(val_files)} images")
    print(f"   Classes covered in val: {len(covered_classes)}/{len(CLASSES)}")
    uncovered = all_classes - covered_classes
    if uncovered:
        uncovered_names = [CLASSES[i] for i in uncovered]
        print(f"   ⚠️  Uncovered classes in val: {uncovered_names}")
    else:
        print(f"   ✅ All classes covered in validation set!")
    
    return train_files, list(val_files)


def process_dataset():
    print(f"Setting up directories in {OUTPUT_DIR}...")
    setup_directories(OUTPUT_DIR)
    
    json_files = sorted(glob.glob(os.path.join(INPUT_DIR, "*.json")))
    print(f"Found {len(json_files)} annotation files.")
    
    # Analyze class distribution
    print("\n📊 Class Distribution in Dataset:")
    class_counts = defaultdict(int)
    for jf in json_files:
        _, _, class_labels = parse_labelme_json(jf)
        for cl in class_labels:
            class_counts[cl] += 1
    for idx, cls_name in enumerate(CLASSES):
        count = class_counts.get(idx, 0)
        print(f"   {cls_name}: {count} instances")
    
    # Stratified split
    train_files, val_files = stratified_split(json_files)
    
    # Write classes.txt
    with open(os.path.join(OUTPUT_DIR, "classes.txt"), "w", encoding='utf-8') as f:
        for cls_name in CLASSES:
            f.write(f"{cls_name}\n")
            
    # Write dataset yaml
    yaml_content = f"""path: {os.path.abspath(OUTPUT_DIR)}
train: images/train
val: images/val

nc: {len(CLASSES)}
names:
"""
    for i, cls_name in enumerate(CLASSES):
        yaml_content += f"  {i}: {cls_name}\n"
        
    with open(os.path.join(OUTPUT_DIR, "dataset.yaml"), "w", encoding='utf-8') as f:
        f.write(yaml_content)
    
    # Get augmentation pipelines
    pipelines = get_augmentation_pipelines()
    
    # Process training set
    train_count = 0
    print(f"\n🔧 Processing training set ({AUGMENTATIONS_PER_IMAGE}x augmentation)...")
    for json_file in tqdm(train_files, desc="Train Images"):
        img_filename, bboxes, class_labels = parse_labelme_json(json_file)
        img_path = os.path.join(INPUT_DIR, img_filename)
        
        if not os.path.exists(img_path):
            print(f"Warning: Image {img_path} not found.")
            continue
            
        image = cv2.imread(img_path)
        if image is None:
            continue
            
        base_name = os.path.splitext(os.path.basename(img_path))[0]
        
        # Save original
        save_yolo_data(image, bboxes, class_labels, 'train', f"{base_name}_orig", OUTPUT_DIR)
        train_count += 1
        
        # Save augmented versions using rotating pipelines
        for i in range(AUGMENTATIONS_PER_IMAGE):
            try:
                pipeline = pipelines[i % len(pipelines)]
                transformed = pipeline(image=image, bboxes=bboxes, class_labels=class_labels)
                t_image = transformed['image']
                t_bboxes = transformed['bboxes']
                t_labels = transformed['class_labels']
                
                if len(t_bboxes) > 0:
                    save_yolo_data(t_image, t_bboxes, t_labels, 'train', f"{base_name}_aug_{i}", OUTPUT_DIR)
                    train_count += 1
            except Exception as e:
                print(f"Augmentation failed for {base_name} (pipeline {i % len(pipelines)}): {e}")
    
    # Process validation set (original only, no augmentation)
    val_count = 0
    print(f"\n🔧 Processing validation set (original only, no augmentation)...")
    for json_file in tqdm(val_files, desc="Val Images"):
        img_filename, bboxes, class_labels = parse_labelme_json(json_file)
        img_path = os.path.join(INPUT_DIR, img_filename)
        
        if not os.path.exists(img_path):
            print(f"Warning: Image {img_path} not found.")
            continue
            
        image = cv2.imread(img_path)
        if image is None:
            continue
            
        base_name = os.path.splitext(os.path.basename(img_path))[0]
        save_yolo_data(image, bboxes, class_labels, 'val', f"{base_name}_orig", OUTPUT_DIR)
        val_count += 1
    
    print(f"\n✅ Dataset preparation complete!")
    print(f"   Train images: {train_count}")
    print(f"   Val images: {val_count}")
    print(f"   Total: {train_count + val_count}")


if __name__ == "__main__":
    random.seed(42)
    np.random.seed(42)
    process_dataset()
