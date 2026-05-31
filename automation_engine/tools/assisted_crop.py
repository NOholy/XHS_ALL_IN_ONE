"""
Assisted Interactive Template Cropper V3
Allows the user to manually navigate the device to the correct screen,
then captures a template by searching for a specific OCR keyword.

V3 improvements:
  - Added search_icon, close_button, search_button to registry
  - Batch collection mode (--batch)
  - Preview save mode (--preview --save)
  - OCR health check before OCR-dependent commands
  - Keyword-ignored warning for fixed-coordinate templates
  - Removed duplicate timestamp recording
  - Better error diagnostics
  - Resolution caching to avoid redundant screenshots

Features:
  --list       Show all required templates and their status (exists/missing)
  --preview    OCR the current screen and print all detected text
  --keyword    Crop a template by matching a keyword on screen
  --batch      Batch crop missing templates by group (home/post/send/nav/all)
  --auto       Fully automatic template collection pipeline
"""
import os
import sys
import argparse

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from mobile_core.agentless_driver import AgentlessMinitouchDriver
from mobile_core.ocr_client import OCRClient
from mobile_core.logger import get_logger
from tools.auto_crop_templates import (
    crop_and_save_via_ocr, _parse_ocr_results, _auto_detect_serial,
    crop_fixed_region, automated_setup_pipeline, verify_template_cross_check,
)

logger = get_logger("assisted_crop")

TEMPLATE_MAX_AGE_DAYS = 30  # 模板保质期（天）

# V3: 移除了 _save_template_timestamp —— 与 auto_crop_templates._record_crop_timestamp 功能完全重复。
#     crop_and_save_via_ocr / crop_fixed_region 内部已自动记录时间戳，无需外部再次调用。


def check_template_freshness(driver, serial=None):
    """检查所有模板的保质期，返回过期模板列表。可在主引擎启动时调用。"""
    import json
    from datetime import datetime, timedelta
    tpl_dir, resolution = get_template_dir(driver, serial)
    meta_path = os.path.join(tpl_dir, "metadata.json")

    stale = []
    if not os.path.exists(meta_path):
        # 没有元数据文件 → 所有已存在的模板都没有时间戳，全部视为可疑
        for name in TEMPLATE_REGISTRY:
            if os.path.exists(os.path.join(tpl_dir, f"{name}.png")):
                stale.append((name, "无采集记录"))
        return stale

    try:
        with open(meta_path, "r") as f:
            meta = json.load(f)
    except Exception:
        return stale

    now = datetime.now()
    for name in TEMPLATE_REGISTRY:
        path = os.path.join(tpl_dir, f"{name}.png")
        if not os.path.exists(path):
            continue
        info = meta.get(name)
        if not info or "cropped_at" not in info:
            stale.append((name, "无采集记录"))
            continue
        try:
            cropped_at = datetime.fromisoformat(info["cropped_at"])
            age = (now - cropped_at).days
            if age > TEMPLATE_MAX_AGE_DAYS:
                stale.append((name, f"已过期 {age} 天"))
        except Exception:
            stale.append((name, "时间戳格式异常"))

    return stale


# ─────────── Template Registry ───────────
# Maps template_name -> (OCR keyword, description, prerequisite, y_min_ratio_or_fixed_box)
#
# 裁切模式由第 4 个字段的类型决定:
#   float  → OCR 裁切模式: 只匹配屏幕 y 位置 > 此比例的 OCR 结果
#   tuple  → 固定坐标裁切模式: (left_ratio, top_ratio, right_ratio, bottom_ratio)
TEMPLATE_REGISTRY = {
    # ── 首页底部 Tab 栏 (固定坐标裁切，Tab 文字太小 OCR 不可靠) ──
    "tab_home":       ("首页",     "底部导航栏 - 首页",           "在首页即可",                    (0.0, 0.92, 0.2, 1.0)),
    "tab_profile":    ("我",       "底部导航栏 - 我",             "在首页即可",                    (0.8, 0.92, 1.0, 1.0)),
    "tab_message":    ("消息",     "底部导航栏 - 消息",           "在首页即可",                    (0.6, 0.92, 0.8, 1.0)),
    # ── 首页顶部 (固定坐标裁切) ──
    "search_input":   ("搜索",     "首页顶部搜索入口",            "在首页即可",                    (0.8, 0.03, 1.0, 0.1)),
    "search_icon":    ("搜索",     "首页搜索图标(放大镜)",         "在首页即可",                    (0.88, 0.04, 0.98, 0.09)),
    # ── 导航辅助 (固定坐标裁切) ──
    "close_button":   ("×",        "弹窗/页面左上角关闭按钮",      "打开任意弹窗或子页面",           (0.0, 0.01, 0.12, 0.07)),
    # ── 搜索页 (OCR 裁切) ──
    "search_button":  ("搜索",     "搜索页右侧提交按钮",          "点击搜索入口进入搜索页",          0.0),
    # ── 帖子详情页 (OCR 裁切) ──
    "comment_input":  ("说点什么", "帖子底部评论输入框",          "打开一篇有评论区的帖子",         0.8),
    "send_button":    ("发送",     "评论发送按钮",               '点击"说点什么"后输入任意文字，右侧出现"发送"', 0.5),
    "reply_button":   ("回复",     "评论区的回复按钮",            "打开一篇有评论的帖子，向下滑动到评论区", 0.3),
}


