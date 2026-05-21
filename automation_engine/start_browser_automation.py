import time
import random
import json
import math
import urllib.request
import urllib.error

# ================= CONFIGURATION =================
DRY_RUN = True
COMMENT_MODE = 'llm_generate'
OPENAI_API_KEY = "your-api-key-here"
OPENAI_API_BASE = "https://api.openai.com/v1"

# ---- Keyword Configuration ----
# The primary keyword used for typing in the search box
SEARCH_KEYWORD = "去旅游 求推荐"

# Words that indicate a post might be relevant (used for pre-filtering titles to save time)
PRE_FILTER_KEYWORDS = ["求推荐", "找人带", "有没有", "怎么玩", "路线", "攻略", "旅游", "向导"]
# -------------------------------

LLM_SYSTEM_PROMPT = """你是一个小红书的高级分析师兼回复助手。
你的任务是阅读用户帖子正文或评论，判断这是否是一个需要"旅游地陪/向导"的潜在精准客户。

判定规则：
1. 如果帖子是同行打广告、游记分享、避雷吐槽被坑，请直接输出纯文本：SKIP
2. 如果帖子是游客在求攻略、找本地人带玩、询问路线，说明是精准客户。请生成一句自然、接地气、像真人搭讪的评论，引导对方私信。

不要带有任何AI口吻。如果判断不是客户，只输出SKIP四个英文字母，不要有任何其他字符。"""

TEMPLATES = [
    "感谢分享，想了解一下更详细的当地游玩安排！",
    "楼主的服务怎么收费呢？",
    "刚好近期有出行计划，求私信联系方式~",
    "想问问有推荐的路线吗？",
    "马克一下，后面去玩可能需要地陪"
]
# =================================================

last_mouse_x = 0
last_mouse_y = 0

def cubic_bezier(t, p0, p1, p2, p3):
    """Calculates coordinate for cubic Bezier curve."""
    return (
        (1-t)**3 * p0 + 
        3 * (1-t)**2 * t * p1 + 
        3 * (1-t) * t**2 * p2 + 
        t**3 * p3
    )

def human_move_and_click(target_x, target_y):
    """Simulates a natural human mouse trajectory before clicking."""
    global last_mouse_x, last_mouse_y
    
    start_x = last_mouse_x if last_mouse_x > 0 else random.randint(10, 100)
    start_y = last_mouse_y if last_mouse_y > 0 else random.randint(10, 100)
    
    dist = math.hypot(target_x - start_x, target_y - start_y)
    
    # Introduce natural wobble and arc
    cp1_x = start_x + (target_x - start_x) * random.uniform(0.1, 0.4) + random.uniform(-100, 100)
    cp1_y = start_y + (target_y - start_y) * random.uniform(0.1, 0.4) + random.uniform(-100, 100)
    
    cp2_x = start_x + (target_x - start_x) * random.uniform(0.6, 0.9) + random.uniform(-50, 50)
    cp2_y = start_y + (target_y - start_y) * random.uniform(0.6, 0.9) + random.uniform(-50, 50)
    
    steps = max(10, int(dist / 20))
    steps = min(steps, 60)
    
    print(f"[*] Simulating mouse trajectory over {steps} steps to ({target_x:.0f}, {target_y:.0f})...")
    
    for i in range(1, steps + 1):
        t = i / steps
        # Ease-out behavior
        ease_t = 1 - (1 - t)**3
        
        curr_x = cubic_bezier(ease_t, start_x, cp1_x, cp2_x, target_x)
        curr_y = cubic_bezier(ease_t, start_y, cp1_y, cp2_y, target_y)
        
        cdp("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": curr_x, "y": curr_y})
        time.sleep(random.uniform(0.005, 0.02))
        
    time.sleep(random.uniform(0.1, 0.3))
    
    # Native CDP Click
    cdp("Input.dispatchMouseEvent", {"type": "mousePressed", "button": "left", "clickCount": 1, "x": target_x, "y": target_y})
    time.sleep(random.uniform(0.05, 0.15))
    cdp("Input.dispatchMouseEvent", {"type": "mouseReleased", "button": "left", "clickCount": 1, "x": target_x, "y": target_y})
    
    last_mouse_x = target_x
    last_mouse_y = target_y

