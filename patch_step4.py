import os
def overwrite_file(filepath, content):
    with open(filepath, 'w') as f:
        f.write(content)

def read_file(filepath):
    with open(filepath, 'r') as f:
        return f.read()

css_path = 'stock-monitor/src/components/stock/StockChartCard.module.css'
scc_css = read_file(css_path)
if '.simulatedFullscreen .chartContainer' not in scc_css:
    scc_css += """
@media (max-width: 768px) {
  .simulatedFullscreen .chartContainer {
    height: calc(100% - 60px) !important;
  }
}
"""
    overwrite_file(css_path, scc_css)

