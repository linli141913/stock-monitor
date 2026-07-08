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

# First, modify triggerOn to be 'none' for mobile.
new_tooltip = """tooltip: {
      trigger: 'axis',
      triggerOn: (typeof window !== 'undefined' && window.innerWidth <= 768) ? 'none' : 'mousemove|click',
      axisPointer: { type: 'cross' },
      borderWidth: 1,
      borderColor: '#ccc',
      padding: 10,
      textStyle: { color: '#000' },
      position: function (pos: any, params: any, el: any, elRect: any, size: any) {
        if (typeof window !== 'undefined' && window.innerWidth <= 768) {
          // 固定在左侧中间，避免遮挡右侧K线
          return [10, '15%'];
        }
        return undefined;
      }
    },"""
    
tooltip_pattern = r'tooltip: \{\s*trigger: \'axis\',\s*triggerOn: \(typeof window !== \'undefined\' && window\.innerWidth <= 768\) \? \'click\' : \'mousemove\|click\',\s*axisPointer: \{ type: \'cross\' \},\s*borderWidth: 1,\s*borderColor: \'#ccc\',\s*padding: 10,\s*textStyle: \{ color: \'#000\' \},\s*position: function \(pos: any, params: any, el: any, elRect: any, size: any\) \{\s*if \(typeof window !== \'undefined\' && window\.innerWidth <= 768\) \{\s*let x = pos\[0\] < size\.viewSize\[0\] / 2 \? pos\[0\] \+ 20 : pos\[0\] - size\.contentSize\[0\] - 20;\s*return \[x, 40\];\s*\}\s*return undefined;\s*\}\s*\},'

tsx = re.sub(tooltip_pattern, new_tooltip, tsx)

# Second, add ZRender event listeners for long-press
effect = """
  // Custom mobile touch logic (Long press to inspect, release to hide)
  const isMobile = typeof window !== 'undefined' && window.innerWidth <= 768;
  const timerRef = useRef<any>(null);
  const isInspectingRef = useRef(false);
  const startPosRef = useRef({ x: 0, y: 0 });

  useEffect(() => {
    if (!isMobile) return;
    const chart = echartsRef.current?.getEchartsInstance();
    if (!chart) return;

    const zr = chart.getZr();

    const onMouseDown = (e: any) => {
      startPosRef.current = { x: e.offsetX, y: e.offsetY };
      timerRef.current = setTimeout(() => {
        isInspectingRef.current = true;
        chart.dispatchAction({ type: 'showTip', x: e.offsetX, y: e.offsetY });
      }, 300); // 300ms for long press
    };

    const onMouseMove = (e: any) => {
      if (isInspectingRef.current) {
        chart.dispatchAction({ type: 'showTip', x: e.offsetX, y: e.offsetY });
      } else {
        const dx = e.offsetX - startPosRef.current.x;
        const dy = e.offsetY - startPosRef.current.y;
        if (Math.abs(dx) > 10 || Math.abs(dy) > 10) {
          clearTimeout(timerRef.current);
        }
      }
    };

    const onMouseUp = () => {
      clearTimeout(timerRef.current);
      if (isInspectingRef.current) {
        chart.dispatchAction({ type: 'hideTip' });
        isInspectingRef.current = false;
      }
    };

    zr.on('mousedown', onMouseDown);
    zr.on('mousemove', onMouseMove);
    zr.on('mouseup', onMouseUp);
    zr.on('globalout', onMouseUp);

    return () => {
      zr.off('mousedown', onMouseDown);
      zr.off('mousemove', onMouseMove);
      zr.off('mouseup', onMouseUp);
      zr.off('globalout', onMouseUp);
      clearTimeout(timerRef.current);
    };
  }, [data, isMobile]);
"""

if 'const timerRef = useRef<any>(null);' not in tsx:
    tsx = tsx.replace('useEffect(() => {\n    const handleFullscreenChange', effect + '\n  useEffect(() => {\n    const handleFullscreenChange')

overwrite_file(tsx_path, tsx)
print("ZR patched")
