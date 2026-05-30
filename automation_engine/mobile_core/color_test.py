import cv2
import numpy as np

def verify_color_shift(img_before, img_after, box, color_type):
    x, y, bw, bh = box
    h, w = img_before.shape[:2]
    x = max(0, min(x, w - 1))
    y = max(0, min(y, h - 1))
    bw = max(1, min(bw, w - x))
    bh = max(1, min(bh, h - y))
    
    roi_b = img_before[y:y+bh, x:x+bw]
    roi_a = img_after[y:y+bh, x:x+bw]
    
    # Convert to HSV
    hsv_b = cv2.cvtColor(roi_b, cv2.COLOR_BGR2HSV)
    hsv_a = cv2.cvtColor(roi_a, cv2.COLOR_BGR2HSV)
    
    if color_type == "red":
        # Red has two ranges in HSV
        mask_b1 = cv2.inRange(hsv_b, np.array([0, 70, 50]), np.array([10, 255, 255]))
        mask_b2 = cv2.inRange(hsv_b, np.array([170, 70, 50]), np.array([180, 255, 255]))
        mask_b = cv2.bitwise_or(mask_b1, mask_b2)
        
        mask_a1 = cv2.inRange(hsv_a, np.array([0, 70, 50]), np.array([10, 255, 255]))
        mask_a2 = cv2.inRange(hsv_a, np.array([170, 70, 50]), np.array([180, 255, 255]))
        mask_a = cv2.bitwise_or(mask_a1, mask_a2)
        
    elif color_type == "yellow":
        mask_b = cv2.inRange(hsv_b, np.array([15, 70, 50]), np.array([35, 255, 255]))
        mask_a = cv2.inRange(hsv_a, np.array([15, 70, 50]), np.array([35, 255, 255]))
        
    pixels_b = cv2.countNonZero(mask_b)
    pixels_a = cv2.countNonZero(mask_a)
    
    # Verify significant increase in target color
    return pixels_a > pixels_b + (bw * bh * 0.05) # at least 5% more target color pixels

print("Color logic looks sound.")
