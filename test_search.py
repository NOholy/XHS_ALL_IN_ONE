import sys
import json
import logging
from xhs_cli.client import XhsClient
from xhs_cli.browser import BrowserEngine

logging.basicConfig(level=logging.INFO)

def main():
    keyword = "测试"
    print(f"Searching for '{keyword}'...")
    
    engine = BrowserEngine(headless=True)
    client = XhsClient(cookie_dict={})
    client.start()
    
    try:
        results = client.search_notes(keyword)
        
        print(f"\nFound {len(results)} results.")
        if results:
            print("Structure of the first result:")
            print(json.dumps(results[0], indent=2, ensure_ascii=False))
            
            with open("/tmp/xhs_search_results.json", "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
            print("Saved full results to /tmp/xhs_search_results.json")
    finally:
        client.close()

if __name__ == "__main__":
    main()
