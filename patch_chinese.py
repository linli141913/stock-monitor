import os

def overwrite_file(filepath, content):
    with open(filepath, 'w') as f:
        f.write(content)

def read_file(filepath):
    with open(filepath, 'r') as f:
        return f.read()

tsx_path = 'stock-monitor/src/components/stock/StockChartCard.tsx'
tsx = read_file(tsx_path)

# Replace the dataset definition
old_dataset = """      dataset: {
        source: data.map(d => ["""
new_dataset = """      dataset: {
        dimensions: ['date', '开盘', '收盘', '最低', '最高', '成交量'],
        source: data.map(d => ["""

tsx = tsx.replace(old_dataset, new_dataset)
overwrite_file(tsx_path, tsx)
print("Chinese patched")
