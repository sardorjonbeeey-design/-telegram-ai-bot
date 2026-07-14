import aiohttp
from bs4 import BeautifulSoup


async def search_olx(product):
    url = f"https://www.olx.uz/d/obyavleniya/q-{product.replace(' ', '-')}/"

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(url) as response:
            html = await response.text()

    soup = BeautifulSoup(html, "html.parser")

    results = []

    for link in soup.find_all("a", href=True):
        title = link.get_text(" ", strip=True)

        if len(title) > 10 and "/d/obyavleniya/" in link["href"]:
            results.append({
                "title": title[:80],
                "url": "https://www.olx.uz" + link["href"]
            })

    return results[:5]