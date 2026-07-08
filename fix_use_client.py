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
        lines = f.readlines()
    
    # Remove all "const API_BASE = process.env.NEXT_PUBLIC_API_BASE"
    cleaned_lines = [line for line in lines if "const API_BASE = process.env.NEXT_PUBLIC_API_BASE" not in line]
    
    # Find 'use client'; and insert API_BASE right after it.
    out_lines = []
    inserted = False
    for line in cleaned_lines:
        out_lines.append(line)
        if "'use client';" in line or '"use client";' in line:
            out_lines.append("\nconst API_BASE = process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8001';\n")
            inserted = True
            
    if not inserted:
        # If no 'use client', insert at the top
        out_lines.insert(0, "const API_BASE = process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8001';\n")
        
    with open(fpath, 'w') as f:
        f.writelines(out_lines)

print("Fixed use client issue")
