"""
XHS AI Driver - The perception and action adapter for Antigravity AI.
This script is designed to be executed via `browser-harness < scripts/xhs_ai_driver.py -- [args]`
It provides clean, standardized operations so the AI doesn't have to write raw JS strings.
"""
import time
import random
import argparse
import json
import sys

def parse_args():
    parser = argparse.ArgumentParser(description="XHS AI Automation Driver")
    parser.add_argument("--action", required=True, choices=["scan", "extract", "reply"], help="The action to perform")
    parser.add_argument("--x", type=float, help="X coordinate for clicking (required for extract and reply)")
    parser.add_argument("--y", type=float, help="Y coordinate for clicking (required for extract and reply)")
    parser.add_argument("--text", type=str, help="Text to type (required for reply)")
    parser.add_argument("--live", action="store_true", help="If set, actually clicks the send button. Otherwise, clicks cancel.")
    parser.add_argument("--close", action="store_true", help="If set, closes the post overlay after replying. Defaults to False to let user inspect.")
    
    # We must filter out any args before '--' if run via harness
    if '--' in sys.argv:
        args_list = sys.argv[sys.argv.index('--') + 1:]
    else:
        args_list = sys.argv[1:]
        
    return parser.parse_args(args_list)

def human_type(text):
    for char in text:
        js(f"document.execCommand('insertText', false, '{char}')")
        time.sleep(random.uniform(0.06, 0.15))

def action_scan():
    print("[*] Driver: Scanning for posts...")
    # Simulate human scroll to load more
    js("window.scrollBy({top: 300, behavior: 'smooth'})")
    time.sleep(1.5)
    js("window.scrollBy({top: 200, behavior: 'smooth'})")
    time.sleep(2)

    posts = js("""
    (function() {
        const sections = document.querySelectorAll('section.note-item');
        const results = [];
        sections.forEach((s, i) => {
            const titleEl = s.querySelector('.title span') || s.querySelector('.title');
            const coverLink = s.querySelector('a.cover');
            const authorEl = s.querySelector('.author .name');
            if (titleEl && coverLink) {
                const titleRect = titleEl.getBoundingClientRect();
                results.push({
                    id: i,
                    title: titleEl.innerText.trim(),
                    author: authorEl ? authorEl.innerText.trim() : 'Unknown',
                    x: titleRect.x + titleRect.width / 2,
                    y: titleRect.y + titleRect.height / 2,
                    inViewport: titleRect.top >= 0 && titleRect.bottom <= window.innerHeight && titleRect.height > 0
                });
            }
        });
        return results.filter(p => p.inViewport);
    })()
    """)
    
    print("\n--- VISIBLE POSTS (JSON) ---")
    print(json.dumps(posts, ensure_ascii=False, indent=2))
    print("----------------------------\n")
    capture_screenshot()
    print("[*] Scan complete.")

