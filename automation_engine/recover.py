import json
import re

out_lines = []
found = False

with open('/Users/qi/.gemini/antigravity/brain/2e99fd9b-8395-4126-a239-cc28333313ab/.system_generated/logs/transcript.jsonl', 'r') as f:
    for line in f:
        try:
            data = json.loads(line)
        except:
            continue
        if data.get('type') == 'RUN_COMMAND' or data.get('type') == 'TOOL_RESPONSE' or 'content' in data:
            content = data.get('content', '')
            # We look for a git diff output from earlier!
            if "diff --git a/tools/auto_crop_templates.py b/tools/auto_crop_templates.py" in content:
                print("Found git diff in step", data.get('step_index'))
                with open('recovered_diff.patch', 'w') as outf:
                    outf.write(content)
        
        if data.get('type') == 'PLANNER_RESPONSE':
            # We can also check if we used write_to_file or multi_replace
            pass

