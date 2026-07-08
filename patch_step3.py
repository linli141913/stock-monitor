import os

def overwrite_file(filepath, content):
    with open(filepath, 'w') as f:
        f.write(content)

def read_file(filepath):
    with open(filepath, 'r') as f:
        return f.read()

# 1. Modify StockChartCard.tsx
tsx_path = 'stock-monitor/src/components/stock/StockChartCard.tsx'
scc_tsx = read_file(tsx_path)

if 'const [isSimulatedFullscreen, setIsSimulatedFullscreen] = useState(false);' not in scc_tsx:
    # Add state
    scc_tsx = scc_tsx.replace('const [isFullscreen, setIsFullscreen] = useState(false);', 
                              'const [isFullscreen, setIsFullscreen] = useState(false);\n  const [isSimulatedFullscreen, setIsSimulatedFullscreen] = useState(false);')

    # Update toggleFullscreen
    new_toggle = """  const toggleFullscreen = () => {
    if (isSimulatedFullscreen) {
      setIsSimulatedFullscreen(false);
      return;
    }
    
    if (document.fullscreenElement) {
      document.exitFullscreen();
    } else {
      if (containerRef.current?.requestFullscreen) {
        containerRef.current.requestFullscreen().catch(err => {
          console.error(`Error attempting to enable fullscreen: ${err.message}`);
          setIsSimulatedFullscreen(true);
        });
      } else {
        // Fallback for iOS
        setIsSimulatedFullscreen(true);
      }
    }
  };"""
    
    import re
    # Replace the old toggleFullscreen
    scc_tsx = re.sub(r'const toggleFullscreen = \(\) => \{[\s\S]*?\};\n', new_toggle + '\n', scc_tsx)
    
    # Update container class
    scc_tsx = scc_tsx.replace('className={styles.card}', 'className={`${styles.card} ${isSimulatedFullscreen ? styles.simulatedFullscreen : \'\'}`}')
    
    # Update height calculation
    scc_tsx = scc_tsx.replace('height: isFullscreen ? \'calc(100vh - 80px)\' : \'400px\'', 'height: (isFullscreen || isSimulatedFullscreen) ? \'calc(100% - 60px)\' : \'400px\'')
    
    overwrite_file(tsx_path, scc_tsx)

# 2. Add simulatedFullscreen class
css_path = 'stock-monitor/src/components/stock/StockChartCard.module.css'
scc_css = read_file(css_path)
if '.simulatedFullscreen' not in scc_css:
    scc_css += """
.simulatedFullscreen {
  position: fixed !important;
  top: 0 !important;
  left: 0 !important;
  width: 100vw !important;
  height: 100vh !important;
  z-index: 99999 !important;
  background: white !important;
  margin: 0 !important;
  border-radius: 0 !important;
  padding: 16px !important;
  box-sizing: border-box !important;
}

@media (max-width: 768px) {
  .simulatedFullscreen {
    /* To force landscape on mobile, we can rotate the container */
    transform: rotate(90deg);
    transform-origin: top left;
    width: 100vh !important;
    height: 100vw !important;
    left: 100vw !important;
    top: 0 !important;
  }
}
"""
    overwrite_file(css_path, scc_css)

print("Step 3 patched")
