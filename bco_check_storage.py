import json, urllib.request, websocket

DEBUG_PORT = 9223

def get_tabs():
    return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json").read())

def eval_js(ws_url, expr):
    ws = websocket.create_connection(ws_url, timeout=10)
    ws.send(json.dumps({"id":1,"method":"Runtime.evaluate","params":{"expression":expr}}))
    r = json.loads(ws.recv())
    ws.close()
    return r.get("result",{}).get("result",{}).get("value")

tabs = get_tabs()
bco = next((t for t in tabs if "bco.bangkok.go.th" in t.get("url","")), tabs[0])
ws = bco["webSocketDebuggerUrl"]

# ดู keys ทั้งหมดใน localStorage
keys = eval_js(ws, "JSON.stringify(Object.keys(localStorage))")
print("localStorage keys:", keys)

# ดูค่า auth-related keys
for k in ["auth","initUser","person-auth","person-initUser","token","accessToken","jwt"]:
    val = eval_js(ws, f"localStorage.getItem('{k}')")
    if val:
        print(f"\n{k}:", val[:200])

# ดู cookies ที่เกี่ยวข้อง
cookies = eval_js(ws, "document.cookie")
print("\ncookies:", (cookies or "")[:300])