# ─────────── Template Groups (for --batch mode) ───────────
TEMPLATE_GROUPS = {
    "home": ["tab_home", "tab_message", "tab_profile", "search_input", "search_icon"],
    "post": ["comment_input", "reply_button"],
    "send": ["send_button"],
    "nav":  ["close_button", "search_button"],
}


# ─────────── Resolution Cache ───────────
_resolution_cache = {}  # serial -> (tpl_dir, resolution)


def get_template_dir(driver, serial):
    """Get the template directory for the current device.
    Caches result to avoid repeated screenshots just for resolution detection.
    """
    serial = serial or driver.serial or _auto_detect_serial()
    if serial in _resolution_cache:
        return _resolution_cache[serial]
    img = driver.clean_screenshot()
    h, w = img.shape[:2]
    resolution = f"{w}x{h}"
    tpl_dir = os.path.join(os.path.dirname(__file__), "..", "data", "ui_templates", serial, resolution)
    _resolution_cache[serial] = (tpl_dir, resolution)
    return tpl_dir, resolution


def _check_ocr_health(ocr):
    """验证 OCR 服务是否可连接（TCP 端口探测）"""
    from urllib.parse import urlparse
    import socket
    try:
        parsed = urlparse(ocr.endpoint)
        host = parsed.hostname or "localhost"
        port = parsed.port or 8001
        sock = socket.create_connection((host, port), timeout=3)
        sock.close()
        return True
    except Exception:
        return False


def cmd_list(driver, serial):
    """List all required templates and their status."""
    tpl_dir, resolution = get_template_dir(driver, serial)

    print(f"\n{'='*70}")
    print(f"  模板状态报告  |  设备: {serial or 'default'}  |  分辨率: {resolution}")
    print(f"{'='*70}\n")

    missing_count = 0
    for name, (keyword, desc, prerequisite, _) in TEMPLATE_REGISTRY.items():
        path = os.path.join(tpl_dir, f"{name}.png")
        exists = os.path.exists(path)
        status = "✅ 已采集" if exists else "❌ 缺失"
        if not exists:
            missing_count += 1
        print(f"  {status}  {name:<18s}  {desc}")
        if not exists:
            print(f"           💡 采集方法: 先{prerequisite}，然后执行:")
            print(f"              python tools/assisted_crop.py --keyword \"{keyword}\" --name \"{name}\"")
        print()

    print(f"{'─'*70}")
    if missing_count == 0:
        print(f"  🎉 所有 {len(TEMPLATE_REGISTRY)} 个模板均已就绪！")
    else:
        print(f"  ⚠️  {missing_count}/{len(TEMPLATE_REGISTRY)} 个模板缺失，请按上方提示逐一采集")
        print(f"  💡 或使用批量模式: python tools/assisted_crop.py --batch home")
    print(f"{'─'*70}\n")


