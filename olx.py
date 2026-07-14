import aiohttp
from bs4 import BeautifulSoup
import asyncio


async def search_olx(product):
    print("=" * 50)
    print("SEARCH START:", product)

    query = product.replace(" ", "-")
    url = f"https://www.olx.uz/oz/q-{query}/"

    print("URL:", url)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/137 Safari/537.36"
        ),
        "Accept-Language": "uz-UZ,uz;q=0.9"
    }

    try:
        async with aiohttp.ClientSession(headers=headers) as session:

            async with session.get(url, timeout=30) as response:

                print("STATUS:", response.status)

                html = await response.text()

                print("HTML SIZE:", len(html))
                print(html[:300])


        soup = BeautifulSoup(html, "html.parser")

        cards = soup.select('[data-cy="l-card"]')

        print("CARDS:", len(cards))


        results = []

        for card in cards[:5]:

            title = card.get_text(" ", strip=True)

            link = card.find("a", href=True)

            if link:
                href = link["href"]

                if href.startswith("/"):
                    href = "https://www.olx.uz" + href

                results.append({
                    "title": title[:100],
                    "url": href
                })


        print("RESULTS:", results)

        return results


    except Exception as e:
        print("OLX ERROR:", e)
        return []