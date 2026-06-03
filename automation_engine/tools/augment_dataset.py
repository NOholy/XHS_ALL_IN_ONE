import os
import glob
import json
import cv2
import copy
import argparse
import albumentations as A

def augment_dataset(input_dir, output_dir, multiplier=5):
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        
    print(f"Input directory: {input_dir}")
    print(f"Output directory: {output_dir}")
    print(f"Multiplier: {multiplier}")

    # Define augmentation pipeline
    transform = A.Compose([
        A.RandomBrightnessContrast(p=0.5),
        A.GaussNoise(p=0.3),
        A.ColorJitter(brightness=0.2, contrast=0.2, saturation=0.2, hue=0.05, p=0.4),
        A.ShiftScaleRotate(shift_limit=0.03, scale_limit=0.03, rotate_limit=0, p=0.5, border_mode=cv2.BORDER_REPLICATE),
    ], bbox_params=A.BboxParams(format='pascal_voc', label_fields=['class_labels']))

    json_files = glob.glob(os.path.join(input_dir, "*.json"))
    
    if not json_files:
        print("No JSON files found in the input directory.")
        return

    for json_path in json_files:
        print(f"Processing: {os.path.basename(json_path)}")
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        img_name = data.get("imagePath")
        if not img_name:
            continue
            
        img_path = os.path.join(input_dir, img_name)
        if not os.path.exists(img_path):
            print(f"Image not found for {json_path}: {img_path}")
            continue
            
        # Read image
        image = cv2.imread(img_path)
        if image is None:
            print(f"Failed to load image: {img_path}")
            continue
            
        # Extract bboxes in pascal_voc format [xmin, ymin, xmax, ymax]
        bboxes = []
        class_labels = []
        for shape in data["shapes"]:
            points = shape["points"]
            # AnyLabeling rectangle has 4 points. We can just find min/max
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            xmin = max(0.0, min(xs))
            ymin = max(0.0, min(ys))
            xmax = min(float(image.shape[1]), max(xs))
            ymax = min(float(image.shape[0]), max(ys))
            
            # Albumentations expects xmin < xmax and ymin < ymax
            if xmax <= xmin or ymax <= ymin:
                continue
                
            bboxes.append([xmin, ymin, xmax, ymax])
            class_labels.append(shape["label"])
            
        base_name = os.path.splitext(os.path.basename(json_path))[0]
        
        # Save original copy to output dir to keep everything together
        # (Optional, but good for completeness)
        
        for i in range(multiplier):
            try:
                transformed = transform(image=image, bboxes=bboxes, class_labels=class_labels)
                transformed_image = transformed['image']
                transformed_bboxes = transformed['bboxes']
                transformed_class_labels = transformed['class_labels']
                
                new_base_name = f"{base_name}_aug_{i+1}"
                new_img_name = f"{new_base_name}.png"
                new_json_name = f"{new_base_name}.json"
                
                # Create new JSON data
                new_data = copy.deepcopy(data)
                new_data["imagePath"] = new_img_name
                new_data["imageData"] = None # Clean up base64 image data to save space
                new_data["imageHeight"] = transformed_image.shape[0]
                new_data["imageWidth"] = transformed_image.shape[1]
                
                new_shapes = []
                for idx, bbox in enumerate(transformed_bboxes):
                    xmin, ymin, xmax, ymax = bbox
                    # Convert back to LabelMe 4-point rectangle format
                    points = [
                        [xmin, ymin],
                        [xmax, ymin],
                        [xmax, ymax],
                        [xmin, ymax]
                    ]
                    
                    original_shape = copy.deepcopy(data["shapes"][idx])
                    original_shape["points"] = points
                    new_shapes.append(original_shape)
                    
                new_data["shapes"] = new_shapes
                
                # Save augmented image
                cv2.imwrite(os.path.join(output_dir, new_img_name), transformed_image)
                
                # Save augmented JSON
                with open(os.path.join(output_dir, new_json_name), 'w', encoding='utf-8') as f:
                    json.dump(new_data, f, ensure_ascii=False, indent=2)
                    
            except Exception as e:
                print(f"Error augmenting {base_name} iteration {i}: {e}")

    print("Data augmentation complete!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Augment LabelMe/X-AnyLabeling JSON dataset")
    parser.add_argument("--input", required=True, help="Path to input directory containing images and JSONs")
    parser.add_argument("--output", required=True, help="Path to output directory")
    parser.add_argument("--multiplier", type=int, default=5, help="Number of augmented copies to generate per image")
    
    args = parser.parse_args()
    augment_dataset(args.input, args.output, args.multiplier)
