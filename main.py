import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from utils.client import get_client
from utils.processing import convert_pdf_into_images, to_dict
from utils.model import Categories, CategorywithItems, CategoryBase, CategoryItemAddons


# ----------------------------
# Config
# ----------------------------
PDF_PATH = "Amici-Dinner-Menu-June-2025.pdf"
OUTPUT_DIR = Path("outputs")
OUTPUT_DIR.mkdir(exist_ok=True)

CATEGORIES_FILE = OUTPUT_DIR / "categories_all_pages.json"
ITEMS_FILE = OUTPUT_DIR / "category_items_all_pages.json"
CATEGORY_BASES_FILE = OUTPUT_DIR / "category_bases_all_pages.json"
ITEMS_ADDONS_FILE = OUTPUT_DIR / "category_items_addons_all_pages.json"

MODEL = os.getenv("OPENROUTER_DEFAULT_MODEL")

# Limit concurrency to avoid rate limits/timeouts
MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY", "4"))

client = get_client("OpenRouter")

env = Environment(
    loader=FileSystemLoader("prompts"),
    undefined=StrictUndefined,
    autoescape=False,
)


# ----------------------------
# Helpers
# ----------------------------
def render_prompt(template_name: str, **variables) -> str:
    return env.get_template(template_name).render(**variables)


def phase1_prompt(restaurant_name: str, page_number: int) -> str:
    return render_prompt(
        "phase1.j2",
        restaurant_name=restaurant_name,
        page_number=page_number,
    )


def phase2_prompt(restaurant_name: str, page_number: int, category: Any) -> str:
    """
    category is one CategoryRef (a single category object)
    """
    return render_prompt(
        "phase2.j2",
        restaurant_name=restaurant_name,
        page_number=page_number,
        # include only that category, not the full Categories object
        categories=json.dumps(to_dict(category), indent=2),
    )

def phase3_prompt(restaurant_name: str, page_number: int, category: Any) -> str:
    """
    category_base is one CategoryBase (a single category base object)
    """
    return render_prompt(
        "phase3.j2",
        restaurant_name=restaurant_name,
        page_number=page_number,
        # include only that category base, not the full list
        category=json.dumps(to_dict(category), indent=2),
    )

def phase4_prompt(restaurant_name: str, page_number: int, category: Any, category_base: Any) -> str:
    """
    category_base is one CategoryBase (a single category base object)
    """
    return render_prompt(
        "phase4.j2",
        restaurant_name=restaurant_name,
        page_number=page_number,
        # include only that category base, not the full list
        category=json.dumps(to_dict(category), indent=2),
        category_base=json.dumps(to_dict(category_base), indent=2),
    )


def json_schema_format(model_cls) -> Dict[str, Any]:
    # OpenRouter chat.completions JSON schema format
    return {
        "type": "json_schema",
        "json_schema": {
            "name": model_cls.__name__,
            "schema": model_cls.model_json_schema(),
            "strict": True,
        },
    }


async def call_openrouter(message_content: List[dict], response_format: dict):
    """
    One OpenRouter call. message_content is the multimodal list:
    [{"type":"text"...}, {"type":"image_url"...}]
    """
    return await client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": message_content}],
        response_format=response_format,
    )


async def bounded_gather(coros: List, limit: int):
    """
    Run coroutines with a concurrency limit (good for production).
    """
    sem = asyncio.Semaphore(limit)

    async def _run(coro):
        async with sem:
            return await coro

    return await asyncio.gather(*(_run(c) for c in coros))


# ----------------------------
# Phase 1
# ----------------------------
async def run_phase1(restaurant_name: str) -> Dict[str, Any]:
    images = await convert_pdf_into_images(PDF_PATH)

    coros = []
    fmt = json_schema_format(Categories)

    for page_idx, img_b64 in enumerate(images, start=1):
        prompt = phase1_prompt(restaurant_name, page_idx)
        message_content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
        ]
        coros.append(call_openrouter(message_content, fmt))

    responses = await bounded_gather(coros, MAX_CONCURRENCY)

    pages: List[Dict[str, Any]] = []
    for page_idx, resp in enumerate(responses, start=1):
        data = json.loads(resp.choices[0].message.content)
        validated = Categories.model_validate(data)

        pages.append({
            "page_number": page_idx,
            "data": validated.model_dump(),
        })

    output = {"restaurant_name": restaurant_name, "pages": pages}

    CATEGORIES_FILE.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    return output


# ----------------------------
# Phase 2
# ----------------------------
async def run_phase2(restaurant_name: str, categories_payload: Dict[str, Any]) -> Dict[str, Any]:
    images = await convert_pdf_into_images(PDF_PATH)

    # sanity check: images count should match pages count
    pages = categories_payload["pages"]

    all_pages_out: List[Dict[str, Any]] = []
    fmt = json_schema_format(CategorywithItems)

    for page in pages:
        page_number = page["page_number"]
        img_b64 = images[page_number - 1]

        # Validate the page categories
        page_categories = Categories.model_validate(page["data"])

        # One request per category for THIS page (your requirement)
        coros = []
        for cat in page_categories.categories:
            prompt = phase2_prompt(restaurant_name, page_number, cat)
            message_content = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
            ]
            coros.append(call_openrouter(message_content, fmt))

        # Run category calls concurrently (bounded)
        responses = await bounded_gather(coros, MAX_CONCURRENCY)

        # Validate each category response
        category_results = []
        for resp in responses:
            raw = resp.choices[0].message.content
            obj = json.loads(raw)
            validated = CategorywithItems.model_validate(obj)
            category_results.append(validated.model_dump())

        all_pages_out.append({
            "page_number": page_number,
            "categories": category_results,
        })

    output = {
        "restaurant_name": restaurant_name,
        "pages": all_pages_out,
    }

    ITEMS_FILE.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    return output


