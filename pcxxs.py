import requests

url = "https://www.shixiseng.com/interns?keyword=%E5%90%8E%E7%AB%AF%E5%BC%80%E5%8F%91&city=%E5%85%A8%E5%9B%BD&type=intern&from=menu"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.shixiseng.com/",
    "Accept": "application/json, text/plain, */*",
    "X-Requested-With": "XMLHttpRequest"
}
params = {
    "keyword": "后端开发",
    "type": "intern",
    "page": 1
}

try:
    resp = requests.get(url, headers=headers, params=params, timeout=10)
    print("状态码:", resp.status_code)
    print("响应内容:", resp.text[:500])  # 打印前500字符
except Exception as e:
    print("请求失败:", e)