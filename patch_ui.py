import os

def append_to_file(filepath, content):
    with open(filepath, 'a') as f:
        f.write("\n" + content + "\n")

# 1. StockOverviewCard.module.css
append_to_file('stock-monitor/src/components/stock/StockOverviewCard.module.css', """
@media (max-width: 768px) {
  .card {
    padding: var(--space-md);
  }
  .titleArea {
    flex-wrap: wrap;
    align-items: center;
  }
  .mainInfo {
    flex-direction: column;
    gap: var(--space-md);
    margin-bottom: var(--space-md);
  }
  .metricsGrid {
    border-left: none;
    padding-left: 0;
    padding-top: var(--space-md);
    border-top: 1px solid var(--color-border);
    grid-template-columns: 1fr 1fr;
    gap: var(--space-md);
  }
  .bottomMetrics {
    grid-template-columns: 1fr 1fr;
    gap: var(--space-md);
    text-align: left;
  }
  .bottomMetricItem {
    align-items: flex-start;
  }
  .latestPrice {
    font-size: 36px;
  }
}
""")

# 2. StockInfoTabs.module.css
# Tab styling for mobile to hide scrollbar
append_to_file('stock-monitor/src/components/stock/StockInfoTabs.module.css', """
.tabList::-webkit-scrollbar {
  display: none;
}
.tabList {
  -ms-overflow-style: none;
  scrollbar-width: none;
}
@media (max-width: 768px) {
  .tabBtn {
    font-size: 14px;
    padding: 12px 16px;
  }
}
""")

# 3. Global CSS table fixes
append_to_file('stock-monitor/src/app/globals.css', """
@media (max-width: 768px) {
  .table th, .table td {
    white-space: nowrap;
    padding: 10px 12px;
    font-size: 13px;
  }
}
""")

print("UI patches applied")
