import os
import cv2
import glob
import matplotlib.pyplot as plt
import matplotlib.patches as patches

# 解决 macOS 下 matplotlib 中文显示问题
plt.rcParams['font.sans-serif'] = ['Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False

def view_yolo_labels(dataset_dir, classes_file):
    with open(classes_file, "r", encoding="utf-8") as f:
        classes = [line.strip() for line in f if line.strip()]

    images = glob.glob(os.path.join(dataset_dir, "*.png")) + glob.glob(os.path.join(dataset_dir, "*.jpg"))
    images = sorted(images)

    if not images:
        print("No images found.")
        return

    print(f"Found {len(images)} images. Close the window to view the next one.")

    for img_path in images:
        label_path = os.path.splitext(img_path)[0] + ".txt"
        if not os.path.exists(label_path):
            continue

        # Load image
        img = cv2.imread(img_path)
        if img is None:
            continue
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        h, w, _ = img.shape

        # Create figure and axes
        fig, ax = plt.subplots(1, figsize=(10, 16))
        ax.imshow(img)
        plt.title(f"Preview: {os.path.basename(img_path)}")

        # Read labels
        with open(label_path, "r", encoding="utf-8") as lf:
            lines = lf.readlines()

        for line in lines:
            parts = line.strip().split()
            if len(parts) != 5:
                continue
            
            c_id = int(parts[0])
            cx, cy, bw, bh = map(float, parts[1:])

            # Convert YOLO (normalized center) back to absolute pixels
            box_w = bw * w
            box_h = bh * h
            x_min = (cx * w) - (box_w / 2)
            y_min = (cy * h) - (box_h / 2)

            # Draw box
            rect = patches.Rectangle((x_min, y_min), box_w, box_h, linewidth=2, edgecolor='red', facecolor='none')
            ax.add_patch(rect)
            
            # Add label
            c_name = classes[c_id] if c_id < len(classes) else str(c_id)
            ax.text(x_min, y_min - 5, c_name, color='red', fontsize=12, backgroundcolor='white', fontweight='bold')

        plt.axis('off')
        plt.show() # Blocks until window is closed

if __name__ == "__main__":
    dataset_dir = "/Users/qi/ai-code-project/XHS_ALL_IN_ONE/automation_engine/data/xhs_dataset/2026-06-07"
    classes_file = "/Users/qi/ai-code-project/XHS_ALL_IN_ONE/automation_engine/data/xhs_dataset/classes.txt"
    view_yolo_labels(dataset_dir, classes_file)
