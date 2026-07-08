import os
def patch_all():
    files = ["backend/main.py", "backend/real_data_fetcher.py", "backend/ai_analysis.py"]
    for f in files:
        with open(f, 'r', encoding='utf-8') as file:
            content = file.read()
        
        # Check if already patched
        if "os.environ['http_proxy'] = ''" in content:
            continue
            
        patch = "import os\nos.environ['http_proxy'] = ''\nos.environ['https_proxy'] = ''\nos.environ['all_proxy'] = ''\n"
        # Find first import statement
        lines = content.split('\n')
        patched_lines = []
        injected = False
        for line in lines:
            if not injected and line.startswith('import ') or line.startswith('from '):
                patched_lines.append(patch)
                injected = True
            patched_lines.append(line)
            
        with open(f, 'w', encoding='utf-8') as file:
            file.write('\n'.join(patched_lines))

patch_all()
print("Patched proxies")
