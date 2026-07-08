import re
with open("ai_analysis.py", "r") as f:
    content = f.read()

# Replace: ai_res = json.loads(content)
# With: print("RAW_LLM_OUTPUT:", content); ai_res = json.loads(content)
old_line = 'ai_res = json.loads(content)'
new_line = 'print("RAW_LLM_OUTPUT:", content, flush=True)\n        ai_res = json.loads(content)'

if "RAW_LLM_OUTPUT" not in content:
    content = content.replace(old_line, new_line)
    with open("ai_analysis.py", "w") as f:
        f.write(content)
    print("Debug patched.")
else:
    print("Already patched.")