def action_extract(x, y):
    print(f"[*] Driver: Clicking target at ({x}, {y}) to extract content...")
    click_at_xy(x, y)
    
    # 显式状态校验：轮询等待帖子弹窗加载
    max_attempts = 15
    loaded = False
    for attempt in range(max_attempts):
        time.sleep(0.5)
        is_ready = js("""
        (function() {
            const desc = document.querySelector('#detail-desc') || document.querySelector('.desc') || document.querySelector('.note-text');
            const closeBtn = document.querySelector('.close-circle') || document.querySelector('[class*="close"]');
            return (desc !== null || closeBtn !== null);
        })()
        """)
        if is_ready:
            loaded = True
            print(f"[*] Driver: Post loaded successfully after {(attempt + 1) * 0.5}s.")
            break
            
    if not loaded:
        print("[-] Driver Error: Post modal failed to load within timeout (Network issue or hit an ad/video?).")
        capture_screenshot()
        sys.exit(1)
        
    time.sleep(1) # Extra buffer for layout stability
    capture_screenshot()

    # Extract Post Description
    desc = js("""
    (function() {
        const el = document.querySelector('#detail-desc') || document.querySelector('.desc') || document.querySelector('.note-text');
        return el ? el.innerText.trim() : '';
    })()
    """)

    # 平滑滚动以加载隐藏的评论
    print("[*] Driver: Scrolling post to load more comments...")
    for _ in range(2):
        js("""
        (function() {
            const scroller = document.querySelector('.note-scroller') || document.querySelector('.interaction-container') || window;
            if (scroller && scroller.scrollBy) {
                scroller.scrollBy({top: window.innerHeight * 0.8, behavior: 'smooth'});
            }
        })()
        """)
        time.sleep(1.5)
        
    capture_screenshot()

    # Extract Comments
    comments = js("""
    (function() {
        const commentItems = document.querySelectorAll('.comment-item, [class*="commentItem"]');
        const results = [];
        commentItems.forEach((item, index) => {
            const authorEl = item.querySelector('.name, [class*="name"]');
            const contentEl = item.querySelector('.content, [class*="content"]');
            
            const spans = item.querySelectorAll('span');
            let replyBtn = null;
            for (const span of spans) {
                if (span.innerText && span.innerText.trim() === '回复') {
                    replyBtn = span; break;
                }
            }
            
            if (authorEl && contentEl && replyBtn) {
                const rect = replyBtn.getBoundingClientRect();
                if (rect.top > 0 && rect.top < window.innerHeight && rect.height > 0) {
                    results.push({
                        id: index,
                        author: authorEl.innerText.trim(),
                        content: contentEl.innerText.trim(),
                        reply_x: rect.x + rect.width / 2,
                        reply_y: rect.y + rect.height / 2
                    });
                }
            }
        });
        return results;
    })()
    """)

    print("\n--- POST DESCRIPTION ---")
    print(desc)
    print("------------------------\n")
    
    print("\n--- COMMENTS (JSON) ---")
    print(json.dumps(comments, ensure_ascii=False, indent=2))
    print("-----------------------\n")
    print("[*] Extract complete.")

