import re
with open("ai_analysis.py", "r") as f:
    content = f.read()

# Replace: ai_res.get("futureTrendPrediction", "暂无预测")
# With: ai_res.get("futureTrendPrediction") or ai_res.get("futureTrendAnalysis") or ai_res.get("futureTrend") or "暂无推演内容"

old_line = 'ai_res.get("futureTrendPrediction", "暂无预测")'
new_line = 'ai_res.get("futureTrendPrediction") or ai_res.get("futureTrendAnalysis") or ai_res.get("futureTrend") or ai_res.get("future_trend_prediction") or "暂无推演内容"'
content = content.replace(old_line, new_line)

with open("ai_analysis.py", "w") as f:
    f.write(content)
print("Patched.")
