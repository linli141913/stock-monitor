import os

def append_to_file(filepath, content):
    with open(filepath, 'a') as f:
        f.write("\n" + content + "\n")

if os.path.exists('stock-monitor/src/components/stock/StockChartCard.module.css'):
    append_to_file('stock-monitor/src/components/stock/StockChartCard.module.css', """
@media (max-width: 768px) {
  .header {
    flex-direction: column;
    align-items: flex-start;
    gap: var(--space-md);
  }
  .controls {
    width: 100%;
    justify-content: space-between;
    overflow-x: auto;
    white-space: nowrap;
    padding-bottom: 4px;
    -webkit-overflow-scrolling: touch;
  }
  .timeFilters {
    gap: var(--space-sm);
  }
  .filterBtn {
    padding: 4px 8px;
    font-size: 13px;
  }
}
""")
