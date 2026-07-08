import os

def overwrite_file(filepath, content):
    with open(filepath, 'w') as f:
        f.write(content)

def read_file(filepath):
    with open(filepath, 'r') as f:
        return f.read()

css_path = 'stock-monitor/src/components/stock/StockChartCard.module.css'
css = read_file(css_path)

css = css.replace('100dvw', '100vw')
css = css.replace('100dvh', '100vh')

overwrite_file(css_path, css)
print("CSS patched back to vh")
