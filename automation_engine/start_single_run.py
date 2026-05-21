"""
XHS Single Run Agent - Screenshot-verified automation for Xiaohongshu.
Designed to run via: browser-harness < scripts/xhs_single_run.py

Key design principle: Every action is followed by a screenshot verification step.
"""
import time
import random
import urllib.parse

# ================= CONFIGURATION =================
DRY_RUN = True
SEARCH_KEYWORD = "地陪"
TEMPLATES = [
    "感谢分享，想了解一下更详细的当地游玩安排！",
    "楼主的服务怎么收费呢？",
    "刚好近期有出行计划，求私信联系方式~",
    "想问问有推荐的路线吗？",
    "马克一下，后面去玩可能需要地陪",
]
# =================================================


def verify(description, check_fn, retries=2, delay=2):
    """Run a check function and retry if it fails. Returns the check result."""
    for attempt in range(retries + 1):
        result = check_fn()
        if result:
            print(f"  ✅ Verified: {description}")
            return result
        if attempt < retries:
            print(f"  ⏳ Verification failed: {description} (retry {attempt+1}/{retries})...")
            time.sleep(delay)
    print(f"  ❌ Verification FAILED after {retries+1} attempts: {description}")
    return None


def human_type(text):
    """Simulate typing text character by character."""
    print(f"[*] Typing: {text}")
    for char in text:
        js(f"document.execCommand('insertText', false, '{char}')")
        time.sleep(random.uniform(0.06, 0.15))


