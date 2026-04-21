import json, urllib.request, urllib.parse, websocket, time

DEBUG_PORT = 9223

tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json").read())
bco = next((t for t in tabs if "bco.bangkok.go.th" in t.get("url","")), tabs[0])

ws = websocket.create_connection(bco["webSocketDebuggerUrl"], timeout=30)
ws.send(json.dumps({"id":1,"method":"Network.enable","params":{}}))
ws.recv()

# navigate
ws.send(json.dumps({"id":2,"method":"Page.navigate","params":{"url":"https://bco.bangkok.go.th/officer/request"}}))

print("intercept 15 วินาที...")
seen = set()
for _ in range(150):
    try:
        ws.settimeout(0.1)
        msg = json.loads(ws.recv())
        if msg.get("method") == "Network.requestWillBeSent":
            url = msg["params"]["request"]["url"]
            req = msg["params"]["request"]
            if "bco-api" in url and url not in seen:
                seen.add(url)
                print(f"  {req.get('method','GET')} {url}")
    except: pass

ws.close()
