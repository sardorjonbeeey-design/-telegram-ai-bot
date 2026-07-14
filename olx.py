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
            "AppleWebKit/537.36 Chrome/120 Safari/537.36"
        )
    }

    results = []

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=15) as response:

                if response.status != 200:
                    return []

                html = await response.text()

        soup = BeautifulSoup(html, "lxml")

        cards = soup.select("div[data-cy='l-card']")

        for card in cards[:10]:

            try:
                title_element = card.select_one("h4")

                if not title_element:
                    continue

                title = title_element.get_text(strip=True)


                price_element = card.select_one(
                    "p[data-testid='ad-price']"
                )

                price = (
                    price_element.get_text(strip=True)
                    if price_element
                    else "Narx ko'rsatilmagan"
                )


                location_element = card.select_one(
                    "p[data-testid='location-date']"
                )

                item_location = (
                    location_element.get_text(" ", strip=True)
                    if location_element
                    else location
                )


                link_element = card.find("a", href=True)

                link = ""

                if link_element:
                    link = link_element["href"]

                    if link.startswith("/"):
                        link = BASE_URL + link


                results.append(
                    {
                        "title": title,
                        "price": price,
                        "location": item_location,
                        "url": link
                    }
                )


            except Exception:
                continue


    except Exception:
        return []


    return results