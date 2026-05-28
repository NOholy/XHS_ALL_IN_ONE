import json

lines = {}

with open('/Users/qi/.gemini/antigravity/brain/2e99fd9b-8395-4126-a239-cc28333313ab/.system_generated/logs/transcript.jsonl', 'r') as f:
    for line in f:
        try:
            data = json.loads(line)
        except:
            continue
        if data.get('type') == 'VIEW_FILE':
            content = data.get('content', '')
            if 'Showing lines' in content and 'tools/auto_crop_templates.py' in content:
                for l in content.split('\n'):
                    if ':' in l:
                        parts = l.split(':', 1)
                        if parts[0].isdigit():
                            lineno = int(parts[0])
                            code = parts[1][1:] # remove leading space
                            if lineno not in lines:
                                lines[lineno] = code

with open('recovered.py', 'w') as outf:
    if lines:
        for i in range(1, max(lines.keys()) + 1):
            outf.write(lines.get(i, f'# MISSING LINE {i}\n'))

print(f"Recovered {len(lines)} lines up to {max(lines.keys()) if lines else 0}")