def human_sleep(min_sec, max_sec):
    delay = random.uniform(min_sec, max_sec)
    print(f"[*] Sleeping for {delay:.2f} seconds...")
    time.sleep(delay)

def human_type(text):
    print(f"[*] Simulating human CDP typing: {text}")
    for char in text:
        cdp("Input.dispatchKeyEvent", {"type": "keyDown", "text": char})
        time.sleep(random.uniform(0.01, 0.08))
        cdp("Input.dispatchKeyEvent", {"type": "keyUp", "text": char})
        time.sleep(random.uniform(0.08, 0.3))

def get_dynamic_text(content, context_type="post"):
    if COMMENT_MODE == 'template_only' or not OPENAI_API_KEY or OPENAI_API_KEY == "your-api-key-here":
        text = random.choice(TEMPLATES)
        print(f"[*] Using template (LLM disabled/unconfigured): {text}")
        return text
    
    print(f"[*] Calling LLM API to generate dynamic {context_type} text...")
    prompt = f"上下文类型: {'帖子正文' if context_type == 'post' else '针对别人的评论进行回复'}\n"
    prompt += f"正文/评论内容: {content}\n"
    prompt += f"参考模版列表: {json.dumps(TEMPLATES, ensure_ascii=False)}\n"
    prompt += "请直接给出你要发送的一句评论文本："
    
    req_body = json.dumps({
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": LLM_SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7
    }).encode('utf-8')
    
    req = urllib.request.Request(f"{OPENAI_API_BASE.rstrip('/')}/chat/completions", data=req_body)
    req.add_header('Content-Type', 'application/json')
    req.add_header('Authorization', f'Bearer {OPENAI_API_KEY}')
    
    try:
        response = urllib.request.urlopen(req, timeout=15)
        res_data = json.loads(response.read().decode('utf-8'))
        generated_text = res_data['choices'][0]['message']['content'].strip().strip('"').strip("'")
        print(f"[*] LLM Generated text: {generated_text}")
        return generated_text
    except Exception as e:
        print(f"[-] LLM API call failed: {e}. Falling back to random template.")
        return random.choice(TEMPLATES)

def submit_comment(text):
    if DRY_RUN:
        print(f"[!] DRY-RUN MODE: Typing comment '{text}' but NOT submitting.")
        human_type(text)
        human_sleep(2, 3)
    else:
        print(f"[!] LIVE MODE: Typing and submitting comment '{text}'.")
        human_type(text)
        human_sleep(1, 2)
        
        send_btn_rect = js("""
          (function(){
            const btn = Array.from(document.querySelectorAll('button, div')).find(e => e.innerText && e.innerText.includes('发送'));
            if(!btn) return null;
            const r = btn.getBoundingClientRect();
            return {x: r.x + r.width/2, y: r.y + r.height/2};
          })();
        """)
        
        if send_btn_rect:
            human_move_and_click(send_btn_rect['x'], send_btn_rect['y'])
        else:
            print("[-] Could not find send button!")

def go_back_to_feed():
    print("[*] Closing post overlay to return to feed...")
    close_btn_rect = js("""
      (function() {
          const btn = document.querySelector('.close-box') || document.querySelector('.back-icon');
          if(!btn) return null;
          const r = btn.getBoundingClientRect();
          return {x: r.x + r.width/2, y: r.y + r.height/2};
      })();
    """)
    
    if close_btn_rect:
        human_move_and_click(close_btn_rect['x'], close_btn_rect['y'])
    else:
        cdp("Input.dispatchKeyEvent", {"type": "keyDown", "windowsVirtualKeyCode": 27, "key": "Escape"})
        cdp("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Escape"})
    human_sleep(3, 5)

