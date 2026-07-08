import os

def append_to_file(filepath, content):
    with open(filepath, 'a') as f:
        f.write("\n" + content + "\n")

# FinancialSummaryTab.module.css
append_to_file('stock-monitor/src/components/stock/FinancialSummaryTab.module.css', """
@media (max-width: 768px) {
  .grid {
    grid-template-columns: 1fr;
    gap: 12px;
  }
  .chartRow {
    flex-direction: column;
  }
  .meta {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }
}
""")

# AiAttributionTab.module.css
append_to_file('stock-monitor/src/components/stock/AiAttributionTab.module.css', """
@media (max-width: 768px) {
  .header {
    flex-direction: column;
    align-items: flex-start;
    gap: 12px;
  }
  .judgmentHeader {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }
}
""")

# StockOverviewCard.module.css
if os.path.exists('stock-monitor/src/components/stock/StockOverviewCard.module.css'):
    append_to_file('stock-monitor/src/components/stock/StockOverviewCard.module.css', """
@media (max-width: 768px) {
  .statsGrid {
    grid-template-columns: 1fr 1fr;
  }
  .header {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }
}
""")

# RelatedStocksCard.module.css
if os.path.exists('stock-monitor/src/components/stock/RelatedStocksCard.module.css'):
    append_to_file('stock-monitor/src/components/stock/RelatedStocksCard.module.css', """
@media (max-width: 768px) {
  .grid {
    grid-template-columns: 1fr;
  }
}
""")

print("Mobile CSS patched!")
