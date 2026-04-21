import json, urllib.request, urllib.parse, websocket

DEBUG_PORT = 9223
OFFICER_ID = 149  # ปฐมรัฐ ฟักสุวรรณ
BASE_API = "https://bco-api.bangkok.go.th/api/v2"

def get_tabs():
    return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json").read())

def eval_js(ws_url, expr):
    ws = websocket.create_connection(ws_url, timeout=10)
    ws.send(json.dumps({"id":1,"method":"Runtime.evaluate","params":{"expression":expr}}))
    r = json.loads(ws.recv())
    ws.close()
    return r.get("result",{}).get("result",{}).get("value")

# ดึง token จาก cookies
tabs = get_tabs()
bco = next((t for t in tabs if "bco.bangkok.go.th" in t.get("url","")), tabs[0])
ws_url = bco["webSocketDebuggerUrl"]

raw_cookie = eval_js(ws_url, "document.cookie")
auth_encoded = next((p.split("=",1)[1] for p in raw_cookie.split("; ") if p.startswith("auth=")), None)
auth_data = json.loads(urllib.parse.unquote(auth_encoded))
TOKEN = auth_data["accessToken"]
print(f"Token OK: {TOKEN[:60]}...")

# ดึงงานค้างของปฐมรัฐ
def api_get(path, params=None):
    url = f"{BASE_API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode({k:v for k,v in params.items() if v is not None})
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json"
    })
    res = urllib.request.urlopen(req, timeout=15).read()
    return json.loads(res)

print("\nดึงงานค้าง (กำลังดำเนินการ)...")
result = api_get("/form/form_action_process", {
    "user_officer_id": OFFICER_ID,
    "page": 1,
    "size": 100
})

print(f"Status: {result.get('status')}")
if result.get("status"):
    data = result.get("data", {})
    items = data.get("items", [])
    total = data.get("total_items", 0)
    print(f"รวม: {total} รายการ\n")
    
    # บันทึกข้อมูลทั้งหมด
    with open("G:/drive/01 project/ai/bco/bco_jobs.json","w",encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    for i, item in enumerate(items, 1):
        form_no = item.get("form_number","")
        name = item.get("person_request_name","")
        status = item.get("status","")
        doc_status = item.get("doc_status","")
        action = item.get("current_action_name","") or item.get("action_name","")
        created = item.get("created_at","")[:10]
        print(f"{i:3}. {form_no} | {name[:25]} | {action[:30]} | {status} | {created}")
else:
    print("Error:", result)
