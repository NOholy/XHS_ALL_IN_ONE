import os
import glob
import json
from PIL import Image

def yolo_to_labelme(pred_dir, classes_file):
    with open(classes_file, "r", encoding="utf-8") as f:
        classes = [line.strip() for line in f if line.strip()]

    images = glob.glob(os.path.join(pred_dir, "*.png")) + glob.glob(os.path.join(pred_dir, "*.jpg"))
    
    count = 0
    for img_path in images:
        base_name = os.path.basename(img_path)
        txt_path = os.path.splitext(img_path)[0] + ".txt"
        json_path = os.path.splitext(img_path)[0] + ".json"
        
        if not os.path.exists(txt_path):
            continue
            
        with Image.open(img_path) as img:
            img_w, img_h = img.size
            
        shapes = []
        with open(txt_path, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) != 5:
                    continue
                c_id = int(parts[0])
                cx, cy, bw, bh = map(float, parts[1:])
                
                c_name = classes[c_id] if c_id < len(classes) else str(c_id)
                
                x_center = cx * img_w
                y_center = cy * img_h
                w = bw * img_w
                h = bh * img_h
                
                x1 = x_center - w / 2
                y1 = y_center - h / 2
                x2 = x_center + w / 2
                y2 = y_center + h / 2
                
                shapes.append({
                    "label": c_name,
                    "score": 1.0,
                    "points": [
                        [x1, y1],
                        [x2, y1],
                        [x2, y2],
                        [x1, y2]
                    ],
                    "group_id": None,
                    "description": None,
                    "difficult": False,
                    "shape_type": "rectangle",
                    "flags": {},
                    "attributes": {},
                    "kie_linking": []
                })
                
        labelme_data = {
            "version": "4.0.0-beta.7",
            "flags": {},
            "checked": False,
            "shapes": shapes,
            "imagePath": base_name,
            "imageData": None,
            "imageHeight": img_h,
            "imageWidth": img_w,
            "description": ""
        }
        
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(labelme_data, f, ensure_ascii=False, indent=2)
            
        count += 1
        
    print(f"Converted {count} YOLO txt files to LabelMe json files.")

if __name__ == "__main__":
    pred_dir = "/Users/qi/ai-code-project/XHS_ALL_IN_ONE/automation_engine/data/xhs_dataset/2026-06-07_vlm_predictions"
    classes_file = "/Users/qi/ai-code-project/XHS_ALL_IN_ONE/automation_engine/data/xhs_dataset/2026-06-07_vlm_predictions/classes.txt"
    yolo_to_labelme(pred_dir, classes_file)
