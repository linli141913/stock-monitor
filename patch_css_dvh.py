import re

def read_file(filepath):
    with open(filepath, 'r') as f:
        return f.read()

def write_file(filepath, content):
    with open(filepath, 'w') as f:
        f.write(content)

css_path = 'stock-monitor/src/components/stock/StockChartCard.module.css'
css = read_file(css_path)

css = css.replace('100vw', '100dvw')
css = css.replace('100vh', '100dvh')

write_file(css_path, css)
print("CSS dvh patched!")
