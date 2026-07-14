from playwright.async_api import async_playwright


async def search_olx(product):

    print("=" * 50)
    print("PLAYWRIGHT OLX SEARCH:", product)

    query = product.replace(" ", "-")
    url = f"https://www.olx.uz/oz/q-{query}/"

    print("URL:", url)

    results = []

    try:
        async with async_playwright() as p:

            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage"
                ]
            )

            page = await browser.new_page(
                viewport={
                    "width": 1280,
                    "height": 900
                },
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 "
                    "Chrome/137 Safari/537.36"
                )
            )

            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=60000
            )

            print("PAGE TITLE:", await page.title())

            await page.wait_for_timeout(3000)

            cards = await page.query_selector_all(
                '[data-cy="l-card"]'
            )

            print("CARDS FOUND:", len(cards))


            for card in cards[:5]:

                title_el = await card.query_selector(
                    "h6"
                )

                title = (
                    await title_el.inner_text()
                    if title_el
                    else "No title"
                )

                link_el = await card.query_selector(
                    "a"
                )

                href = (
                    await link_el.get_attribute("href")
                    if link_el
                    else None
                )

                if href and href.startswith("/"):
                    href = "https://www.olx.uz" + href


                results.append({
                    "title": title,
                    "price": "N/A",
                    "location": "OLX",
                    "url": href
                })


            await browser.close()


        print("RESULTS:", results)

        return results


    except Exception as e:

        print("PLAYWRIGHT ERROR:", e)

        return []