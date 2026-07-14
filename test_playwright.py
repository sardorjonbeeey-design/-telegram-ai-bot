import os
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"
import asyncio
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        page = await browser.new_page()

        print("Opening OLX...")

        try:
            response = await page.goto(
                "https://www.olx.uz",
                wait_until="networkidle",
                timeout=60000,
            )

            print("Status:", response.status if response else "No response")
            print("Title:", await page.title())

            html = await page.content()

            with open("olx_page.html", "w", encoding="utf-8") as f:
                f.write(html)

            print("HTML saved successfully!")
            print(html[:500])

        except Exception as e:
            print("ERROR:", e)

        await browser.close()


asyncio.run(main())