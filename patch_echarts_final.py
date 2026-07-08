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

# Fix dimensions
tsx = tsx.replace("dimensions: ['date', '开盘', '收盘', '最低', '最高'],", "dimensions: ['开盘', '收盘', '最低', '最高'],")

# Fix position
tooltip_pattern = r'position: function \([\s\S]*?return undefined;\s*\}'
new_position = """position: function (pos: any, params: any, el: any, elRect: any, size: any) {
        if (typeof window !== 'undefined' && /Mobi|Android|iPhone/i.test(navigator.userAgent)) {
          let viewW = size.viewSize[0] || window.innerWidth;
          let viewH = size.viewSize[1] || window.innerHeight;
          let boxW = size.contentSize[0] || 150;
          let boxH = size.contentSize[1] || 150;
          
          // 放在十字线触点旁边
          let x = pos[0] + 15;
          let y = pos[1] + 15;
          
          // 如果右侧溢出，放左侧
          if (x + boxW > viewW) x = pos[0] - boxW - 15;
          // 如果下方溢出，放上方
          if (y + boxH > viewH) y = pos[1] - boxH - 15;
          
          // 强制不越界
          if (x < 0) x = 5;
          if (y < 0) y = 5;
          
          return [x, y];
        }
        return undefined;
      }"""
tsx = re.sub(tooltip_pattern, new_position, tsx)

# Fix the 100dvh back to 100vh for iOS Safari bottom bar locking
# Wait, the user said: "图一下面箭头指的浏览这个地方自动给我锁死不要让他动，这个也参考图二那里"
# If I use 100dvh, it moves up and down when Safari URL bar shows/hides.
# If I use 100vh, the URL bar stays static or can be pushed away. Let's revert simulatedFullscreen back to 100vw/100vh.
# The user wants "不要让他动" (don't let it move).

overwrite_file(tsx_path, tsx)
print("Patched completely.")
