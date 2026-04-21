import json, time, subprocess, urllib.request, socket, os
import websocket

CHROME_EXE = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
USER = os.environ.get("USERNAME", "nucif")
BCO_PROFILE = rf"C:\Users\{USER}\AppData\Local\Google\ChromeBCO"
DEBUG_PORT = 9223
BCO_URL = "https://bco.bangkok.go.th/officer"

def is_open():
    try:
        socket.create_connection(("127.0.0.1", DEBUG_PORT), 1).close()
        return True
    except: return False

def get_tabs():
    return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json").read())

def eval_js(ws_url, expr):
    ws = websocket.create_connection(ws_url, timeout=10)
    ws.send(json.dumps({"id":1,"method":"Runtime.evaluate","params":{"expression":expr}}))
    r = json.loads(ws.recv())
    ws.close()
    return r.get("result",{}).get("result",{}).get("value")

if not is_open():
    subprocess.Popen([CHROME_EXE,
        f"--remote-debugging-port={DEBUG_PORT}",
        "--remote-allow-origins=*",
        f"--user-data-dir={BCO_PROFILE}",
        "--no-first-run", "--no-default-browser-check", BCO_URL])
    print("เปิด Chrome...")
    for _ in range(15):
        time.sleep(1)
        if is_open(): break
else:
    print("Chrome running already")

time.sleep(2)
print("รอ login... กรุณา login ที่หน้าต่าง Chrome ที่เปิดขึ้นมา")

for i in range(60):
    time.sleep(3)
    try:
        tabs = get_tabs()
        bco = next((t for t in tabs if "bco.bangkok.go.th" in t.get("url","")), None)
        if not bco:
            print(f"  {i+1}: ยังไม่พบ tab bco...")
            continue
        auth = eval_js(bco["webSocketDebuggerUrl"], "localStorage.getItem('auth')")
        if auth and json.loads(auth).get("accessToken"):
            print(f"Login สำเร็จ!")
            break
        print(f"  {i+1}: รอ login...")
    except Exception as e:
        print(f"  {i+1}: {e}")

tabs = get_tabs()
bco = next((t for t in tabs if "bco.bangkok.go.th" in t.get("url","")), None)
if not bco:
    print("ไม่พบ tab BCO"); exit(1)

ws = bco["webSocketDebuggerUrl"]
auth = eval_js(ws, "localStorage.getItem('auth')")
user = eval_js(ws, "localStorage.getItem('initUser')")

data = {"auth": json.loads(auth) if auth else None,
        "user": json.loads(user) if user else None}

with open("G:/drive/01 project/ai/bco/bco_token.json","w",encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

u = data.get("user") or {}
print(f"User: {u.get('fullName') or u.get('username') or str(u)[:80]}")
print(f"Token: {(data.get('auth') or {}).get('accessToken','')[:80]}...")
print("Done! บันทึกที่ bco_token.json")
