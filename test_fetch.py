import httpx
try:
    r = httpx.get("https://calspread.online/api/kite/token", headers={"x-token-passcode": "BOETHEBEST"}, timeout=5.0)
    print("STATUS:", r.status_code)
    print("BODY:", r.text)
except Exception as e:
    import traceback
    traceback.print_exc()
