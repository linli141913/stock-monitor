import os

def append_to_file(filepath, content):
    with open(filepath, 'a') as f:
        f.write("\n" + content + "\n")

# 1. AppHeader.module.css
append_to_file('stock-monitor/src/components/layout/AppHeader.module.css', """
@media (max-width: 768px) {
  .container {
    padding: 0 var(--space-md);
    height: auto;
    min-height: 60px;
    flex-wrap: wrap;
    justify-content: center;
    gap: var(--space-sm);
    padding-top: var(--space-sm);
    padding-bottom: var(--space-sm);
  }
  .nav {
    width: 100%;
    justify-content: center;
    gap: var(--space-md);
    overflow-x: auto;
    padding-bottom: 4px;
  }
  .logoText {
    font-size: 18px;
  }
  .navItem {
    font-size: 14px;
    white-space: nowrap;
  }
}
""")

# 2. SearchMonitorBar.module.css
append_to_file('stock-monitor/src/components/stock/SearchMonitorBar.module.css', """
@media (max-width: 768px) {
  .bar {
    flex-direction: column;
    align-items: stretch;
    gap: var(--space-sm);
  }
  .searchContainer {
    max-width: 100%;
  }
  .badge {
    margin-left: 0;
    justify-content: center;
  }
  .monitorBtn {
    width: 100%;
  }
}
""")

# 3. StockInfoTabs.module.css
append_to_file('stock-monitor/src/components/stock/StockInfoTabs.module.css', """
@media (max-width: 768px) {
  .tabList {
    overflow-x: auto;
    white-space: nowrap;
    -webkit-overflow-scrolling: touch;
  }
  .tabBtn {
    padding: var(--space-md) var(--space-md);
    flex: none;
  }
  .financeGrid {
    grid-template-columns: repeat(2, 1fr);
    gap: var(--space-md);
  }
  .content {
    padding: var(--space-md);
  }
  .label {
    width: 80px;
  }
}
@media (max-width: 480px) {
  .financeGrid {
    grid-template-columns: 1fr;
  }
}
""")

# 4. page.module.css
append_to_file('stock-monitor/src/app/page.module.css', """
@media (max-width: 768px) {
  .container {
    padding: 0 var(--space-md);
  }
  .leftCol, .rightCol {
    gap: var(--space-md);
  }
}
""")

# 5. FinancialSummaryTab.module.css (if exists, if not ignore)
if os.path.exists('stock-monitor/src/components/stock/FinancialSummaryTab.module.css'):
    append_to_file('stock-monitor/src/components/stock/FinancialSummaryTab.module.css', """
@media (max-width: 768px) {
  .chartGrid {
    grid-template-columns: 1fr;
  }
}
""")

# 6. AiAttributionTab.module.css (if exists)
if os.path.exists('stock-monitor/src/components/stock/AiAttributionTab.module.css'):
    append_to_file('stock-monitor/src/components/stock/AiAttributionTab.module.css', """
@media (max-width: 768px) {
  .topRow {
    flex-direction: column;
  }
  .gaugeContainer, .summaryContainer {
    width: 100%;
  }
  .evidenceGrid {
    grid-template-columns: 1fr;
  }
}
""")

print("CSS media queries applied!")
