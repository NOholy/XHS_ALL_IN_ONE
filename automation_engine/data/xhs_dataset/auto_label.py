import os
import json
import glob
import requests

def main():
    # 1. Load classes
    classes_file = "/Users/qi/ai-code-project/XHS_ALL_IN_ONE/automation_engine/data/xhs_dataset/classes.txt"
    with open(classes_file, "r", encoding="utf-8") as f:
        classes = [line.strip() for line in f if line.strip()]
    
    class_to_idx = {c: i for i, c in enumerate(classes)}
    print(f"Loaded {len(classes)} classes: {classes}")

    # 2. Find images
    dataset_dir = "/Users/qi/ai-code-project/XHS_ALL_IN_ONE/automation_engine/data/xhs_dataset/2026-06-07"
    images = glob.glob(os.path.join(dataset_dir, "*.png")) + glob.glob(os.path.join(dataset_dir, "*.jpg"))
    print(f"Found {len(images)} images to process.")

    api_url = "http://localhost:8000/api/v1/detect"

    # 3. Process each image
    for img_path in images:
        label_path = os.path.splitext(img_path)[0] + ".txt"
        
        # skip if already labeled
        if os.path.exists(label_path):
            print(f"Skipping {os.path.basename(img_path)}, label already exists.")
            continue

        print(f"Processing {os.path.basename(img_path)} ...")
        
        try:
            with open(img_path, "rb") as f:
                files = {
                    "file": (os.path.basename(img_path), f, "image/png")
                }
                data = {
                    "categories": json.dumps(classes),
                    "use_sam2": "false",
                    "use_sam3": "false"
                }
                resp = requests.post(api_url, files=files, data=data)
            
            if resp.status_code != 200 and resp.status_code != 201:
                print(f"Error {resp.status_code}: {resp.text}")
                continue
            
            resp_data = resp.json().get("data", {})
            boxes = resp_data.get("boxes", [])
            img_w = resp_data.get("imageWidth", 1)
            img_h = resp_data.get("imageHeight", 1)
            
            # 4. Save to YOLO format
            yolo_lines = []
            for box in boxes:
                c_name = box.get("className")
                if c_name not in class_to_idx:
                    continue
                c_id = class_to_idx[c_name]
                
                x1, y1, x2, y2 = box["x1"], box["y1"], box["x2"], box["y2"]
                
                # convert to normalized center-x, center-y, w, h
                cx = ((x1 + x2) / 2.0) / img_w
                cy = ((y1 + y2) / 2.0) / img_h
                bw = (x2 - x1) / img_w
                bh = (y2 - y1) / img_h
                
                # ensure within [0, 1]
                cx = max(0.0, min(1.0, cx))
                cy = max(0.0, min(1.0, cy))
                bw = max(0.0, min(1.0, bw))
                bh = max(0.0, min(1.0, bh))
                
                yolo_lines.append(f"{c_id} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
            
            with open(label_path, "w", encoding="utf-8") as lf:
                lf.write("\n".join(yolo_lines) + "\n")
                
            print(f"  -> Saved {len(boxes)} boxes to {os.path.basename(label_path)}")
            
        except Exception as e:
            print(f"Failed to process {img_path}: {e}")

if __name__ == "__main__":
    main()
