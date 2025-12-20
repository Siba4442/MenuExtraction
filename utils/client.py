import os
from openai import AsyncOpenAI
from typing import Literal
from dotenv import load_dotenv

load_dotenv(override=True)

def get_client(service: Literal["Groq", "OpenRouter"]) -> AsyncOpenAI:
    
    if service == "Groq":
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError(f"API key for {service} not found in environment variables.")
        return AsyncOpenAI(base_url="https://api.groq.com/openai/v1",api_key=api_key)
    elif service == "OpenRouter":
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError(f"API key for {service} not found in environment variables.")
        return AsyncOpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
    else:    
        raise ValueError(f"Unsupported service: {service}")