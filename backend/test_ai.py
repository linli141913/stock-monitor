from ai_analysis import get_ai_attribution
import os

# We will need some mock env var for LLM if it's set
res = get_ai_attribution("000021")
print(res)
