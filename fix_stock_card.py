import re

def read_file(filepath):
    with open(filepath, 'r') as f:
        return f.read()

def write_file(filepath, content):
    with open(filepath, 'w') as f:
        f.write(content)

tsx_path = 'stock-monitor/src/components/stock/StockChartCard.tsx'
tsx = read_file(tsx_path)

# 1. Update the tooltip formatter and position
tooltip_pattern = re.compile(r'tooltip:\s*\{[\s\S]*?axisPointer:\s*\{\s*link:', re.MULTILINE)
new_tooltip = """tooltip: {
      show: true,
      trigger: 'axis',
      triggerOn: (typeof window !== 'undefined' && /Mobi|Android|iPhone/i.test(navigator.userAgent)) ? 'none' : 'mousemove|click',
      axisPointer: { type: 'cross' },
      borderWidth: 1,
      borderColor: '#ccc',
      padding: 10,
      textStyle: { color: '#000' },
      formatter: function (params: any) {
        if (!params || !params.length) return '';
        let html = params[0].axisValueLabel + '<br/>';
        params.forEach((param: any) => {
          if (param.seriesType === 'candlestick') {
            const raw = param.data;
            let o, c, l, h;
            if (Array.isArray(raw)) {
              o = raw[0]; c = raw[1]; l = raw[2]; h = raw[3];
            } else {
              o = param.value[1]; c = param.value[2]; l = param.value[3]; h = param.value[4];
            }
            html += `${param.marker} ${param.seriesName}<br/>`;
            html += `&nbsp;&nbsp;开盘: ${o}<br/>`;
            html += `&nbsp;&nbsp;收盘: ${c}<br/>`;
            html += `&nbsp;&nbsp;最低: ${l}<br/>`;
            html += `&nbsp;&nbsp;最高: ${h}<br/>`;
          } else {
            let val = param.value;
            if (Array.isArray(val)) {
                val = val[1] !== undefined ? val[1] : val[0];
            } else if (typeof val === 'object' && val !== null) {
                val = val.value || 0;
            }
            if (val !== '-' && val !== undefined && val !== null) {
              html += `${param.marker} ${param.seriesName}: ${val}<br/>`;
            }
          }
        });
        return html;
      },
      position: function (pos: any, params: any, el: any, elRect: any, size: any) {
        if (typeof window !== 'undefined' && /Mobi|Android|iPhone/i.test(navigator.userAgent)) {
          let viewW = size.viewSize[0] || window.innerWidth;
          let viewH = size.viewSize[1] || window.innerHeight;
          let boxW = size.contentSize[0] || 150;
          let boxH = size.contentSize[1] || 150;
          
          let x = pos[0] + 15; 
          let y = (viewH - boxH) / 2; // 垂直居中，不忽上忽下
          
          if (x + boxW > viewW) x = pos[0] - boxW - 15;
          if (x < 0) x = 5;
          
          return [x, y];
        }
        return undefined;
      }
    },
    axisPointer: {
      link:"""
tsx = tooltip_pattern.sub(new_tooltip, tsx)

# 2. Update the mobile touch logic
touch_pattern = re.compile(r'// Custom mobile touch logic[\s\S]*?// Initialize mobile tooltip state[\s\S]*?zr\.on\(\'globalout\', onTouchEnd\);\s*\n\s*return \(\) => \{[\s\S]*?clearTimeout\(timerRef\.current\);\s*\};\s*\}, \[data\]\);', re.MULTILINE)
new_touch = """// Custom mobile touch logic
  const timerRef = useRef<any>(null);
  const isInspectingRef = useRef(false);
  const startPosRef = useRef({ x: 0, y: 0 });

  useEffect(() => {
    const isMobile = typeof window !== 'undefined' && /Mobi|Android|iPhone/i.test(navigator.userAgent);
    if (!isMobile) return;
    const chart = echartsRef.current?.getEchartsInstance();
    if (!chart) return;

    const zr = chart.getZr();

    const onTouchStart = (e: any) => {
      startPosRef.current = { x: e.offsetX, y: e.offsetY };
      timerRef.current = setTimeout(() => {
        isInspectingRef.current = true;
        chart.dispatchAction({ type: 'showTip', x: e.offsetX, y: e.offsetY });
      }, 200); 
    };

    const onTouchMove = (e: any) => {
      if (isInspectingRef.current) {
        if (e.event) e.event.preventDefault(); // 阻止浏览器滚动
        chart.dispatchAction({ type: 'showTip', x: e.offsetX, y: e.offsetY });
      } else {
        const dx = e.offsetX - startPosRef.current.x;
        const dy = e.offsetY - startPosRef.current.y;
        if (Math.abs(dx) > 5 || Math.abs(dy) > 5) {
          clearTimeout(timerRef.current);
        }
      }
    };

    const onTouchEnd = () => {
      clearTimeout(timerRef.current);
      if (isInspectingRef.current) {
        chart.dispatchAction({ type: 'hideTip' });
        isInspectingRef.current = false;
      }
    };

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
  }, [data]);"""
tsx = touch_pattern.sub(new_touch, tsx)

# 3. Add body lock for simulatedFullscreen
# Find toggleFullscreen and add useEffect below it
fullscreen_effect = """
  useEffect(() => {
    if (isSimulatedFullscreen) {
      document.body.style.overflow = 'hidden';
      document.documentElement.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
      document.documentElement.style.overflow = '';
    }
    return () => {
      document.body.style.overflow = '';
      document.documentElement.style.overflow = '';
    };
  }, [isSimulatedFullscreen]);
"""
if "document.body.style.overflow = 'hidden';" not in tsx:
    tsx = tsx.replace('const categoryData = data.map', fullscreen_effect + '\n  const categoryData = data.map')

write_file(tsx_path, tsx)
print("Fix applied successfully!")