async def run_phase3(restaurant_name: str, category_bases_payload: Dict[str, Any]) -> Dict[str, Any]:
    images = await convert_pdf_into_images(PDF_PATH)

    pages = category_bases_payload["pages"]
    
    all_pages_out: List[Dict[str, Any]] = []
    fmt = json_schema_format(CategoryBase)

    for page in pages:
        page_number = page["page_number"]
        img_b64 = images[page_number - 1]

        page_categories = page["categories"]
        
        coros = []
        for cat in page_categories:
            cat = CategorywithItems.model_validate(cat)
            category_base = {
                "category": cat
            }
            prompt = phase3_prompt(restaurant_name, page_number, category_base)
            message_content = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
            ]
            coros.append(call_openrouter(message_content, fmt))
        
        responses = await bounded_gather(coros, MAX_CONCURRENCY)

        # Validate each category response
        category_results = []
        for resp in responses:
            raw = resp.choices[0].message.content
            obj = json.loads(raw)
            validated = CategoryBase.model_validate(obj)
            category_results.append(validated.model_dump())

        all_pages_out.append({
            "page_number": page_number,
            "categories": category_results,
        })

    output = {
        "restaurant_name": restaurant_name,
        "pages": all_pages_out,
    }

    CATEGORY_BASES_FILE.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    return output


async def run_phase4(restaurant_name: str, category_payload: Dict[str, Any], category_base_payload: Dict[str, Any]) -> Dict[str, Any]:
    images = await convert_pdf_into_images(PDF_PATH)

    pages = category_payload["pages"]
    pages_base = category_base_payload["pages"]
    
    all_pages_out: List[Dict[str, Any]] = []
    fmt = json_schema_format(CategoryItemAddons)

    for page, page_base in zip(pages, pages_base):
        page_number = page["page_number"]
        img_b64 = images[page_number - 1]

        page_categories = page["categories"]
        page_bases = page_base["categories"]
        
        coros = []
        for cat, cat_base in zip(page_categories, page_bases):
            cat = CategorywithItems.model_validate(cat)
            cat_base = CategoryBase.model_validate(cat_base)
            category = {
                "category": cat
            }
            category_base = {
                "category_base": cat_base
            }
            prompt = phase4_prompt(restaurant_name, page_number, category, category_base)
            message_content = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
            ]
            coros.append(call_openrouter(message_content, fmt))
        
        responses = await bounded_gather(coros, MAX_CONCURRENCY)

        # Validate each category response
        category_results = []
        for resp in responses:
            raw = resp.choices[0].message.content
            obj = json.loads(raw)
            validated = CategoryItemAddons.model_validate(obj)
            category_results.append(validated.model_dump())

        all_pages_out.append({
            "page_number": page_number,
            "categories": category_results,
        })

    output = {
        "restaurant_name": restaurant_name,
        "pages": all_pages_out,
    }

    ITEMS_ADDONS_FILE.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    return output


# ----------------------------
# CLI
# ----------------------------
def load_categories_file() -> Dict[str, Any]:
    return json.loads(CATEGORIES_FILE.read_text(encoding="utf-8"))


def load_items_file() -> Dict[str, Any]:
    return json.loads(ITEMS_FILE.read_text(encoding="utf-8"))


def load_category_bases_file() -> Dict[str, Any]:
    return json.loads(CATEGORY_BASES_FILE.read_text(encoding="utf-8"))


def _normalize(name: Optional[str]) -> str:
    return (name or "").strip().lower()




async def main():
    restaurant_name = input("Enter the restaurant name: ").strip()
    phase = int(input("Enter the phase (1 or 2): ").strip())

    if phase == 1:
        result = await run_phase1(restaurant_name)
        print(f"Saved: {CATEGORIES_FILE}")
        # optional: print summary
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif phase == 2:
        categories_payload = load_categories_file()
        result = await run_phase2(restaurant_name, categories_payload)
        print(f"Saved: {ITEMS_FILE}")
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif phase == 3:
        items_payload = load_items_file()
        result = await run_phase3(restaurant_name, items_payload)
        print(f"Saved: {CATEGORY_BASES_FILE}")
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif phase == 4:
        category_payload = load_items_file()
        category_bases_payload = load_category_bases_file()

        result = await run_phase4(restaurant_name, category_payload, category_bases_payload)
        print(f"Saved: {ITEMS_ADDONS_FILE}")
        print(json.dumps(result, indent=2, ensure_ascii=False))

    else:
        raise ValueError("Phase must be 1, 2, 3, or 4.")

if __name__ == "__main__":
    asyncio.run(main())