def action_reply(x, y, text, live_mode, should_close):
    print(f"[*] Driver: Replying at ({x}, {y})")
    click_at_xy(x, y)
    
    # 显式状态校验：轮询等待输入框获得焦点
    max_attempts = 10
    is_focused = False
    for attempt in range(max_attempts):
        time.sleep(0.5)
        is_focused = js("return document.activeElement && document.activeElement.contentEditable === 'true';")
        if is_focused:
            print(f"[*] Driver: Input box focused after {(attempt + 1) * 0.5}s.")
            break

    if not is_focused:
        print("[-] Warning: Input box not focused. Attempting fallback click...")
        input_box = js("""
        (function() {
            const inputs = document.querySelectorAll('[contenteditable="true"]');
            for(let i=0; i<inputs.length; i++) {
                if(inputs[i].getBoundingClientRect().width > 50 && !inputs[i].className.includes('search')) {
                    return {x: inputs[i].getBoundingClientRect().x + 50, y: inputs[i].getBoundingClientRect().y + 10};
                }
            }
            return null;
        })()
        """)
        if input_box:
            click_at_xy(input_box['x'], input_box['y'])
            time.sleep(1)
        else:
            print("[-] FATAL: Failed to focus input box and no fallback input box found. Aborting.")
            capture_screenshot()
            sys.exit(1)

    print(f"[*] Driver: Typing text: {text}")
    human_type(text)
    time.sleep(2)
    capture_screenshot()

    if live_mode:
        print("[!] LIVE MODE: Clicking '发送'...")
        send_btn = js("""
        (function() {
            const btns = document.querySelectorAll('button, div, span');
            for (const b of btns) {
                if (b.innerText && b.innerText.trim() === '发送' && b.getBoundingClientRect().top > 500) {
                    const rect = b.getBoundingClientRect();
                    return {x: rect.x + rect.width/2, y: rect.y + rect.height/2};
                }
            }
            return null;
        })()
        """)
        if send_btn:
            click_at_xy(send_btn['x'], send_btn['y'])
            print("[*] Driver: Waiting 3s for network response...")
            time.sleep(3)
            
            # 执行发送结果校验
            import json
            safe_text = json.dumps(text)
            verify_result = js(f"""
            (function() {{
                const pageText = document.body.innerText;
                // 1. 检查是否触发防风控验证码
                if (pageText.includes('验证码') || pageText.includes('安全验证') || pageText.includes('向右滑动')) {{
                    return 'CAPTCHA';
                }}
                
                // 2. 检查评论是否成功挂载到 DOM
                const comments = document.querySelectorAll('.content, [class*="content"]');
                for (let i=0; i<comments.length; i++) {{
                    if (comments[i].innerText && comments[i].innerText.includes({safe_text})) {{
                        return 'SUCCESS';
                    }}
                }}
                
                // 3. 检查输入框是否依然残留着文本（点击发送没反应）
                const active = document.activeElement;
                if (active && active.contentEditable === 'true' && active.innerText.includes({safe_text})) {{
                    return 'DRAFT_STILL_EXISTS';
                }}
                
                return 'UNKNOWN';
            }})()
            """)
            
            print(f"[*] Driver Verification Result: {verify_result}")
            capture_screenshot()
            
            if verify_result == 'CAPTCHA':
                print("[-] CRITICAL: Captcha or Security Check detected! Stopping automation.")
                sys.exit(1)
            elif verify_result == 'SUCCESS':
                print("[+] Comment verified as successfully posted in DOM.")
            else:
                print("[-] Warning: Comment not found in DOM or still in draft. It may be shadowbanned or network failed.")
        else:
            print("[-] FATAL: Could not find '发送' (Send) button. Aborting.")
            capture_screenshot()
            sys.exit(1)
    else:
        print("[!] DRY RUN: Cancelling comment...")
        cancel_btn = js("""
        (function() {
            const btns = document.querySelectorAll('button, div, span');
            for (const b of btns) {
                if (b.innerText && b.innerText.trim() === '取消' && b.getBoundingClientRect().top > 500) {
                    const rect = b.getBoundingClientRect();
                    return {x: rect.x + rect.width/2, y: rect.y + rect.height/2};
                }
            }
            return null;
        })()
        """)
        if cancel_btn:
            click_at_xy(cancel_btn['x'], cancel_btn['y'])
            time.sleep(1)
        else:
            print("[-] Warning: Could not find '取消' (Cancel) button during DRY RUN.")

    if should_close:
        print("[*] Driver: Closing overlay...")
        close_btn = js("""
        (function() {
            const el = document.querySelector('.close-circle') || document.querySelector('[class*="close"]');
            if (el) {
                const rect = el.getBoundingClientRect();
                if (rect.width > 0) return {x: rect.x + rect.width/2, y: rect.y + rect.height/2};
            }
            return null;
        })()
        """)
        if close_btn:
            click_at_xy(close_btn['x'], close_btn['y'])
        else:
            click_at_xy(30, 30)
        time.sleep(2)
        capture_screenshot()
    else:
        print("[*] Driver: Leaving overlay open for user inspection.")
        
    print("[*] Reply complete.")

def main():
    args = parse_args()
    print("=========================================")
    print(f"XHS AI Driver executing action: {args.action}")
    print("=========================================")
    
    if args.action == "scan":
        action_scan()
    elif args.action == "extract":
        if args.x is None or args.y is None:
            print("Error: --x and --y required for extract")
            return
        action_extract(args.x, args.y)
    elif args.action == "reply":
        if args.x is None or args.y is None or not args.text:
            print("Error: --x, --y, and --text required for reply")
            return
        action_reply(args.x, args.y, args.text, args.live, args.close)

if __name__ == "__main__":
    main()
