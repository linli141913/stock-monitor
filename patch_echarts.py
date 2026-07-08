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

if 'const echartsRef = useRef<any>(null);' not in tsx:
    # Add echartsRef
    tsx = tsx.replace('const containerRef = useRef<HTMLDivElement>(null);',
                      'const containerRef = useRef<HTMLDivElement>(null);\n  const echartsRef = useRef<any>(null);')
    
    # Add useEffect for touchend
    effect = """  useEffect(() => {
    const handleRelease = () => {
      echartsRef.current?.getEchartsInstance()?.dispatchAction({ type: 'hideTip' });
    };
    const el = containerRef.current;
    if (el) {
      el.addEventListener('touchend', handleRelease);
      el.addEventListener('mouseup', handleRelease);
      return () => {
        el.removeEventListener('touchend', handleRelease);
        el.removeEventListener('mouseup', handleRelease);
      };
    }
  }, []);"""
    
    tsx = tsx.replace('useEffect(() => {\n    const handleFullscreenChange = () => {',
                      effect + '\n\n  useEffect(() => {\n    const handleFullscreenChange = () => {')
    
    # Add ref to ReactECharts
    tsx = tsx.replace('<ReactECharts \n          option={option}', '<ReactECharts \n          ref={echartsRef}\n          option={option}')
    
    overwrite_file(tsx_path, tsx)
