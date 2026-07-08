import os

files_to_fix = [
    'stock-monitor/src/app/industry/page.tsx',
    'stock-monitor/src/components/stock/AiAttributionTab.tsx',
    'stock-monitor/src/components/stock/FinancialSummaryTab.tsx',
    'stock-monitor/src/app/page.tsx',
    'stock-monitor/src/app/watchlist/page.tsx'
]

for fpath in files_to_fix:
    with open(fpath, 'r') as f:
        content = f.read()
    
    new_content = content.replace("process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8001'", "process.env.NEXT_PUBLIC_API_BASE || 'https://banister-drilling-jawless.ngrok-free.dev'")
    
    if new_content != content:
        with open(fpath, 'w') as f:
            f.write(new_content)

print("Replaced API Base")
