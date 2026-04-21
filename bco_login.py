"""เปิด Playwright browser ไปที่ bco แล้วดึง token หลัง login อัตโนมัติ"""
import asyncio, json
from playwright.async_api import async_playwright

BCO_URL = "https://bco.bangkok.go.th/officer"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(BCO_URL)
        
        print("รอ login... (polling localStorage ทุก 3 วินาที)")
        
        for i in range(60):  # รอสูงสุด 3 นาที
            await asyncio.sleep(3)
            auth = await page.evaluate("() => localStorage.getItem('auth')")
            if auth:
                try:
                    auth_data = json.loads(auth)
                    if auth_data.get("accessToken"):
                        print(f"Login สำเร็จ! (try {i+1})")
                        break
                except:
                    pass
        else:
            print("Timeout - ไม่พบ token")
            await browser.close()
            return
        
        user = await page.evaluate("() => localStorage.getItem('initUser')")
        
        data = {
            "auth": json.loads(auth) if auth else None,
            "user": json.loads(user) if user else None
        }
        
        out = "G:/drive/01 project/ai/bco/bco_token.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        print(f"บันทึก token แล้วที่ {out}")
        if data["user"]:
            print(f"User: {data['user'].get('fullName') or data['user'].get('username')}")
        
        await browser.close()

asyncio.run(main())