def main():
    print("=" * 60)
    print("[*] XHS Single Run Agent - Screenshot Verified")
    print(f"[*] DRY_RUN = {DRY_RUN}")
    print("=" * 60)

    # ---- Step 1: Ensure we have a real browser tab ----
    print("\n[Step 1] Ensuring real browser tab...")
    ensure_real_tab()
    url = js("window.location.href")
    print(f"  Current URL: {url}")
    capture_screenshot()

    # ---- Step 2: Navigate to XHS search results directly via URL ----
    print("\n[Step 2] Navigating to search results for '{}'...".format(SEARCH_KEYWORD))
    encoded_kw = urllib.parse.quote(SEARCH_KEYWORD)
    search_url = f"https://www.xiaohongshu.com/search_result?keyword={encoded_kw}&source=web_search_box_history_page"
    js(f"window.location.href = '{search_url}'")
    wait_for_load()
    time.sleep(4)

    # Verify: URL should contain search_result
    url_ok = verify(
        "URL contains search_result",
        lambda: "search_result" in js("window.location.href")
    )
    if not url_ok:
        print("[!] ABORT: Failed to navigate to search results page.")
        capture_screenshot()
        return

    capture_screenshot()
    print(f"  Page title: {js('document.title')}")

    # ---- Step 3: Verify login status ----
    print("\n[Step 3] Verifying login status...")
    login_count = js("""
        Array.from(document.querySelectorAll('div, span, a, button'))
            .filter(e => e.innerText && e.innerText.trim() === '登录' && e.getBoundingClientRect().width > 0)
            .length
    """)
    if login_count and login_count > 0:
        print("[!] ABORT: Detected login prompt. Please log in manually first!")
        capture_screenshot()
        return
    print("  ✅ Login verified (no login prompt detected)")

    # ---- Step 4: Find posts in search results ----
    print("\n[Step 4] Scanning for posts...")
    posts = js("""
    (function() {
        const sections = document.querySelectorAll('section.note-item');
        const results = [];
        sections.forEach((s, i) => {
            const titleEl = s.querySelector('.title span') || s.querySelector('.title');
            const coverLink = s.querySelector('a.cover');
            if (titleEl && coverLink) {
                const titleRect = titleEl.getBoundingClientRect();
                results.push({
                    index: i,
                    title: titleEl.innerText.trim(),
                    href: coverLink.href,
                    x: titleRect.x + titleRect.width / 2,
                    y: titleRect.y + titleRect.height / 2,
                    inViewport: titleRect.top >= 0 && titleRect.bottom <= window.innerHeight && titleRect.height > 0
                });
            }
        });
        return results.filter(p => p.inViewport);
    })()
    """)

    if not posts:
        print("[!] ABORT: No posts found in search results.")
        capture_screenshot()
        return

    print(f"  Found {len(posts)} visible posts:")
    for p in posts:
        print(f"    [{p['index']}] '{p['title']}' at ({p['x']:.0f}, {p['y']:.0f})")

    # ---- Step 5: Select and click a target post (via title, not cover image) ----
    # Prefer posts with relevant keywords in title
    target = None
    for p in posts:
        if any(kw in p['title'] for kw in ['地陪', '重庆', '找', '旅游']):
            target = p
            break
    if not target:
        target = posts[0]

    print(f"\n[Step 5] Clicking post: '{target['title']}' at ({target['x']:.0f}, {target['y']:.0f})...")
    click_at_xy(target['x'], target['y'])
    time.sleep(4)

    # Verify: URL should now contain /explore/ (post detail page)
    post_opened = verify(
        "Post detail page opened (URL contains /explore/)",
        lambda: "/explore/" in js("window.location.href")
    )
    if not post_opened:
        print("[!] ABORT: Post did not open. Taking screenshot for debug.")
        capture_screenshot()
        return

    capture_screenshot()
    print(f"  Post URL: {js('window.location.href')}")

    # ---- Step 6: Read post content ----
    print("\n[Step 6] Reading post content...")
    content = js("""
    (function() {
        const el = document.querySelector('#detail-desc')
            || document.querySelector('.desc')
            || document.querySelector('.note-text');
        return el ? el.innerText : '';
    })()
    """)
    if content:
        print(f"  Post content (first 100 chars): {content[:100]}...")
    else:
        print("  ⚠️ Could not extract post content (may be image-only post)")

    # ---- Step 7: Locate the comment input ----
    print("\n[Step 7] Locating comment input box...")
    comment_input = js("""
    (function() {
        const candidates = document.querySelectorAll('[contenteditable="true"], textarea');
        for (const el of candidates) {
            const rect = el.getBoundingClientRect();
            const cls = (el.className || '');
            // Filter out the search input and other unrelated editables
            if (rect.width > 50 && rect.height > 0 && rect.top > 0
                && rect.top < window.innerHeight && !cls.includes('search')) {
                return {
                    x: rect.x + rect.width / 2,
                    y: rect.y + rect.height / 2,
                    cls: cls.substring(0, 80)
                };
            }
        }
        return null;
    })()
    """)

    if not comment_input:
        print("  ⚠️ Comment input not found. Skipping comment step.")
    else:
        print(f"  Found comment input (class='{comment_input['cls']}') at ({comment_input['x']:.0f}, {comment_input['y']:.0f})")

        # ---- Step 8: Click and activate comment input ----
        print("\n[Step 8] Clicking comment input box...")
        click_at_xy(comment_input['x'], comment_input['y'])
        time.sleep(2)

        # Verify: the contenteditable element should be focused
        input_focused = verify(
            "Comment input is focused",
            lambda: js("""
                (function() {
                    const active = document.activeElement;
                    return active && active.contentEditable === 'true'
                        && !(active.className || '').includes('search');
                })()
            """)
        )

        if not input_focused:
            print("  ⚠️ Comment input did not focus. Taking screenshot.")
            capture_screenshot()
        else:
            capture_screenshot()

            # ---- Step 9: Type comment (DRY RUN) ----
            comment_text = random.choice(TEMPLATES)
            print(f"\n[Step 9] Typing comment: '{comment_text}'")
            human_type(comment_text)
            time.sleep(1)

            # Verify: text should appear in the input
            typed = js("""
                (function() {
                    const input = document.querySelector('[contenteditable="true"]:not([class*="search"])');
                    return input ? input.innerText.trim() : '';
                })()
            """)
            if typed:
                print(f"  ✅ Text verified in input: '{typed}'")
            else:
                print("  ⚠️ Could not verify typed text")

            capture_screenshot()

            if DRY_RUN:
                print("\n[!] DRY RUN MODE: NOT clicking '发送'. Cancelling...")
                # Click cancel button
                cancel = js("""
                (function() {
                    const btns = document.querySelectorAll('button, div, span');
                    for (const b of btns) {
                        if (b.innerText && b.innerText.trim() === '取消'
                            && b.getBoundingClientRect().width > 0
                            && b.getBoundingClientRect().top > 500) {
                            const rect = b.getBoundingClientRect();
                            return {x: rect.x + rect.width / 2, y: rect.y + rect.height / 2};
                        }
                    }
                    return null;
                })()
                """)
                if cancel:
                    click_at_xy(cancel['x'], cancel['y'])
                    time.sleep(1)
            else:
                print("\n[!] LIVE MODE: Clicking '发送'...")
                send_btn = js("""
                (function() {
                    const btns = document.querySelectorAll('button, div, span');
                    for (const b of btns) {
                        if (b.innerText && b.innerText.trim() === '发送'
                            && b.getBoundingClientRect().width > 0
                            && b.getBoundingClientRect().top > 500) {
                            const rect = b.getBoundingClientRect();
                            return {x: rect.x + rect.width / 2, y: rect.y + rect.height / 2};
                        }
                    }
                    return null;
                })()
                """)
                if send_btn:
                    click_at_xy(send_btn['x'], send_btn['y'])
                    time.sleep(3)
                    capture_screenshot()
                    print("  ✅ Comment sent!")

    # ---- Step 10: Close post and return to feed ----
    print("\n[Step 10] Closing post overlay...")
    # Click the X button at top-left corner
    close_btn = js("""
    (function() {
        const el = document.querySelector('.close-circle')
            || document.querySelector('[class*="close"]');
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
        click_at_xy(30, 30)  # Fallback: X button usually at top-left
    time.sleep(2)

    # Verify: should be back on search results
    back_ok = verify(
        "Returned to search results",
        lambda: "search_result" in js("window.location.href")
    )
    capture_screenshot()

    print("\n" + "=" * 60)
    if back_ok:
        print("[*] ✅ Full workflow completed successfully!")
    else:
        print("[*] ⚠️ Workflow completed but final state may not be on search results.")
    print("=" * 60)


main()
