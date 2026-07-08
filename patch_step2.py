import os

def overwrite_file(filepath, content):
    with open(filepath, 'w') as f:
        f.write(content)

def read_file(filepath):
    with open(filepath, 'r') as f:
        return f.read()

# 4. AiAttributionTab.module.css
css_path = 'stock-monitor/src/components/stock/AiAttributionTab.module.css'
aat_css = read_file(css_path)

if '.gaugeCard' not in aat_css:
    aat_css += """
.gaugeCard {
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(145deg, #ffffff 0%, #f9fafb 100%);
  border: 1px solid #e5e7eb;
  border-radius: 16px;
  padding: 32px 40px;
  margin-bottom: 32px;
  box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05);
}

.gaugeChart {
  flex: 1;
  max-width: 400px;
}

.gaugeInfo {
  flex: 1;
  padding-left: 40px;
  border-left: 1px solid #e5e7eb;
}

.gaugeInfoTitle {
  margin: 0 0 16px 0;
  font-size: 1.2rem;
  color: #374151;
  font-weight: 600;
}

.gaugeScoreArea {
  display: flex;
  align-items: baseline;
  margin-bottom: 16px;
}

.gaugeScoreNum {
  font-size: 3.5rem;
  font-weight: 800;
  line-height: 1;
}

.gaugeScoreUnit {
  font-size: 1.2rem;
  color: #6b7280;
  margin-left: 4px;
  font-weight: 500;
}

.gaugeScoreTag {
  margin-left: 20px;
  padding: 6px 16px;
  border-radius: 999px;
  font-size: 1rem;
  font-weight: 600;
}

.gaugeInfoDesc {
  margin: 0;
  font-size: 0.95rem;
  color: #6b7280;
  line-height: 1.6;
}

.gaugeInfoDesc strong {
  color: #4b5563;
}

@media (max-width: 768px) {
  .gaugeCard {
    flex-direction: column;
    padding: 16px;
    border-radius: 12px;
  }
  .gaugeChart {
    width: 100%;
    margin-bottom: 16px;
  }
  .gaugeInfo {
    padding-left: 0;
    border-left: none;
    border-top: 1px solid #e5e7eb;
    padding-top: 16px;
  }
  .gaugeScoreArea {
    flex-wrap: wrap;
    align-items: center;
  }
  .gaugeScoreNum {
    font-size: 2.8rem;
  }
  .gaugeScoreTag {
    margin-left: auto; /* push to right */
    white-space: nowrap;
    font-size: 0.9rem;
    padding: 4px 12px;
  }
}
"""
    overwrite_file(css_path, aat_css)

print("Step 2 patched")