def cmd_preview(driver, ocr, save_path=None):
    """OCR the current screen and print all detected text with coordinates."""
    print("\n📸 正在截取当前屏幕并识别文字...\n")
    img = driver.clean_screenshot()
    h, w = img.shape[:2]

    try:
        results = ocr.ocr_image(img)
    except Exception as e:
        print(f"❌ OCR 服务异常: {e}")
        return

    parsed = _parse_ocr_results(results)
    if not parsed:
        print("⚠️  当前屏幕未识别到任何文字。请确认 OCR 服务正常运行。")
        return

    print(f"  屏幕分辨率: {w}x{h}")
    print(f"  识别到 {len(parsed)} 个文字区域:\n")
    print(f"  {'序号':<6s} {'文字':<20s} {'置信度':<8s} {'中心坐标':<14s} {'可用作关键字'}")
    print(f"  {'─'*70}")

    for i, (box, text, conf) in enumerate(parsed, 1):
        x_center = int(sum(p[0] for p in box) / 4)
        y_center = int(sum(p[1] for p in box) / 4)
        # Check if this text matches any known template keyword
        match_hint = ""
        for name, (keyword, desc, _, _y) in TEMPLATE_REGISTRY.items():
            if keyword in text:
                match_hint = f"→ --keyword \"{keyword}\" --name \"{name}\""
                break
        print(f"  {i:<6d} {text:<20s} {conf:<8.2f} ({x_center:>4d}, {y_center:>4d})  {match_hint}")

    print(f"\n  💡 选择上方文字作为关键字，执行:")
    print(f"     python tools/assisted_crop.py --keyword \"<文字>\" --name \"<模板名>\"\n")

    # V3: 可视化标注保存
    if save_path:
        import cv2
        import numpy as np
        annotated = img.copy()
        for i, (box, text, conf) in enumerate(parsed, 1):
            pts = np.array([[int(p[0]), int(p[1])] for p in box])
            cv2.polylines(annotated, [pts], True, (0, 255, 0), 2)
            cv2.putText(annotated, f"{i}", (int(pts[0][0]), int(pts[0][1]) - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        cv2.imwrite(save_path, annotated)
        print(f"  📁 标注截图已保存: {save_path}")
        try:
            import subprocess
            subprocess.Popen(["open", save_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


def cmd_crop(driver, ocr, keyword, name, serial, exact, force):
    """Crop a template by keyword."""
    # Check if template already exists
    tpl_dir, resolution = get_template_dir(driver, serial)
    existing_path = os.path.join(tpl_dir, f"{name}.png")

    if os.path.exists(existing_path) and not force:
        print(f"\n⚠️  模板 '{name}' 已存在: {existing_path}")
        print(f"   如需覆盖，请添加 --force 参数")
        print(f"   python tools/assisted_crop.py --keyword \"{keyword}\" --name \"{name}\" --force")
        return

    # Look up y_min_ratio from registry if this is a known template
    y_min_ratio = 0.0
    if name in TEMPLATE_REGISTRY:
        y_min_ratio = TEMPLATE_REGISTRY[name][3]

    if isinstance(y_min_ratio, tuple):
        # Fixed coordinate crop — keyword parameter is unused in this mode
        registered_keyword = TEMPLATE_REGISTRY.get(name, ("",))[0]
        if keyword != registered_keyword:
            print(f"\n💡 提示: 模板 '{name}' 使用固定坐标裁切，--keyword \"{keyword}\" 将被忽略，"
                  f"实际裁切区域由注册表定义: {y_min_ratio}")
        logger.info(f"使用固定坐标比例裁剪模板 '{name}' {y_min_ratio}...")
        img = driver.clean_screenshot()
        h, w = img.shape[:2]
        left = int(w * y_min_ratio[0])
        top = int(h * y_min_ratio[1])
        right = int(w * y_min_ratio[2])
        bottom = int(h * y_min_ratio[3])
        box = (left, top, right, bottom)
        success, path = crop_fixed_region(driver, name, serial, box)
    else:
        logger.info(f"正在查找关键字 '{keyword}' 以裁剪模板 '{name}'（y_min_ratio={y_min_ratio}）...")
        success, path = crop_and_save_via_ocr(
            driver, ocr, keyword, name, serial,
            exact_match=exact, y_min_ratio=y_min_ratio
        )

    if success:
        print(f"\n✅ 模板 '{name}' 裁切成功！")
        print(f"   保存路径: {path}")

        # V3: 时间戳已由 crop_and_save_via_ocr / crop_fixed_region 内部记录，
        #     无需再调用 _save_template_timestamp（已删除的冗余函数）。

        # 校验策略：固定坐标裁切的模板只做模板匹配校验
        #           OCR 裁切的模板做完整的三信号交叉校验
        is_fixed_box = isinstance(y_min_ratio, tuple)

        if is_fixed_box:
            print(f"   🔍 正在进行模板匹配校验（固定坐标模板，跳过 OCR 交叉）...")
            from mobile_core.vision import VisionEngine
            vision = VisionEngine(tpl_dir)
            img_verify = driver.clean_screenshot()
            match = vision.find_template(img_verify, name, threshold=0.7)
            if match:
                print(f"   ✅ 模板匹配校验通过！在屏幕 ({match['x']}, {match['y']}) 成功定位，可放心使用。")
            else:
                print(f"   ⚠️  模板匹配校验未通过：VisionEngine 无法在当前屏幕匹配到该模板。")
                print(f"   建议：请确认 Tab 栏可见，然后用 --force 重新采集。")
        else:
            print(f"   🔍 正在进行交叉校验...")
            cross_ok = verify_template_cross_check(
                driver, ocr, name, serial, resolution,
                ocr_text=keyword
            )
            if cross_ok:
                print(f"   ✅ 交叉校验通过！模板匹配位置与 OCR 文字位置吻合，可放心使用。")
            else:
                print(f"   ⚠️  交叉校验未通过：VisionEngine 无法匹配该模板，或与 OCR 位置偏差过大。")
                print(f"   建议：请确认截取的区域是否正确，可用 --preview 查看屏幕内容后重新采集。")

        print(f"   💡 在 Mac 上预览: open \"{path}\"")
        # Try to open for preview on macOS
        try:
            import subprocess
            subprocess.Popen(["open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
    else:
        print(f"\n❌ 未能在当前屏幕找到关键字 '{keyword}'")
        # V3: 差异化诊断建议
        if name in TEMPLATE_REGISTRY:
            _, _, prerequisite, _ = TEMPLATE_REGISTRY[name]
            print(f"   请确认:")
            print(f"   1. 手机屏幕状态正确: {prerequisite}")
            print(f"   2. 手机屏幕上确实显示了 \"{keyword}\" 字样")
            print(f"   3. OCR 服务正在运行 (python start_ocr_server.py)")
        else:
            print(f"   请确认:")
            print(f"   1. 手机屏幕上确实显示了 \"{keyword}\" 字样")
            print(f"   2. OCR 服务正在运行 (python start_ocr_server.py)")
        print(f"   💡 使用 --preview 查看当前屏幕识别到的所有文字:")
        print(f"      python tools/assisted_crop.py --preview")


def cmd_batch(driver, ocr, group_name, serial, force):
    """Batch crop all missing templates in a group."""
    if group_name == "all":
        templates = list(TEMPLATE_REGISTRY.keys())
    elif group_name in TEMPLATE_GROUPS:
        templates = TEMPLATE_GROUPS[group_name]
    else:
        print(f"\n❌ 未知分组 '{group_name}'")
        print(f"   可用分组: {', '.join(TEMPLATE_GROUPS.keys())}, all")
        for gname, members in TEMPLATE_GROUPS.items():
            print(f"   • {gname}: {', '.join(members)}")
        return

    tpl_dir, resolution = get_template_dir(driver, serial)

    # Find missing templates in this group
    missing = []
    for name in templates:
        if name not in TEMPLATE_REGISTRY:
            continue
        if not os.path.exists(os.path.join(tpl_dir, f"{name}.png")) or force:
            missing.append(name)

    if not missing:
        print(f"\n✅ 分组 '{group_name}' 的所有模板均已就绪！")
        return

    # Show prerequisites
    prereqs = set()
    for name in missing:
        prereqs.add(TEMPLATE_REGISTRY[name][2])

    print(f"\n{'='*60}")
    print(f"  批量采集 - 分组: {group_name}")
    print(f"{'='*60}")
    print(f"\n  需要采集 {len(missing)} 个模板:")
    for name in missing:
        keyword, desc, _, _ = TEMPLATE_REGISTRY[name]
        print(f"   • {name:<18s}  {desc}")
    print(f"\n  💡 请确保手机屏幕状态: {' / '.join(prereqs)}")
    print(f"\n  准备好后按回车继续...", end="")
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        print("\n  ⏹ 已取消")
        return

    # Crop each missing template
    success_count = 0
    for i, name in enumerate(missing, 1):
        keyword, desc, _, _ = TEMPLATE_REGISTRY[name]
        print(f"\n{'─'*60}")
        print(f"  [{i}/{len(missing)}] 📸 正在采集: {name} ({desc})")
        print(f"{'─'*60}")
        cmd_crop(driver, ocr, keyword, name, serial, exact=False, force=force)
        if os.path.exists(os.path.join(tpl_dir, f"{name}.png")):
            success_count += 1

    print(f"\n{'='*60}")
    print(f"  批量采集完成: {success_count}/{len(missing)} 成功")
    print(f"{'='*60}")
    cmd_list(driver, serial)


def main():
    parser = argparse.ArgumentParser(
        description="半自动 UI 模板辅助采集工具 V3",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 查看所有模板的缺失状态
  python tools/assisted_crop.py --list

  # 预览当前屏幕上的所有文字（帮助选择关键字）
  python tools/assisted_crop.py --preview

  # 预览并保存标注截图
  python tools/assisted_crop.py --preview --save

  # 采集评论输入框（先在手机上打开一篇帖子）
  python tools/assisted_crop.py --keyword "说点什么" --name "comment_input"

  # 采集发送按钮（先点击评论框并输入文字）
  python tools/assisted_crop.py --keyword "发送" --name "send_button"

  # 批量采集首页相关模板（手机停在首页）
  python tools/assisted_crop.py --batch home

  # 批量采集帖子相关模板（手机打开帖子）
  python tools/assisted_crop.py --batch post

  # 批量采集所有缺失模板
  python tools/assisted_crop.py --batch all

  # 全自动采集所有缺失模板 (无人值守)
  python tools/assisted_crop.py --auto
        """
    )
    parser.add_argument("--serial", type=str, default=None, help="设备序列号")

    # Modes (mutually exclusive)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="列出所有模板及其采集状态")
    group.add_argument("--preview", action="store_true", help="预览当前屏幕 OCR 识别结果")
    group.add_argument("--auto", action="store_true", help="全自动采集所有基础模板")
    group.add_argument("--keyword", type=str, help="按关键字裁剪模板 (如 '说点什么')")
    group.add_argument("--batch", type=str, metavar="GROUP",
                       help=f"批量采集分组模板 (可选: {', '.join(TEMPLATE_GROUPS.keys())}, all)")

    parser.add_argument("--name", type=str, help="模板保存名称 (如 'comment_input'，与 --keyword 搭配使用)")
    parser.add_argument("--exact", action="store_true", help="要求精确匹配关键字")
    parser.add_argument("--force", action="store_true", help="强制覆盖已有模板")
    parser.add_argument("--save", action="store_true", help="与 --preview 搭配，保存标注截图")

    args = parser.parse_args()

    # Validate --keyword requires --name
    if args.keyword and not args.name:
        parser.error("--keyword 必须与 --name 搭配使用")

    # Validate --save only with --preview
    if args.save and not args.preview:
        parser.error("--save 必须与 --preview 搭配使用")

    driver = AgentlessMinitouchDriver(args.serial)
    ocr = OCRClient()
    serial = args.serial or driver.serial or _auto_detect_serial()

    # V3: OCR 健康检查 — 在需要 OCR 的命令前提前探测服务连通性
    if args.preview or args.keyword or args.auto or args.batch:
        if not _check_ocr_health(ocr):
            print("\n❌ OCR 服务不可用！请先启动 OCR 服务:")
            print("   python start_ocr_server.py")
            print(f"\n   OCR 端点: {ocr.endpoint}")
            sys.exit(1)

    if args.list:
        cmd_list(driver, serial)
    elif args.preview:
        save_path = None
        if args.save:
            tpl_dir, _ = get_template_dir(driver, serial)
            os.makedirs(tpl_dir, exist_ok=True)
            save_path = os.path.join(tpl_dir, "_preview_annotated.png")
        cmd_preview(driver, ocr, save_path=save_path)
    elif args.auto:
        print("\n🚀 正在启动全自动模板采集流水线...")
        automated_setup_pipeline(driver, ocr, serial=serial)
        print("\n✅ 流水线执行完毕！下面是最终模板状态报告：\n")
        cmd_list(driver, serial)
    elif args.batch:
        cmd_batch(driver, ocr, args.batch, serial, args.force)
    elif args.keyword:
        cmd_crop(driver, ocr, args.keyword, args.name, serial, args.exact, args.force)


if __name__ == "__main__":
    main()
