import os
import glob
import json
import urllib.request
import urllib.error
import time

PAT = os.environ.get("GITHUB_TOKEN", "YOUR_GITHUB_TOKEN")
REPO = "NOholy/XHS_ALL_IN_ONE"
TAG = "models-1780916378"

print(f"Fetching release {TAG}...")
url = f"https://api.github.com/repos/{REPO}/releases/tags/{TAG}"
req = urllib.request.Request(url, headers={"Authorization": f"token {PAT}", "Accept": "application/vnd.github.v3+json"})
try:
    with urllib.request.urlopen(req) as response:
        release = json.loads(response.read())
except urllib.error.HTTPError as e:
    print(f"Failed to fetch release: {e.read().decode('utf-8')}")
    exit(1)

upload_url = release['upload_url'].split('{')[0]
existing_assets = [asset['name'] for asset in release.get('assets', [])]

print(f"Already uploaded: {existing_assets}")

files = glob.glob('automation_engine/models/**/*.pt', recursive=True) + glob.glob('automation_engine/models/**/*.onnx', recursive=True)

for file in files:
    parent_dir = os.path.basename(os.path.dirname(file))
    filename = f"{parent_dir}_{os.path.basename(file)}"
    
    if filename in existing_assets:
        print(f"Skipping {filename}, already uploaded.")
        continue
        
    print(f"Uploading {filename} from {file}...")
    
    with open(file, 'rb') as f:
        data = f.read()
    
    req = urllib.request.Request(f"{upload_url}?name={filename}", data=data, headers={
        "Authorization": f"token {PAT}",
        "Content-Type": "application/octet-stream"
    })
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            urllib.request.urlopen(req, timeout=300)
            print(f"Success: {filename}")
            break
        except Exception as e:
            print(f"Attempt {attempt+1} failed to upload {filename}: {e}")
            if attempt < max_retries - 1:
                time.sleep(5)
            else:
                print(f"Giving up on {filename}.")
