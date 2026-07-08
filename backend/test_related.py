import traceback
from main import get_related_stocks
import requests

old_get = requests.get

def debug_get(*args, **kwargs):
    print("GET:", args, kwargs)
    return old_get(*args, **kwargs)

requests.get = debug_get

get_related_stocks('000021')
