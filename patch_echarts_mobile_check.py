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

# Replace all `window.innerWidth <= 768` with user agent check
tsx = tsx.replace('window.innerWidth <= 768', '/Mobi|Android|iPhone/i.test(navigator.userAgent)')

overwrite_file(tsx_path, tsx)
print("Mobile check patched")
