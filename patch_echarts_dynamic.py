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

# First, fix the tooltip base config
tooltip_pattern = r'tooltip: \{\s*trigger: \'axis\',[\s\S]*?position: function[\s\S]*?return undefined;\s*\}\s*\},'

new_tooltip = """tooltip: {
      show: true, // will be dynamically toggled on mobile
      trigger: 'axis',
      triggerOn: (typeof window !== 'undefined' && window.innerWidth <= 768) ? 'none' : 'mousemove|click',
      axisPointer: { type: 'cross' },
      borderWidth: 1,
      borderColor: '#ccc',
      padding: 10,
      textStyle: { color: '#000' },
      position: function (pos: any, params: any, el: any, elRect: any, size: any) {
        if (typeof window !== 'undefined' && window.innerWidth <= 768) {
          // 固定在图表左上角，彻底避免跳动和截断
          return ['2%', '2%'];
        }
        return undefined;
      }
    },"""

tsx = re.sub(tooltip_pattern, new_tooltip, tsx)

# Second, add the custom touch logic
effect = """
  // Custom mobile touch logic: Long-press to inspect, Release to hide
  const timerRef = useRef<any>(null);
  const isInspectingRef = useRef(false);
  const startPosRef = useRef({ x: 0, y: 0 });

  useEffect(() => {
    const isMobile = typeof window !== 'undefined' && window.innerWidth <= 768;
    if (!isMobile) return;
    const chart = echartsRef.current?.getEchartsInstance();
    if (!chart) return;

    const zr = chart.getZr();

    const onTouchStart = (e: any) => {
      startPosRef.current = { x: e.offsetX, y: e.offsetY };
      timerRef.current = setTimeout(() => {
        isInspectingRef.current = true;
        // Enable tooltip explicitly
        chart.setOption({ tooltip: { show: true, triggerOn: 'mousemove' } });
        chart.dispatchAction({ type: 'showTip', x: e.offsetX, y: e.offsetY });
      }, 350); // 350ms long press
    };

    const onTouchMove = (e: any) => {
      if (isInspectingRef.current) {
        // Already inspecting, update tooltip
        chart.dispatchAction({ type: 'showTip', x: e.offsetX, y: e.offsetY });
      } else {
        // Not inspecting yet, if moved significantly, cancel long press
        const dx = e.offsetX - startPosRef.current.x;
        const dy = e.offsetY - startPosRef.current.y;
        if (Math.abs(dx) > 10 || Math.abs(dy) > 10) {
          clearTimeout(timerRef.current);
        }
      }
    };

    const onTouchEnd = () => {
      clearTimeout(timerRef.current);
      if (isInspectingRef.current) {
        chart.dispatchAction({ type: 'hideTip' });
        // Disable tooltip to restore panning
        chart.setOption({ tooltip: { show: false, triggerOn: 'none' } });
        isInspectingRef.current = false;
      }
    };

    // Initialize mobile tooltip state
    chart.setOption({ tooltip: { show: false, triggerOn: 'none' } });

    zr.on('mousedown', onTouchStart);
    zr.on('mousemove', onTouchMove);
    zr.on('mouseup', onTouchEnd);
    zr.on('globalout', onTouchEnd);

    return () => {
      zr.off('mousedown', onTouchStart);
      zr.off('mousemove', onTouchMove);
      zr.off('mouseup', onTouchEnd);
      zr.off('globalout', onTouchEnd);
      clearTimeout(timerRef.current);
    };
  }, [data]);
"""

if 'const isInspectingRef = useRef(false);' not in tsx:
    tsx = tsx.replace('useEffect(() => {\n    const handleFullscreenChange', effect + '\n  useEffect(() => {\n    const handleFullscreenChange')

overwrite_file(tsx_path, tsx)
print("Dynamic patched")
