import requests, json

def get_info():
    url = "http://f10.eastmoney.com/CompanySurvey/CompanySurveyAjax?code=SZ000021"
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(url, headers=headers)
    data = resp.json()
    print(json.dumps(data.get("jbzl", {}), ensure_ascii=False, indent=2))

get_info()
