import os

def append_to_file(filepath, content):
    with open(filepath, 'a') as f:
        f.write("\n" + content + "\n")

if os.path.exists('stock-monitor/src/components/industry/IndustryMonitorCard.module.css'):
    append_to_file('stock-monitor/src/components/industry/IndustryMonitorCard.module.css', """
@media (max-width: 768px) {
  .card {
    padding: var(--space-md);
  }
}
""")

if os.path.exists('stock-monitor/src/components/industry/RadarNewsCard.module.css'):
    append_to_file('stock-monitor/src/components/industry/RadarNewsCard.module.css', """
@media (max-width: 768px) {
  .card {
    padding: var(--space-md);
  }
}
""")

if os.path.exists('stock-monitor/src/components/industry/AbnormalStocksCard.module.css'):
    append_to_file('stock-monitor/src/components/industry/AbnormalStocksCard.module.css', """
@media (max-width: 768px) {
  .card {
    padding: var(--space-md);
  }
  .tableWrapper {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }
}
""")

