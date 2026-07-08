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

# 1. Remove touchend listener effect
effect_pattern = r'useEffect\(\(\) => \{\n\s*const handleRelease = \(\) => \{[\s\S]*?\}, \[\]\);\n\n'
tsx = re.sub(effect_pattern, '', tsx)

# 2. Update tooltip configuration
# Find tooltip object and replace
tooltip_pattern = r'tooltip: \{\s*trigger: \'axis\',\s*triggerOn: \'mousemove\',\s*hideDelay: 0,\s*transitionDuration: 0,\s*axisPointer: \{ type: \'cross\' \},\s*borderWidth: 1,\s*borderColor: \'#ccc\',\s*padding: 10,\s*textStyle: \{ color: \'#000\' \}\s*\},'

new_tooltip = """tooltip: {
      trigger: 'axis',
      triggerOn: (typeof window !== 'undefined' && window.innerWidth <= 768) ? 'click' : 'mousemove|click',
      axisPointer: { type: 'cross' },
      borderWidth: 1,
      borderColor: '#ccc',
      padding: 10,
      textStyle: { color: '#000' },
      position: function (pos: any, params: any, el: any, elRect: any, size: any) {
        if (typeof window !== 'undefined' && window.innerWidth <= 768) {
          // 固定 Y 轴位置（如顶部往下 40px），X 轴跟随触摸点但避开手指
          let x = pos[0] < size.viewSize[0] / 2 ? pos[0] + 20 : pos[0] - size.contentSize[0] - 20;
          return [x, 40];
        }
        return undefined;
      }
    },"""

if 'triggerOn: \'mousemove\',' in tsx:
    tsx = re.sub(tooltip_pattern, new_tooltip, tsx)
else:
    # If the pattern is slightly different
    pass

overwrite_file(tsx_path, tsx)
print("Tooltip patched")
