import os
import re

src_dir = 'stock-monitor/src'

for root, dirs, files in os.walk(src_dir):
    for file in files:
        if file.endswith('.tsx') or file.endswith('.ts'):
            filepath = os.path.join(root, file)
            with open(filepath, 'r') as f:
                content = f.read()

            modified = False
            
            # Ensure API_BASE is defined if not present
            if 'fetch(' in content or 'fetch`' in content:
                if 'http://localhost:8001' in content and 'NEXT_PUBLIC_API_BASE' not in content:
                    content = "const API_BASE = process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8001';\n" + content
                    modified = True
                
                content = content.replace('http://localhost:8001', '${API_BASE}')
                
                # Add headers to fetch
                # This regex finds fetch(url) and replaces it with fetch(url, { headers: { 'ngrok-skip-browser-warning': 'true' } })
                # Since fetch might already have an options object, it's safer to just replace all `fetch(xxx)` with `fetch(xxx, { headers: { 'ngrok-skip-browser-warning': 'true' } })`
                # Let's do it manually for the files we found.

            if modified:
                with open(filepath, 'w') as f:
                    f.write(content)

print("Patch applied.")
