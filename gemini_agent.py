#!/usr/bin/env python3
"""Interactive Gemini agent that uses the Restaurant EV Charging API as a tool."""
from __future__ import annotations

import json
import os
import pathlib
import sys

from google import genai
from google.genai import types
import httpx

_INSTRUCTIONS_PATH = pathlib.Path(__file__).parent / "gemini_instructions.md"

API_URL = os.getenv("RESTAURANT_EV_API_URL", "http://127.0.0.1:8000")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

_TOOLS = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="geocode_address",
            description="Convert a street address or place name into latitude and longitude coordinates.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "address": types.Schema(
                        type=types.Type.STRING,
                        description="The street address or place name to geocode.",
                    ),
                },
                required=["address"],
            ),
        ),
        types.FunctionDeclaration(
            name="find_dining_chargers",
            description=(
                "Find restaurants located near DC fast EV chargers at a given location. "
                "Returns matched restaurant–charger pairs with straight-line distances. "
                "Requires latitude and longitude — use geocode_address first if you only have an address."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "latitude": types.Schema(
                        type=types.Type.NUMBER,
                        description="Latitude of the search centre (-90 to 90).",
                    ),
                    "longitude": types.Schema(
                        type=types.Type.NUMBER,
                        description="Longitude of the search centre (-180 to 180).",
                    ),
                    "radius_km": types.Schema(
                        type=types.Type.NUMBER,
                        description="Search radius for chargers in km (default 10, max 100).",
                    ),
                    "restaurant_radius_m": types.Schema(
                        type=types.Type.INTEGER,
                        description="Max metres between a charger and a restaurant (default 500, max 2000).",
                    ),
                    "nacs": types.Schema(
                        type=types.Type.BOOLEAN,
                        description="Include NACS/Tesla connectors (default true).",
                    ),
                    "ccs": types.Schema(
                        type=types.Type.BOOLEAN,
                        description="Include CCS connectors (default true).",
                    ),
                    "l2": types.Schema(
                        type=types.Type.BOOLEAN,
                        description="Include Level 2 AC chargers (default false). L2 chargers (7–22 kW) are too slow for a typical dining stop — only use this for overnight stays or when the user specifically asks for L2.",
                    ),
                    "tesla_only": types.Schema(
                        type=types.Type.BOOLEAN,
                        description="Only include Tesla-designated stations (default false).",
                    ),
                },
                required=["latitude", "longitude"],
            ),
        ),
    ]
)


def _geocode(address: str, geoapify_key: str) -> dict:
    try:
        response = httpx.get(
            "https://api.geoapify.com/v1/geocode/search",
            params={"text": address, "format": "json", "limit": 1, "apiKey": geoapify_key},
            timeout=20,
        )
        response.raise_for_status()
        results = response.json().get("results", [])
        if not results:
            return {"error": f"No results found for address: {address!r}"}
        result = results[0]
        return {
            "latitude": result["lat"],
            "longitude": result["lon"],
            "formatted_address": result.get("formatted", address),
        }
    except httpx.HTTPError as e:
        return {"error": f"Geocoding service error: {e}"}


def _find_dining_chargers(args: dict) -> dict:
    try:
        response = httpx.post(
            f"{API_URL}/find-dining-chargers",
            json=args,
            timeout=60,
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as e:
        return {"error": f"Search service error: {e}"}


def main() -> None:
    gemini_key = os.getenv("GEMINI_API_KEY", "")
    if not gemini_key:
        sys.exit("GEMINI_API_KEY is not set. Add it to setup.env and restart.")

    geoapify_key = os.getenv("GEOAPIFY_API_KEY", "")
    if not geoapify_key:
        sys.exit("GEOAPIFY_API_KEY is not set. Add it to setup.env and restart.")

    client = genai.Client(api_key=gemini_key)
    system_instruction = _INSTRUCTIONS_PATH.read_text()
    chat = client.chats.create(
        model=GEMINI_MODEL,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=[_TOOLS],
        ),
    )

    print(f"Gemini EV dining agent ({GEMINI_MODEL}). Ask about EV chargers and nearby restaurants.")
    print("Type 'quit' to exit.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue
        if user_input.lower() in {"quit", "exit"}:
            break

        try:
            response = chat.send_message(user_input)

            # Handle function calls — loop until Gemini returns a text response
            while True:
                fc_part = next(
                    (p for p in response.candidates[0].content.parts if p.function_call),
                    None,
                )
                if fc_part is None:
                    break

                fc = fc_part.function_call
                args = dict(fc.args)

                if fc.name == "geocode_address":
                    print(f"  [→ geocode_address({json.dumps(args)})]")
                    result = _geocode(args["address"], geoapify_key)
                else:
                    print(f"  [→ find_dining_chargers({json.dumps(args)})]")
                    result = _find_dining_chargers(args)

                response = chat.send_message(
                    types.Part.from_function_response(name=fc.name, response=result)
                )

            print(f"Gemini: {response.text}\n")
        except Exception as e:
            print(f"  [!] Error: {e}\n")


if __name__ == "__main__":
    main()
