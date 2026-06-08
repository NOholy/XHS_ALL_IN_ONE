import os
import json
import glob

DATASET_BASE_DIR = r"c:\Users\25831\PycharmProjects\XHS_ALL_IN_ONE\automation_engine\data\xhs_dataset"

# Find all device folders, skipping the augmented output folders
device_dirs = [os.path.join(DATASET_BASE_DIR, d) for d in os.listdir(DATASET_BASE_DIR)
               if os.path.isdir(os.path.join(DATASET_BASE_DIR, d)) and not d.startswith("augmented")]

merged_like = 0
merged_star = 0

for ddir in device_dirs:
    json_files = glob.glob(os.path.join(ddir, "*.json"))
    for jf in json_files:
        with open(jf, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        modified = False
        for shape in data.get("shapes", []):
            if shape["label"] == "已点赞":
                shape["label"] = "赞"
                merged_like += 1
                modified = True
            elif shape["label"] == "已收藏":
                shape["label"] = "收藏"
                merged_star += 1
                modified = True
                
        if modified:
            with open(jf, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

print(f"✅ Data cleaning complete!")
print(f"Replaced {merged_like} '已点赞' with '赞'")
print(f"Replaced {merged_star} '已收藏' with '收藏'")
