import os

def overwrite_file(filepath, content):
    with open(filepath, 'w') as f:
        f.write(content)

def read_file(filepath):
    if not os.path.exists(filepath): return ""
    with open(filepath, 'r') as f:
        return f.read()

# 6. IndustryMonitorCard.module.css
imc_css = read_file('stock-monitor/src/components/industry/IndustryMonitorCard.module.css')
if imc_css:
    if '@media' in imc_css:
        imc_css = imc_css[:imc_css.find('@media')]
    imc_css += """
@media (max-width: 768px) {
  .card { padding: 12px; margin-bottom: 8px; border-radius: 8px; }
  .header { margin-bottom: 12px; padding-bottom: 8px; }
  .row { flex-wrap: wrap; gap: 8px; }
  .label { width: 100%; font-size: 13px; margin-bottom: -4px; }
  .valueArea { width: 100%; }
}
"""
    overwrite_file('stock-monitor/src/components/industry/IndustryMonitorCard.module.css', imc_css)

# 7. AbnormalStocksCard.module.css
asc_css = read_file('stock-monitor/src/components/industry/AbnormalStocksCard.module.css')
if asc_css:
    if '@media' in asc_css:
        asc_css = asc_css[:asc_css.find('@media')]
    asc_css += """
@media (max-width: 768px) {
  .card { padding: 12px; margin-bottom: 8px; border-radius: 8px; }
  .header { margin-bottom: 12px; padding-bottom: 8px; }
  .tableWrapper { 
    margin: 0 -12px; 
    padding: 0 12px; 
    overflow-x: auto; 
    -webkit-overflow-scrolling: touch; 
  }
  .table th, .table td { white-space: nowrap; padding: 10px 8px; font-size: 13px; }
}
"""
    overwrite_file('stock-monitor/src/components/industry/AbnormalStocksCard.module.css', asc_css)

print("Industry mobile CSS patched!")
