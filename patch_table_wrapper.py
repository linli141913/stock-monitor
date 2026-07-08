import os

def append_to_file(filepath, content):
    with open(filepath, 'a') as f:
        f.write("\n" + content + "\n")

if os.path.exists('stock-monitor/src/components/stock/FinancialSummaryTab.module.css'):
    append_to_file('stock-monitor/src/components/stock/FinancialSummaryTab.module.css', """
@media (max-width: 768px) {
  .tableWrapper {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    margin: 0 -var(--space-md);
    padding: 0 var(--space-md);
    border-radius: 0;
    border-left: none;
    border-right: none;
  }
}
""")
