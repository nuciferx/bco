"""intercept network requests จาก BCO tab"""
import json, urllib.request, urllib.parse, websocket, time, threading

DEBUG_PORT = 9223

tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json").read())
bco = next((t for t in tabs if "bco.bangkok.go.th" in t.get("url","")), tabs[0])
ws_url = bco["webSocketDebuggerUrl"]

ws = websocket.create_connection(ws_url, timeout=30)

# enable network
ws.send(json.dumps({"id":1,"method":"Network.enable","params":{}}))
ws.recv()

# navigate to dashboard/งานค้าง page
ws.send(json.dumps({"id":2,"method":"Runtime.evaluate","params":{"expression":"window.location.href"}}))
r = json.loads(ws.recv())
print("Current URL:", r.get("result",{}).get("result",{}).get("value"))

# navigate to officer dashboard
ws.send(json.dumps({"id":3,"method":"Runtime.evaluate","params":{"expression":"window.location.assign('/officer/dashboard')"}}))

print("กำลัง intercept API calls... รอ 10 วินาที")
api_calls = []
start = time.time()
while time.time() - start < 10:
    try:
        ws.settimeout(1)
        msg = json.loads(ws.recv())
        method = msg.get("method","")
        if method == "Network.requestWillBeSent":
            url = msg["params"]["request"]["url"]
            if "bco-api" in url:
                print(f"  API: {url}")
                api_calls.append(url)
    except: pass

ws.close()
print(f"\nพบ {len(api_calls)} API calls")