def main():
    print("[*] Starting Xiaohongshu Automation Script...")
    ensure_real_tab()
    url = js("window.location.href")

    if "xiaohongshu.com" not in url:
        print("[*] Navigating to Xiaohongshu explore page...")
        new_tab("https://www.xiaohongshu.com/explore")
        wait_for_load()
        human_sleep(2, 4)

    # Verify Login
    login_elements = js("""
      Array.from(document.querySelectorAll('div, span, a, button'))
        .filter(e => e.innerText && e.innerText.trim() === '登录' && e.getBoundingClientRect().width > 0)
        .length
    """)
    if login_elements > 0:
        print("[!] Detected login prompt. Please log in manually in the browser first!")
        return

    print("[*] Logged in successfully. Starting feed browsing...")

    # Search for "地陪"
    search_box_info = js("""
      (function() {
          const inputs = Array.from(document.querySelectorAll('input'));
          const searchBox = inputs.find(e => e.placeholder && e.placeholder.includes('搜索')) || inputs[0];
          if (searchBox) {
              const rect = searchBox.getBoundingClientRect();
              return {x: rect.x + rect.width/2, y: rect.y + rect.height/2};
          }
          return null;
      })()
    """)

    if search_box_info:
        print(f"[*] Found search box. Clicking and typing '{SEARCH_KEYWORD}'...")
        human_move_and_click(search_box_info['x'], search_box_info['y'])
        human_sleep(1, 2)
        human_type(SEARCH_KEYWORD)
        human_sleep(0.5, 1.5)
        cdp("Input.dispatchKeyEvent", {"type": "keyDown", "windowsVirtualKeyCode": 13, "key": "Enter", "text": "\r"})
        cdp("Input.dispatchKeyEvent", {"type": "keyUp", "windowsVirtualKeyCode": 13, "key": "Enter"})
        wait_for_load()
        human_sleep(3, 5)
    else:
        print("[-] Could not find search box. Perhaps already on a search page?")

    scroll_attempts = 0
    while True:
        try:
            print("[*] Scanning feed for posts...")
            human_sleep(2, 4)

            # Optimization 2 & 4: Strict viewport check, no slice, Set for urls
            posts_info = js("""
              (function() {
                  if (!window.__processed_urls) window.__processed_urls = new Set();
                  const links = Array.from(document.querySelectorAll('a[href^="/explore/"]'));
                  return links.map(a => {
                      const rect = a.getBoundingClientRect();
                      return {
                         href: a.href,
                         title: (a.innerText || '').trim().split('\\n')[0] || '',
                         x: rect.x + rect.width/2, 
                         y: rect.y + rect.height/2,
                         in_viewport: rect.top >= 0 && rect.bottom <= window.innerHeight && rect.height > 0
                      };
                  }).filter(p => p.in_viewport && !window.__processed_urls.has(p.href));
              })()
            """)

            if not posts_info:
                scroll_attempts += 1
                print(f"[-] No new posts found in current viewport (Attempt {scroll_attempts}/5). Scrolling...")
                
                # Optimization 5: Infinite loop defense
                if scroll_attempts >= 5:
                    print("[!] Feed exhausted or stuck. Refreshing page...")
                    js("window.location.reload();")
                    human_sleep(5, 8)
                    scroll_attempts = 0
                    continue
                    
                js("window.scrollBy({top: window.innerHeight * 0.8, behavior: 'smooth'});")
                human_sleep(3, 6)
                continue
                
            scroll_attempts = 0
            print(f"[*] Found {len(posts_info)} visible unprocessed posts.")

            for post in posts_info:
                # Attempt to process post with exception handling
                try:
                    js(f"if(!window.__processed_urls) window.__processed_urls = new Set(); window.__processed_urls.add('{post['href']}');")
                    
                    # Optimization 1: Pre-filtering & Random Skip
                    # Skip posts completely irrelevant in title, or randomly skip to seem human
                    title = post['title']
                    is_relevant = any(kw in title for kw in PRE_FILTER_KEYWORDS)
                    
                    if not is_relevant and random.random() < 0.7:
                        print(f"[*] Skipping post (Irrelevant title & Random skip): {title}")
                        continue
                        
                    print(f"\n[*] Processing Post: {title}...")
                    human_move_and_click(post['x'], post['y'])
                    wait_for_load()
                    
                    # State Assertion: Check if post actually opened
                    human_sleep(2, 4)
                    post_opened = js("(function(){ return document.querySelector('.close-box') !== null || document.querySelector('.back-icon') !== null; })()")
                    if not post_opened:
                        print("[-] Post overlay did not open correctly (possibly blocked or delayed). Skipping.")
                        continue

                    content = js("""
                      (function() {
                          const desc = document.querySelector('.desc') || document.querySelector('#detail-desc') || document.querySelector('.note-text');
                          return desc ? desc.innerText : '';
                      })()
                    """)
                    
                    # Optimization 3: Dynamic reading time based on content length
                    content_length = len(content) if content else 0
                    # Assuming average reading speed of 300 chars/minute -> 5 chars/sec
                    base_read_time = min(max(content_length / 5.0, 3.0), 30.0) # between 3s and 30s
                    
                    print(f"[*] Simulating reading time for {content_length} chars (~{base_read_time:.1f}s)...")
                    human_sleep(base_read_time, base_read_time + random.uniform(2, 5))
                    
                    print(f"[*] Analyzing post intent via LLM...")
                    main_reply_text = get_dynamic_text(content, context_type="post")
                    
                    if "SKIP" not in main_reply_text.upper():
                        print(f"[*] LLM determined post is a valid lead. Preparing to comment...")
                        
                        comment_box = js("""
                          (function() {
                              const box = document.querySelector('.comment-input') || document.querySelector('[placeholder*="说点什么"]');
                              if (box) {
                                  const rect = box.getBoundingClientRect();
                                  return {x: rect.x + rect.width/2, y: rect.y + rect.height/2};
                              }
                              return null;
                          })()
                        """)
                        
                        if comment_box:
                            human_move_and_click(comment_box['x'], comment_box['y'])
                            human_sleep(1, 2)
                            submit_comment(main_reply_text)
                        
                        print("[*] Scrolling to read comments...")
                        for _ in range(3):
                            js("""
                              const container = document.querySelector('.note-scroller') || window;
                              container.scrollBy({top: window.innerHeight * 0.5, behavior: 'smooth'});
                            """)
                            human_sleep(2, 4)
                            
                        target_comment = js("""
                          (function() {
                              const comments = Array.from(document.querySelectorAll('.comment-item'));
                              for(let c of comments) {
                                  const contentEl = c.querySelector('.content');
                                  if(!contentEl) continue;
                                  const rect = contentEl.getBoundingClientRect();
                                  if(rect.width > 0 && rect.height > 0) {
                                      return {
                                          text: contentEl.innerText.trim(),
                                          x: rect.x + rect.width/2,
                                          y: rect.y + rect.height/2
                                      };
                                  }
                              }
                              return null;
                          })()
                        """)
                        
                        if target_comment:
                            print(f"[*] Found top comment: {target_comment['text']}")
                            human_move_and_click(target_comment['x'], target_comment['y'])
                            human_sleep(1, 2)
                            
                            nested_box_active = js("""
                              (function() {
                                  const box = document.querySelector('.comment-input') || document.activeElement;
                                  return box && (box.placeholder && box.placeholder.includes('回复'));
                              })()
                            """)
                            if nested_box_active:
                                nested_reply_text = get_dynamic_text(target_comment['text'], context_type="comment")
                                if "SKIP" not in nested_reply_text.upper():
                                    submit_comment(nested_reply_text)
                                else:
                                    print("[-] LLM determined comment is irrelevant. Skipping nested reply.")
                    else:
                        print("[-] LLM determined post is irrelevant or a complaint (SKIP). Skipping commenting.")
                    
                except Exception as e:
                    print(f"[!] Error processing post {post['title']}: {e}")
                finally:
                    # Always try to close the overlay to maintain state
                    go_back_to_feed()
                    
        except Exception as e:
            print(f"[!] Critical error in main feed loop: {e}. Recovering in 5s...")
            time.sleep(5)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Script terminated by user (Ctrl+C). Gracefully exiting...")
