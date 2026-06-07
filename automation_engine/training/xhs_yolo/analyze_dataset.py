import json, glob, os
from collections import defaultdict

base = r'c:\Users\25831\PycharmProjects\XHS_ALL_IN_ONE\automation_engine\data\xhs_dataset'
devices = ['honor_EBG-AN00_2NSDU20526032516', 'huawei_PLA-AL10_3EV0225717013994', 'samsung_SM-N9600_2972a27dae1c7ece']

total_images = 0
total_shapes = 0
class_counts = defaultdict(int)
resolutions = set()
empty_images = 0
device_counts = {}

for dev in devices:
    dev_path = os.path.join(base, dev)
    jsons = sorted(glob.glob(os.path.join(dev_path, '*.json')))
    dev_img_count = 0
    for jf in jsons:
        with open(jf, 'r', encoding='utf-8') as f:
            data = json.load(f)
        total_images += 1
        dev_img_count += 1
        w = data.get('imageWidth', 0)
        h = data.get('imageHeight', 0)
        resolutions.add(str(w) + 'x' + str(h))
        shapes = data.get('shapes', [])
        if len(shapes) == 0:
            empty_images += 1
        total_shapes += len(shapes)
        for s in shapes:
            class_counts[s['label']] += 1
    device_counts[dev] = dev_img_count

print('=== Dataset Summary ===')
print('Total images:', total_images)
print('Empty images (no labels):', empty_images)
print('Total annotations:', total_shapes)
print('Avg annotations per image:', round(total_shapes / max(total_images, 1), 1))
print('Unique resolutions:', resolutions)
print('Unique classes:', len(class_counts))
print()
print('Per device:')
for dev, cnt in device_counts.items():
    print('  ' + dev.split('_')[0] + ': ' + str(cnt) + ' images')
print()
print('Class distribution (sorted by count):')
for cls, cnt in sorted(class_counts.items(), key=lambda x: -x[1]):
    print('  ' + cls + ': ' + str(cnt))
