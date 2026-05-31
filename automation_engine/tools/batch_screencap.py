import subprocess
import time
import os
import concurrent.futures
import shutil

def _ensure_adb_in_path():
    """Ensure that the 'adb' executable is available in the system PATH."""
    if shutil.which("adb"):
        return
    
    # Common Android SDK paths on macOS, Windows, and Linux
    home = os.path.expanduser("~")
    common_paths = [
        os.path.join(home, "Library/Android/sdk/platform-tools"),
        os.path.join(home, "AppData/Local/Android/Sdk/platform-tools"),
        "/usr/local/bin",
        "/opt/homebrew/bin",
    ]
    
    for path in common_paths:
        if os.path.isdir(path) and os.path.isfile(os.path.join(path, "adb")):
            os.environ["PATH"] = path + os.path.pathsep + os.environ.get("PATH", "")
            return

_ensure_adb_in_path()

# 创建数据集主目录，放到项目 data 目录下
DATASET_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "xhs_dataset")
os.makedirs(DATASET_DIR, exist_ok=True)

def get_connected_devices():
    """获取所有已连接的 adb 设备序列号"""
    result = subprocess.run(["adb", "devices"], capture_output=True, text=True)
    lines = result.stdout.strip().split('\n')[1:]
    devices = [line.split()[0] for line in lines if 'device' in line]
    return devices

def get_device_info(device_id):
    """获取设备的品牌和型号并清洗数据"""
    try:
        # 获取品牌并转为小写，清除前后空白字符
        res_brand = subprocess.run(["adb", "-s", device_id, "shell", "getprop", "ro.product.brand"], capture_output=True, text=True, timeout=3)
        brand = res_brand.stdout.strip().lower() or "unknown"
        
        # 获取型号，空格替换为下划线
        res_model = subprocess.run(["adb", "-s", device_id, "shell", "getprop", "ro.product.model"], capture_output=True, text=True, timeout=3)
        model = res_model.stdout.strip().replace(" ", "_") or "unknown"
        
        # 过滤可能破坏路径和文件名的非法字符
        import re
        brand = re.sub(r'[\\/*?:"<>|]', "", brand)
        model = re.sub(r'[\\/*?:"<>|]', "", model)
        
        return brand, model
    except Exception:
        return "unknown", "unknown"

def get_next_index(device_dir, prefix):
    """根据已有的截图文件计算下一个 4 位递增序号"""
    if not os.path.exists(device_dir):
        return 1
    
    indices = []
    import re
    # 匹配以 prefix 开头，跟着 4 位数字序号结尾的 png 文件
    pattern = re.compile(rf"{re.escape(prefix)}_(\d{{4}})\.png$")
    for f in os.listdir(device_dir):
        m = pattern.match(f)
        if m:
            indices.append(int(m.group(1)))
            
    if indices:
        return max(indices) + 1
    else:
        # 回退：如果没有符合规律的命名，直接计算 png 文件数量作为基础序号
        png_files = [f for f in os.listdir(device_dir) if f.endswith('.png')]
        return len(png_files) + 1

def capture_device(device_info):
    """对单台设备进行截图并直接流式传输到电脑，确保纯净无轨迹"""
    device_id = device_info["id"]
    folder_name = device_info["folder_name"]
    
    # 为每台设备建一个单独的文件夹，方便后期管理
    device_dir = os.path.join(DATASET_DIR, folder_name)
    os.makedirs(device_dir, exist_ok=True)
    
    # 获取递增序号并构造文件名
    index = get_next_index(device_dir, folder_name)
    filename = f"{folder_name}_{index:04d}.png"
    local_path = os.path.join(device_dir, filename)
    
    try:
        # 1. 临时关闭指针位置和触摸显示（保证数据集绝对纯净）
        subprocess.run(["adb", "-s", device_id, "shell", "settings", "put", "system", "pointer_location", "0"], timeout=2)
        subprocess.run(["adb", "-s", device_id, "shell", "settings", "put", "system", "show_touches", "0"], timeout=2)
        
        # 2. 高速截图：直接通过 exec-out 输出到电脑，不经过手机存储，速度提升 300%
        with open(local_path, "wb") as f:
            subprocess.run(["adb", "-s", device_id, "exec-out", "screencap", "-p"], stdout=f, check=True, timeout=10)
            
        # 3. 恢复指针位置
        subprocess.run(["adb", "-s", device_id, "shell", "settings", "put", "system", "pointer_location", "1"], timeout=2)
        subprocess.run(["adb", "-s", device_id, "shell", "settings", "put", "system", "show_touches", "1"], timeout=2)
        
        return f"✅ 设备 {device_id} ({device_info['brand']}_{device_info['model']}) 截图成功 -> {filename}"
    except Exception as e:
        return f"❌ 设备 {device_id} ({device_info['brand']}_{device_info['model']}) 截图失败: {e}"

def main():
    devices = get_connected_devices()
    if not devices:
        print("未检测到任何连接的 ADB 设备！请检查 USB 连接。")
        return
    
    print(f"正在获取 {len(devices)} 台连接设备的信息，请稍候...")
    device_info_list = []
    for dev in devices:
        brand, model = get_device_info(dev)
        folder_name = f"{brand}_{model}_{dev}"
        device_info_list.append({
            "id": dev,
            "brand": brand,
            "model": model,
            "folder_name": folder_name
        })
        print(f"📱 设备 {dev} -> 品牌: {brand} | 型号: {model}")
        
    print(f"\n检测完成！所有截图将保存在目录: {DATASET_DIR} 对应的设备子文件夹下。\n")
    
    count = 1
    while True:
        user_input = input(f"【第 {count} 批次】按 Enter 键同时抓取所有屏幕 (输入 'q' 退出): ")
        if user_input.lower() == 'q':
            break
            
        # 使用线程池并发截图
        print("正在抓取中，请稍候...")
        start_time = time.time()
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(device_info_list)) as executor:
            # 提交所有设备的截图任务
            futures = [executor.submit(capture_device, info) for info in device_info_list]
            
            # 打印每个设备的结果
            for future in concurrent.futures.as_completed(futures):
                print(future.result())
                
        print(f"⏱ 本批次耗时: {time.time() - start_time:.2f} 秒\n")
        count += 1

if __name__ == "__main__":
    main()
