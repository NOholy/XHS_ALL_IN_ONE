import paddlex
try:
    from paddlex.repo_apis import OcrRec_Dict
    print(list(OcrRec_Dict.keys()))
except:
    pass
try:
    print(paddlex.configs.list_models())
except:
    pass
import os
for root, dirs, files in os.walk('/Users/qi/ai-code-project/XHS_ALL_IN_ONE/venv/lib/python3.9/site-packages'):
    for file in files:
        if file.endswith('.yaml') and 'ocr' in file.lower():
            print(os.path.join(root, file))
