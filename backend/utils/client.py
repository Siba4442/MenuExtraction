import os
from openai import AsyncOpenAI
from typing import Literal
from dotenv import load_dotenv

load_dotenv(override=True)

def get_client(service: Literal["Gemini", "Groq", "OpenRouter"]) -> AsyncOpenAI:

    if service == "Gemini":
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("API key for Gemini not found in environment variables.")
        return AsyncOpenAI(
            api_key=api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )

    elif service == "Groq":
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError("API key for Groq not found in environment variables.")
        return AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1",
        )

    elif service == "OpenRouter":
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("API key for OpenRouter not found in environment variables.")
        return AsyncOpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
        )

    else:
        raise ValueError(f"Unsupported service: {service}")
