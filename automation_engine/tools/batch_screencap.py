import subprocess
import time
import os
import concurrent.futures
import shutil
import datetime

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
    devices = []
    for line in lines:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == 'device':
            devices.append(parts[0])
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

def capture_device(device_info, count):
    """对单台设备进行截图并直接流式传输到电脑，确保纯净无轨迹"""
    device_id = device_info["id"]
    folder_name = device_info["folder_name"]
    
    # 获取当前日期和时间
    now = datetime.datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H%M%S")
    
    # 每天建一个单独的文件夹，按日期管理
    date_dir = os.path.join(DATASET_DIR, date_str)
    os.makedirs(date_dir, exist_ok=True)
    
    # 构造文件名：机型_设备号_时间戳_批次号.png
    filename = f"{folder_name}_{time_str}_batch{count}.png"
    local_path = os.path.join(date_dir, filename)
    
    try:
        # 1. 获取系统原有的触摸与指针设置状态
        res_pointer = subprocess.run(["adb", "-s", device_id, "shell", "settings", "get", "system", "pointer_location"], capture_output=True, text=True)
        res_touches = subprocess.run(["adb", "-s", device_id, "shell", "settings", "get", "system", "show_touches"], capture_output=True, text=True)
        
        orig_pointer = "1" if res_pointer.stdout.strip() == "1" else "0"
        orig_touches = "1" if res_touches.stdout.strip() == "1" else "0"
        
        # 2. 如果开启了，为了保证数据集纯净，临时将其关闭
        if orig_pointer == "1":
            subprocess.run(["adb", "-s", device_id, "shell", "settings", "put", "system", "pointer_location", "0"], timeout=2)
        if orig_touches == "1":
            subprocess.run(["adb", "-s", device_id, "shell", "settings", "put", "system", "show_touches", "0"], timeout=2)
            
        # 3. 高速截图：直接通过 exec-out 输出到电脑，不经过手机存储
        with open(local_path, "wb") as f:
            subprocess.run(["adb", "-s", device_id, "exec-out", "screencap", "-p"], stdout=f, check=True, timeout=10)
            
        # 4. 恢复原有状态 (只有原本开启的才需要恢复为1)
        if orig_pointer == "1":
            subprocess.run(["adb", "-s", device_id, "shell", "settings", "put", "system", "pointer_location", "1"], timeout=2)
        if orig_touches == "1":
            subprocess.run(["adb", "-s", device_id, "shell", "settings", "put", "system", "show_touches", "1"], timeout=2)
            
        return f"✅ 设备 {device_id} ({device_info['brand']}_{device_info['model']}) 截图成功 -> {filename}"
    except Exception as e:
        # 截图失败时，清理可能残留的损坏/空文件
        if os.path.exists(local_path):
            try:
                os.remove(local_path)
            except OSError:
                pass
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
        
    print(f"\n检测完成！所有截图将保存在目录: {DATASET_DIR} 按照当天的日期统一归档。\n")
    
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
            futures = [executor.submit(capture_device, info, count) for info in device_info_list]
            
            # 打印每个设备的结果
            for future in concurrent.futures.as_completed(futures):
                print(future.result())
                
        print(f"⏱ 本批次耗时: {time.time() - start_time:.2f} 秒\n")
        count += 1

if __name__ == "__main__":
    main()
