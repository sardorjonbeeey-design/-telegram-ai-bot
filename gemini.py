import json
import logging
import re

from google import genai
from google.genai import types

from config import GEMINI_API_KEY

log = logging.getLogger(__name__)

client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = """
You are an AI that extracts structured data from user messages.

Supported languages:
- Uzbek
- Russian

Your task:
1. Detect the language.
2. Detect the user's intent.
3. Extract only the product name.

Intent values:
- buy
- sell
- unknown

Language values:
- uz
- ru

Return ONLY valid JSON.

Schema:
{
  "intent": "buy",
  "product": "iPhone 13",
  "language": "uz"
}

Rules:
- No markdown.
- No explanations.
- No extra keys.
- Product must not contain words like buy, sell, kerak, sotaman, куплю, продам.
- If no product exists use "".
- If intent is unclear use "unknown".
"""

MODEL = "gemini-2.5-flash-lite"


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