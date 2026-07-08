with open("main.py", "r") as f:
    content = f.read()

# The injected code started with @app.get("/api/stock/ai_attribution/{symbol}")
# and ended with the return statement.
import re
pattern = r'@app\.get\("/api/stock/ai_attribution/\{symbol\}"\)\s*def get_ai_attribution\(symbol: str\):.*?return \{.*?\n        \}\n    except Exception as e:.*?return \{.*?\}\n'
new_content = re.sub(pattern, '', content, flags=re.DOTALL)
with open("main.py", "w") as f:
    f.write(new_content)
print("Removed injected mock")
