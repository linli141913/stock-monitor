import os

def overwrite_file(filepath, content):
    with open(filepath, 'w') as f:
        f.write(content)

def read_file(filepath):
    with open(filepath, 'r') as f:
        return f.read()

# 1. StockOverviewCard.module.css - Strip old media queries and append new one
soc_css = read_file('stock-monitor/src/components/stock/StockOverviewCard.module.css')
if '@media' in soc_css:
    soc_css = soc_css[:soc_css.find('@media')]

soc_css += """
@media (max-width: 768px) {
  .card { padding: 12px; margin-bottom: 8px; }
  .header { flex-direction: column; align-items: flex-start; gap: 8px; margin-bottom: 12px; }
  .titleArea { width: 100%; justify-content: flex-start; flex-wrap: wrap; }
  .name { font-size: 20px; }
  .code { font-size: 14px; }
  
  .mainInfo { 
    flex-direction: row; 
    align-items: center; 
    justify-content: space-between; 
    gap: 8px; 
    margin-bottom: 16px; 
  }
  .priceArea { flex: 1; min-width: 0; }
  .latestPrice { font-size: 34px; }
  .changeArea { font-size: 14px; margin-top: 2px; }
  .updateTime { display: none; }
  
  .metricsGrid { 
    flex: 1.2;
    border-left: none; 
    padding-left: 0; 
    padding-top: 0; 
    border-top: none; 
    grid-template-columns: 1fr 1fr; 
    gap: 8px 12px; 
  }
  .metricItem { flex-direction: column; align-items: flex-end; justify-content: center; gap: 2px; }
  .metricLabel { font-size: 12px; }
  .metricValue { font-size: 14px; }

  .bottomMetrics { 
    grid-template-columns: repeat(3, 1fr); 
    gap: 12px; 
    padding-top: 12px; 
    border-top: 1px solid var(--color-border);
  }
  .bottomMetricItem { align-items: flex-start; gap: 2px; }
  .metricValueBold { font-size: 15px; white-space: nowrap; }
}
"""
overwrite_file('stock-monitor/src/components/stock/StockOverviewCard.module.css', soc_css)

# 2. StockInfoTabs.module.css
sit_css = read_file('stock-monitor/src/components/stock/StockInfoTabs.module.css')
if '@media' in sit_css:
    sit_css = sit_css[:sit_css.find('@media')]

sit_css += """
.tabList::-webkit-scrollbar { display: none; }
.tabList { -ms-overflow-style: none; scrollbar-width: none; }
@media (max-width: 768px) {
  .card { box-shadow: none; border-radius: 0; border-top: 1px solid #f0f0f0; }
  .tabList { overflow-x: auto; white-space: nowrap; -webkit-overflow-scrolling: touch; border-bottom: 1px solid #e5e7eb; }
  .tabBtn { padding: 12px 16px; flex: none; font-size: 15px; }
  .content { padding: 16px 12px; }
  
  .financeGrid { grid-template-columns: 1fr 1fr; gap: 8px; }
  .financeItem { padding: 10px; gap: 2px; border: 1px solid #f0f0f0; }
  .financeLabel { font-size: 12px; }
  .financeValue { font-size: 16px; }
}
"""
overwrite_file('stock-monitor/src/components/stock/StockInfoTabs.module.css', sit_css)

# 3. FinancialSummaryTab.module.css
fst_css = read_file('stock-monitor/src/components/stock/FinancialSummaryTab.module.css')
if '@media' in fst_css:
    fst_css = fst_css[:fst_css.find('@media')]

fst_css += """
@media (max-width: 768px) {
  .grid { grid-template-columns: 1fr 1fr; gap: 8px; }
  .card { padding: 12px; }
  .cardValue { font-size: 16px; }
  .cardTitle { font-size: 12px; margin-bottom: 4px; }
  
  .chartRow { flex-direction: column; gap: 16px; margin-bottom: 24px; }
  .chartCard { padding: 12px 0; border: none; }
  
  .tableWrapper { 
    margin: 0; 
    padding: 0; 
    border-radius: 6px; 
    overflow-x: auto; 
    -webkit-overflow-scrolling: touch;
    border: 1px solid #e5e7eb;
  }
  .table th, .table td { white-space: nowrap; padding: 10px; font-size: 12px; }
}
"""
overwrite_file('stock-monitor/src/components/stock/FinancialSummaryTab.module.css', fst_css)

# 4. StockChartCard.module.css
scc_css = read_file('stock-monitor/src/components/stock/StockChartCard.module.css')
if '@media' in scc_css:
    scc_css = scc_css[:scc_css.find('@media')]

scc_css += """
@media (max-width: 768px) {
  .card { padding: 12px; margin-bottom: 8px; border-radius: 8px; }
  .header { flex-direction: column; align-items: flex-start; gap: 12px; margin-bottom: 12px; }
  .controls { width: 100%; justify-content: flex-start; overflow-x: auto; white-space: nowrap; -webkit-overflow-scrolling: touch; }
  .controls::-webkit-scrollbar { display: none; }
  .timeFilters, .maFilters { gap: 6px; }
  .filterBtn { padding: 6px 10px; font-size: 14px; }
  .chartContainer { height: 300px !important; }
}
"""
overwrite_file('stock-monitor/src/components/stock/StockChartCard.module.css', scc_css)

# 5. SearchMonitorBar.module.css
smb_css = read_file('stock-monitor/src/components/stock/SearchMonitorBar.module.css')
if '@media' in smb_css:
    smb_css = smb_css[:smb_css.find('@media')]

smb_css += """
@media (max-width: 768px) {
  .bar { flex-direction: column; padding: 12px; gap: 12px; border-radius: 8px; }
  .searchContainer { max-width: 100%; width: 100%; }
  .badge { margin-left: 0; justify-content: center; width: 100%; }
  .monitorBtn { width: 100%; justify-content: center; }
}
"""
overwrite_file('stock-monitor/src/components/stock/SearchMonitorBar.module.css', smb_css)

print("Deep UI patching completed!")
