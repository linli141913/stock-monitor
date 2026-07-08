import os
def overwrite_file(filepath, content):
    with open(filepath, 'w') as f:
        f.write(content)

def read_file(filepath):
    with open(filepath, 'r') as f:
        return f.read()

# CSS
css_path = 'stock-monitor/src/components/stock/RelatedStocksCard.module.css'
rsc_css = read_file(css_path)

if '.content' not in rsc_css:
    rsc_css += """
.content {
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  margin: 0 -16px;
  padding: 0 16px;
}

.table th, .table td {
  white-space: nowrap;
}
"""
    overwrite_file(css_path, rsc_css)

