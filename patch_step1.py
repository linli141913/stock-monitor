import os

def overwrite_file(filepath, content):
    with open(filepath, 'w') as f:
        f.write(content)

def read_file(filepath):
    with open(filepath, 'r') as f:
        return f.read()

# 1. StockOverviewCard.module.css - Move right column left
soc_css = read_file('stock-monitor/src/components/stock/StockOverviewCard.module.css')
# Replace `align-items: flex-end;` with `align-items: flex-start;` in the media query.
soc_css = soc_css.replace('align-items: flex-end;', 'align-items: flex-start;')
overwrite_file('stock-monitor/src/components/stock/StockOverviewCard.module.css', soc_css)

# 2. StockChartCard.tsx - Add "全屏" text to mobile button
scc_tsx = read_file('stock-monitor/src/components/stock/StockChartCard.tsx')
# Find the fullscreen button
scc_tsx = scc_tsx.replace('<button className={styles.fullscreenBtn} onClick={toggleFullscreen} title="全屏显示">', '<button className={`${styles.fullscreenBtn} ${styles.mobileFullscreenText}`} onClick={toggleFullscreen} title="全屏显示">')
scc_tsx = scc_tsx.replace('⛶', '⛶ <span className={styles.fsText}>全屏</span>')
overwrite_file('stock-monitor/src/components/stock/StockChartCard.tsx', scc_tsx)

scc_css = read_file('stock-monitor/src/components/stock/StockChartCard.module.css')
if '.fsText' not in scc_css:
    scc_css += """
.fsText { display: none; }
@media (max-width: 768px) {
  .fsText { display: inline; margin-left: 4px; font-size: 14px; }
  .mobileFullscreenText { 
    background-color: var(--color-primary-light); 
    color: var(--color-primary); 
    padding: 4px 10px; 
    border-radius: 4px; 
    border: none;
    display: flex;
    align-items: center;
  }
}
"""
overwrite_file('stock-monitor/src/components/stock/StockChartCard.module.css', scc_css)

# 3. RelatedStocksCard.module.css - Prevent text wrapping
rsc_css = read_file('stock-monitor/src/components/stock/RelatedStocksCard.module.css')
if '.row' in rsc_css:
    rsc_css += """
@media (max-width: 768px) {
  .row { flex-wrap: nowrap; overflow-x: auto; -webkit-overflow-scrolling: touch; }
  .stockName { white-space: nowrap; }
  .stockCode { white-space: nowrap; }
  .list { margin: 0 -12px; padding: 0 12px; overflow-x: auto; }
  .item { min-width: max-content; }
}
"""
    overwrite_file('stock-monitor/src/components/stock/RelatedStocksCard.module.css', rsc_css)

print("Step 1 patched")
