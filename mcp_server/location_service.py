import os
import requests


def _normalize_city(city: str | None) -> str:
    if not city:
        return ""
    return str(city).strip().replace('市', '')


def _from_amap_ip() -> str:
    key = os.getenv("GEO_AMAP_KEY", "").strip()
    if not key:
        return ""
    try:
        response = requests.get(
            "https://restapi.amap.com/v3/ip",
            params={"key": key},
            timeout=3,
        )
        data = response.json()
        if data.get("status") != "1":
            return ""
        city = _normalize_city(data.get("city"))
        if city:
            return city
        province = _normalize_city(data.get("province"))
        return province
    except Exception:
        return ""


def _from_ipapi() -> str:
    try:
        data = requests.get("http://ip-api.com/json/?lang=zh-CN", timeout=3).json()
        if data.get("status") == "success":
            return _normalize_city(data.get("city"))
        return ""
    except Exception:
        return ""


def _from_ipinfo() -> str:
    token = os.getenv("GEO_IPINFO_TOKEN", "").strip()
    url = "https://ipinfo.io/json"
    params = {"token": token} if token else None
    try:
        data = requests.get(url, params=params, timeout=3).json()
        return _normalize_city(data.get("city"))
    except Exception:
        return ""


def _resolve_city_from_provider() -> str:
    provider = os.getenv("GEO_PROVIDER", "auto").strip().lower()

    if provider == "amap":
        return _from_amap_ip()
    if provider == "ip-api":
        return _from_ipapi()
    if provider == "ipinfo":
        return _from_ipinfo()

    for resolver in (_from_amap_ip, _from_ipapi, _from_ipinfo):
        city = resolver()
        if city:
            return city
    return ""


def get_user_location_impl(consent: bool = False, user_city: str = "") -> str:
    normalized_city = _normalize_city(user_city)
    if normalized_city:
        return normalized_city

    if not consent:
        return "未获得用户定位授权；请先征得同意或让用户手动提供城市。"

    default_city = _normalize_city(os.getenv("GEO_DEFAULT_CITY", "北京")) or "北京"
    city = _resolve_city_from_provider()
    return city or default_city
