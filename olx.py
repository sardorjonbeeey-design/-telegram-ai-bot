import os
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"

from playwright.async_api import async_playwright


async def search_olx(product):
    print("=" * 50)
    print("SEARCH_OLX CALLED:", product)

    query = product.replace(" ", "-")
    url = f"https://www.olx.uz/oz/q-{query}/"

    print("URL:", url)

    results = []

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        page = await browser.new_page(
            viewport={"width": 1280, "height": 720},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )

        try:
            print("Opening OLX...")

            response = await page.goto(
                url,
                wait_until="networkidle",
                timeout=60000,
            )

            print("STATUS:", response.status if response else None)
            print("TITLE:", await page.title())

            # Wait for listings
            await page.wait_for_selector(
                '[data-cy="l-card"]',
                timeout=15000
            )

            cards = await page.query_selector_all(
                '[data-cy="l-card"]'
            )

            print("CARDS FOUND:", len(cards))

            for card in cards[:5]:

                try:
                    link = await card.query_selector("a[href]")
                    if not link:
                        continue

                    href = await link.get_attribute("href")

                    if href and not href.startswith("http"):
                        href = "https://www.olx.uz" + href

                    title = await link.inner_text()

                    price_element = await card.query_selector(
                        '[data-testid="ad-price"]'
                    )

                    price = (
                        await price_element.inner_text()
                        if price_element
                        else "Narx ko'rsatilmagan"
                    )

                    location_element = await card.query_selector(
                        '[data-testid="location-date"]'
                    )

                    location = (
                        await location_element.inner_text()
                        if location_element
                        else ""
                    )

                    results.append({
                        "title": title.strip()[:80],
                        "price": price.strip(),
                        "location": location.strip(),
                        "url": href
                    })

                except Exception as e:
                    print("CARD ERROR:", e)

        except Exception as e:
            print("OLX ERROR:", e)

        finally:
            await browser.close()


    print("RESULTS FOUND:", len(results))
    print(results)

    return results[:5]