import os
import re

def overwrite_file(filepath, content):
    with open(filepath, 'w') as f:
        f.write(content)

def read_file(filepath):
    with open(filepath, 'r') as f:
        return f.read()

tsx_path = 'stock-monitor/src/components/stock/StockChartCard.tsx'
tsx = read_file(tsx_path)

pos_pattern = r'position: function \(pos: any, params: any, el: any, elRect: any, size: any\) \{\s*if \(typeof window !== \'undefined\' && window\.innerWidth <= 768\) \{\s*// 固定在左侧中间，避免遮挡右侧K线\s*return \[10, \'15%\'\];\s*\}\s*return undefined;\s*\}'

new_pos = """position: function (pos: any, params: any, el: any, elRect: any, size: any) {
        if (typeof window !== 'undefined' && window.innerWidth <= 768) {
          let x = pos[0] < size.viewSize[0] / 2 ? pos[0] + 20 : pos[0] - size.contentSize[0] - 20;
          let y = pos[1] - size.contentSize[1] / 2;
          if (y < 0) y = 10;
          if (y + size.contentSize[1] > size.viewSize[1]) y = size.viewSize[1] - size.contentSize[1] - 10;
          return [x, y];
        }
        return undefined;
      }"""

tsx = re.sub(pos_pattern, new_pos, tsx)
overwrite_file(tsx_path, tsx)
print("Position patched")
