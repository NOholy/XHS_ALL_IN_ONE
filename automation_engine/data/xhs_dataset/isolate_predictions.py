import os
import glob
import shutil

def main():
    src_dir = "/Users/qi/ai-code-project/XHS_ALL_IN_ONE/automation_engine/data/xhs_dataset/2026-06-07"
    dest_dir = "/Users/qi/ai-code-project/XHS_ALL_IN_ONE/automation_engine/data/xhs_dataset/2026-06-07_vlm_predictions"
    
    os.makedirs(dest_dir, exist_ok=True)
    
    # Also copy classes.txt
    classes_src = os.path.join(os.path.dirname(src_dir), "classes.txt")
    if os.path.exists(classes_src):
        shutil.copy2(classes_src, os.path.join(dest_dir, "classes.txt"))

    images = glob.glob(os.path.join(src_dir, "*.png")) + glob.glob(os.path.join(src_dir, "*.jpg"))
    
    moved_count = 0
    for img_path in images:
        base_name = os.path.basename(img_path)
        txt_name = os.path.splitext(base_name)[0] + ".txt"
        txt_path = os.path.join(src_dir, txt_name)
        
        if os.path.exists(txt_path):
            # Copy image
            shutil.copy2(img_path, os.path.join(dest_dir, base_name))
            # Move txt file (to clean up the original folder)
            shutil.move(txt_path, os.path.join(dest_dir, txt_name))
            moved_count += 1
            
    print(f"Successfully isolated {moved_count} images and their generated .txt labels into {dest_dir}")

if __name__ == "__main__":
    main()
