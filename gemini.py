import json
import logging
import re

from google import genai
from google.genai import types

from config import GEMINI_API_KEY

log = logging.getLogger(__name__)

client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = """
You are an AI assistant for a marketplace bot in Uzbekistan.

Your job:
1. Detect the user's language.
2. Understand the user's intention.
3. Extract the product name if there is one.

Supported languages:
- Uzbek
- Russian

Intent values:
- buy
- sell
- chat

Meaning:
- buy: user wants to find or buy a product.
- sell: user wants to sell a product.
- chat: greetings, thanks, simple conversation, or messages without buying/selling intention.

Examples:

User: "Salom"
{
  "intent": "chat",
  "product": "",
  "language": "uz"
}

User: "Привет"
{
  "intent": "chat",
  "product": "",
  "language": "ru"
}

User: "iPhone 15 kerak"
{
  "intent": "buy",
  "product": "iPhone 15",
  "language": "uz"
}

User: "Продам Samsung S24"
{
  "intent": "sell",
  "product": "Samsung S24",
  "language": "ru"
}

Rules:
- Return ONLY valid JSON.
- No markdown.
- No explanations.
- No extra keys.
- Product must not contain words like buy, sell, kerak, sotaman, куплю, продам.
- If there is no product, use "".
- Never use unknown.
"""

MODEL = "gemini-3.1-flash-lite"


async def parse_message(text: str) -> dict:
    try:
        response = client.models.generate_content(
            model=MODEL,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0,
                response_mime_type="application/json",
            ),
            contents=text,
        )

        raw = response.text.strip()

        raw = re.sub(r"^```json", "", raw)
        raw = re.sub(r"^```", "", raw)
        raw = re.sub(r"```$", "", raw)
        raw = raw.strip()

        data = json.loads(raw)

        return {
            "intent": data.get("intent", "unknown"),
            "product": data.get("product", "").strip(),
            "language": data.get("language", "uz"),
        }

    except Exception as e:
        log.exception(e)

        return {
            "intent": "unknown",
            "product": "",
            "language": "uz",
        }