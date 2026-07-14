import aiohttp
from bs4 import BeautifulSoup
from urllib.parse import quote


BASE_URL = "https://www.olx.uz"


async def search_listings(product: str, location: str):
    query = quote(product)

    url = f"{BASE_URL}/list/q-{query}/"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/137.0 Safari/537.36"
        )
    }

    results = []

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=20) as response:
                if response.status != 200:
                    return []

                html = await response.text()

        soup = BeautifulSoup(html, "lxml")

        cards = soup.select("div[data-cy='l-card']")

        for card in cards[:10]:
            try:
                title = card.select_one("h4").get_text(strip=True)

                price_tag = card.select_one("p[data-testid='ad-price']")
                price = (
                    price_tag.get_text(strip=True)
                    if price_tag
                    else "Narx ko'rsatilmagan"
                )

                location_tag = card.select_one("p[data-testid='location-date']")
                place = (
                    location_tag.get_text(" ", strip=True)
                    if location_tag
                    else location
                )

                link = card.find("a", href=True)

                if link:
                    href = link["href"]

                    if href.startswith("/"):
                        href = BASE_URL + href
                else:
                    href = ""

                if location.lower() not in place.lower():
                    continue

                results.append(
                    {
                        "title": title,
                        "price": price,
                        "location": place,
                        "url": href,
                    }
                )

            except Exception:
                continue

    except Exception:
        return []

    return results