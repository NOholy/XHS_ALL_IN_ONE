import os
import json
import glob
import cv2
import numpy as np
import random
import shutil
from tqdm import tqdm
import albumentations as A

# Configuration
INPUT_DIR = "automation_engine/data/xhs_dataset/honor_EBG-AN00_2NSDU20526032516"
OUTPUT_DIR = "automation_engine/data/xhs_dataset/augmented_yolo"

AUGMENTATIONS_PER_IMAGE = 5
TRAIN_RATIO = 0.8

# Classes observed in the dataset
CLASSES = [
    '位置', '已点赞', '输入评论', '查看评论列表', '收藏', '首页', '消息', 
    '已收藏', '赞', '发现', '我', '已关注', '发送', '市集', '视频帖子', 
    '关注', '回复'
]
CLASS_TO_IDX = {cls_name: i for i, cls_name in enumerate(CLASSES)}

# Augmentation Pipeline for Mobile Screenshots
# We avoid rotation, flipping, and severe distortion.
transform = A.Compose([
    # Simulate scrolling / shifting and slightly scaling (without rotation)
    A.Affine(translate_percent={'x': (-0.1, 0.1), 'y': (-0.1, 0.1)}, scale=(0.9, 1.1), rotate=0, p=0.7, mode=cv2.BORDER_CONSTANT, cval=0),
    # Simulate different screen color temperatures (e.g., Eye Comfort mode)
    A.ColorJitter(brightness=0.2, contrast=0.2, saturation=0.2, hue=0.1, p=0.7),
    # Simulate UI occlusion
    A.CoarseDropout(num_holes_range=(1, 5), hole_height_range=(20, 100), hole_width_range=(20, 100), p=0.5),
    # Slight compression/noise to improve robustness
    A.ImageCompression(quality_range=(60, 100), p=0.3),
    A.GaussianBlur(blur_limit=(3, 5), p=0.1)
], bbox_params=A.BboxParams(format='yolo', min_visibility=0.3, label_fields=['class_labels']))

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
        # Extract min and max coordinates
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
    
    # Save Image
    cv2.imwrite(img_path, image)
    
    # Save labels
    with open(label_path, 'w', encoding='utf-8') as f:
        for bbox, label in zip(bboxes, class_labels):
            f.write(f"{label} {' '.join(map(str, bbox))}\n")

def process_dataset():
    print(f"Setting up directories in {OUTPUT_DIR}...")
    setup_directories(OUTPUT_DIR)
    
    json_files = glob.glob(os.path.join(INPUT_DIR, "*.json"))
    print(f"Found {len(json_files)} annotation files.")
    
    # Write classes.txt
    with open(os.path.join(OUTPUT_DIR, "classes.txt"), "w", encoding='utf-8') as f:
        for cls_name in CLASSES:
            f.write(f"{cls_name}\n")
            
    # Write dataset yaml
    yaml_content = f"""path: {os.path.abspath(OUTPUT_DIR)}
train: images/train
val: images/val

names:
"""
    for i, cls_name in enumerate(CLASSES):
        yaml_content += f"  {i}: {cls_name}\n"
        
    with open(os.path.join(OUTPUT_DIR, "dataset.yaml"), "w", encoding='utf-8') as f:
        f.write(yaml_content)
            
    for json_file in tqdm(json_files, desc="Processing Images"):
        img_filename, bboxes, class_labels = parse_labelme_json(json_file)
        img_path = os.path.join(INPUT_DIR, img_filename)
        
        if not os.path.exists(img_path):
            print(f"Warning: Image {img_path} not found.")
            continue
            
        image = cv2.imread(img_path)
        if image is None:
            continue
            
        base_name = os.path.splitext(os.path.basename(img_path))[0]
        
        # Decide Train or Val
        split = 'train' if random.random() < TRAIN_RATIO else 'val'
        
        # 1. Save original
        save_yolo_data(image, bboxes, class_labels, split, f"{base_name}_orig", OUTPUT_DIR)
        
        # 2. Save augmented versions (only for train split generally, but we'll augment val slightly or maybe just keep orig)
        if split == 'train':
            for i in range(AUGMENTATIONS_PER_IMAGE):
                try:
                    transformed = transform(image=image, bboxes=bboxes, class_labels=class_labels)
                    t_image = transformed['image']
                    t_bboxes = transformed['bboxes']
                    t_labels = transformed['class_labels']
                    
                    if len(t_bboxes) > 0:
                        save_yolo_data(t_image, t_bboxes, t_labels, split, f"{base_name}_aug_{i}", OUTPUT_DIR)
                except Exception as e:
                    print(f"Augmentation failed for {base_name}: {e}")

if __name__ == "__main__":
    random.seed(42)
    process_dataset()
    print("Dataset preparation and augmentation complete!")
