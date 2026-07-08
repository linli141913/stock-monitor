import os
import re

src_dir = 'stock-monitor/src'

for root, dirs, files in os.walk(src_dir):
    for file in files:
        if file.endswith('.tsx') or file.endswith('.ts'):
            filepath = os.path.join(root, file)
            with open(filepath, 'r') as f:
                content = f.read()

            # Add ngrok headers to all fetch calls that don't already have options
            # Example: fetch(`${API_BASE}/api/...`) -> fetch(`${API_BASE}/api/...`, { headers: { 'ngrok-skip-browser-warning': 'true' } })
            
            new_content = re.sub(r'(fetch\([^,)]+)\)', r"\1, { headers: { 'ngrok-skip-browser-warning': 'true' } })", content)

            if new_content != content:
                with open(filepath, 'w') as f:
                    f.write(new_content)

print("Patch 2 applied.")
