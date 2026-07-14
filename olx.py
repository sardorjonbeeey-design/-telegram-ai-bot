import aiohttp
from bs4 import BeautifulSoup


async def search_olx(product):
    query = product.replace(" ", "-")
    url = f"https://www.olx.uz/oz/q-{query}/"

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(url) as response:
            html = await response.text()
            print("STATUS:", response.status)
            print(html[:500])

    soup = BeautifulSoup(html, "html.parser")

    results = []

    cards = soup.select('[data-cy="l-card"]')

    for card in cards:
        link_tag = card.find("a", href=True)
        if not link_tag:
            continue

        href = link_tag["href"]
        if not href.startswith("http"):
            href = "https://www.olx.uz" + href

        title_tag = card.find(["h4", "h6"])
        title = title_tag.get_text(" ", strip=True) if title_tag else link_tag.get_text(" ", strip=True)

        price_tag = card.select_one('[data-testid="ad-price"]')
        price = price_tag.get_text(" ", strip=True) if price_tag else "Narx ko'rsatilmagan"

        location_tag = card.select_one('[data-testid="location-date"]')
        location = location_tag.get_text(" ", strip=True) if location_tag else ""

        if len(title) > 5:
            results.append({
                "title": title[:80],
                "price": price,
                "location": location,
                "url": href
            })

    return results[:5]
