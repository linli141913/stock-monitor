import os
import re

def overwrite_file(filepath, content):
    with open(filepath, 'w') as f:
        f.write(content)

def read_file(filepath):
    with open(filepath, 'r') as f:
        return f.read()

tsx_path = 'stock-monitor/src/components/stock/StockChartCard.tsx'
tsx = read_file(tsx_path)

# Remove the custom ZRender logic
pattern = r'  // Custom mobile touch logic[\s\S]*?clearTimeout\(timerRef\.current\);\s*\};\s*\}, \[data, isMobile\]\);\n'
tsx = re.sub(pattern, '', tsx)

overwrite_file(tsx_path, tsx)
print("ZR removed")
