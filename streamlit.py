import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import streamlit as st
from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from utils.client import get_client
from utils.model import (
    Categories,
    CategorywithItems,
    CategoryBase,
    CategoryItemAddons,
)
from utils.processing import convert_pdf_into_images, to_dict

# Load environment variables
load_dotenv(override=True)


# ----------------------------
# Configuration
# ----------------------------
OUTPUT_DIR = Path("outputs")
OUTPUT_DIR.mkdir(exist_ok=True)

env = Environment(
    loader=FileSystemLoader("prompts"),
    undefined=StrictUndefined,
    autoescape=False,
)

MAX_CONCURRENCY = 4


# ----------------------------
# Helper Functions
# ----------------------------
def render_prompt(template_name: str, **variables) -> str:
    """Render a Jinja2 template with given variables."""
    return env.get_template(template_name).render(**variables)


def json_schema_format(model_cls) -> Dict[str, Any]:
    """Format for OpenRouter chat.completions JSON schema."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": model_cls.__name__,
            "schema": model_cls.model_json_schema(),
            "strict": True,
        },
    }


async def call_llm(client, model: str, message_content: List[dict], response_format: dict):
    """Make an async LLM call with the given message and format."""
    return await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": message_content}],
        response_format=response_format,
    )


async def bounded_gather(coros: List, limit: int):
    """Run coroutines with a concurrency limit."""
    sem = asyncio.Semaphore(limit)

    async def _run(coro):
        async with sem:
            return await coro

    return await asyncio.gather(*(_run(c) for c in coros))


# ----------------------------
# Phase Execution Functions
# ----------------------------
async def execute_phase1(
    service: Literal["Groq", "OpenRouter"],
    model: str,
    restaurant_name: str,
    base64_images: List[str],
) -> Dict[str, Any]:
    """Execute Phase 1: Category extraction."""
    client = get_client(service)
    fmt = json_schema_format(Categories)

    coros = []
    for page_idx, img_b64 in enumerate(base64_images, start=1):
        prompt = render_prompt(
            "phase1.j2",
            restaurant_name=restaurant_name,
            page_number=page_idx,
        )
        message_content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
        ]
        coros.append(call_llm(client, model, message_content, fmt))

    responses = await bounded_gather(coros, MAX_CONCURRENCY)

    pages: List[Dict[str, Any]] = []
    for page_idx, resp in enumerate(responses, start=1):
        try:
            raw = resp.choices[0].message.content
            data = json.loads(raw)
            validated = Categories.model_validate(data)
            pages.append({
                "page_number": page_idx,
                "data": validated.model_dump(),
            })
        except json.JSONDecodeError as e:
            st.error(f"❌ JSON parsing error on page {page_idx}: {e}")
            st.error(f"Raw response: {raw[:500]}...")
            raise
        except Exception as e:
            st.error(f"❌ Validation error on page {page_idx}: {e}")
            raise

    return {"restaurant_name": restaurant_name, "pages": pages}


async def execute_phase2(
    service: Literal["Groq", "OpenRouter"],
    model: str,
    restaurant_name: str,
    base64_images: List[str],
    categories_payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Execute Phase 2: Item extraction."""
    client = get_client(service)
    fmt = json_schema_format(CategorywithItems)

    pages = categories_payload["pages"]
    all_pages_out: List[Dict[str, Any]] = []

    for page in pages:
        page_number = page["page_number"]
        img_b64 = base64_images[page_number - 1]

        page_categories = Categories.model_validate(page["data"])

        coros = []
        for cat in page_categories.categories:
            prompt = render_prompt(
                "phase2.j2",
                restaurant_name=restaurant_name,
                page_number=page_number,
                categories=json.dumps(to_dict(cat), indent=2),
            )
            message_content = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
            ]
            coros.append(call_llm(client, model, message_content, fmt))

        responses = await bounded_gather(coros, MAX_CONCURRENCY)

        category_results = []
        for cat_idx, resp in enumerate(responses):
            try:
                raw = resp.choices[0].message.content
                obj = json.loads(raw)
                validated = CategorywithItems.model_validate(obj)
                category_results.append(validated.model_dump())
            except json.JSONDecodeError as e:
                st.error(f"❌ JSON parsing error on page {page_number}, category {cat_idx + 1}: {e}")
                st.error(f"Raw response preview: {raw[:500]}...")
                with st.expander("View full raw response"):
                    st.code(raw, language="text")
                raise
            except Exception as e:
                st.error(f"❌ Validation error on page {page_number}, category {cat_idx + 1}: {e}")
                raise

        all_pages_out.append({
            "page_number": page_number,
            "categories": category_results,
        })

    return {"restaurant_name": restaurant_name, "pages": all_pages_out}


async def execute_phase3(
    service: Literal["Groq", "OpenRouter"],
    model: str,
    restaurant_name: str,
    base64_images: List[str],
    items_payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Execute Phase 3: Base options extraction."""
    client = get_client(service)
    fmt = json_schema_format(CategoryBase)

    pages = items_payload["pages"]
    all_pages_out: List[Dict[str, Any]] = []

    for page in pages:
        page_number = page["page_number"]
        img_b64 = base64_images[page_number - 1]

        page_categories = page["categories"]

        coros = []
        for cat in page_categories:
            cat_validated = CategorywithItems.model_validate(cat)
            category_base = {"category": cat_validated}
            prompt = render_prompt(
                "phase3.j2",
                restaurant_name=restaurant_name,
                page_number=page_number,
                category=json.dumps(to_dict(category_base), indent=2),
            )
            message_content = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
            ]
            coros.append(call_llm(client, model, message_content, fmt))

        responses = await bounded_gather(coros, MAX_CONCURRENCY)

        category_results = []
        for cat_idx, resp in enumerate(responses):
            try:
                raw = resp.choices[0].message.content
                obj = json.loads(raw)
                validated = CategoryBase.model_validate(obj)
                category_results.append(validated.model_dump())
            except json.JSONDecodeError as e:
                st.error(f"❌ JSON parsing error on page {page_number}, category {cat_idx + 1}: {e}")
                st.error(f"Raw response preview: {raw[:500]}...")
                with st.expander("View full raw response"):
                    st.code(raw, language="text")
                raise
            except Exception as e:
                st.error(f"❌ Validation error on page {page_number}, category {cat_idx + 1}: {e}")
                raise

        all_pages_out.append({
            "page_number": page_number,
            "categories": category_results,
        })

    return {"restaurant_name": restaurant_name, "pages": all_pages_out}


async def execute_phase4(
    service: Literal["Groq", "OpenRouter"],
    model: str,
    restaurant_name: str,
    base64_images: List[str],
    items_payload: Dict[str, Any],
    category_bases_payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Execute Phase 4: Addons extraction."""
    client = get_client(service)
    fmt = json_schema_format(CategoryItemAddons)

    pages = items_payload["pages"]
    pages_base = category_bases_payload["pages"]
    all_pages_out: List[Dict[str, Any]] = []

    for page, page_base in zip(pages, pages_base):
        page_number = page["page_number"]
        img_b64 = base64_images[page_number - 1]

        page_categories = page["categories"]
        page_bases = page_base["categories"]

        coros = []
        for cat, cat_base in zip(page_categories, page_bases):
            cat_validated = CategorywithItems.model_validate(cat)
            cat_base_validated = CategoryBase.model_validate(cat_base)
            category = {"category": cat_validated}
            category_base = {"category_base": cat_base_validated}
            prompt = render_prompt(
                "phase4.j2",
                restaurant_name=restaurant_name,
                page_number=page_number,
                category=json.dumps(to_dict(category), indent=2),
                category_base=json.dumps(to_dict(category_base), indent=2),
            )
            message_content = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
            ]
            coros.append(call_llm(client, model, message_content, fmt))

        responses = await bounded_gather(coros, MAX_CONCURRENCY)

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

    return {"restaurant_name": restaurant_name, "pages": all_pages_out}


# ----------------------------
# UI Helper Functions
# ----------------------------
def _format_price(price_obj):
    """Format price object or value for display."""
    if price_obj is None:
        return None
    if isinstance(price_obj, dict):
        amount = price_obj.get("amount")
        currency = price_obj.get("currency", "$")
        if amount is not None:
            return f"{currency}{amount:,.2f}".replace(".00", "")
        return None
    if isinstance(price_obj, (int, float)):
        return f"${price_obj:,.2f}".replace(".00", "")
    return str(price_obj)


def _editable_json(label: str, session_key: str, downstream_keys=None):
    """Render a JSON text editor and apply changes into session state."""
    downstream_keys = downstream_keys or []
    data = st.session_state.get(session_key)
    if data is None:
        st.info("Nothing to edit yet.")
        return None

    default_text = json.dumps(data, indent=2, ensure_ascii=False)
    text = st.text_area(label, value=default_text, height=240, key=f"editor_{session_key}")
    if st.button(f"Apply changes to {label}", key=f"apply_{session_key}"):
        try:
            parsed = json.loads(text)
            st.session_state[session_key] = parsed
            for k in downstream_keys:
                st.session_state.pop(k, None)
            st.success("Saved changes. Downstream phases cleared if they depended on this.")
        except Exception as e:
            st.error(f"Invalid JSON: {e}")
    return st.session_state.get(session_key)


# ----------------------------
# Rendering Functions
# ----------------------------
def _render_phase1(categories_data: Dict[str, Any]):
    """Render Phase 1 results: Categories."""
    if not categories_data or not categories_data.get("pages"):
        st.info("No results yet. Run Phase 1 after uploading the PDF.")
        return

    pages = categories_data["pages"]
    labels = [f"Page {p.get('page_number', '?')}" for p in pages]
    tabs = st.tabs(labels)

    for tab, page in zip(tabs, pages):
        with tab:
            with st.container(border=True):
                st.markdown(f"**Page {page.get('page_number', '?')}**")
                categories = page.get("data", {}).get("categories", [])
                if categories:
                    for cat in categories:
                        cat_name = cat.get("name_raw", "Unknown category")
                        st.markdown(f"### {cat_name}")
                        subcats = cat.get("subcategories", [])
                        if subcats:
                            st.caption("**Subcategories:**")
                            for subcat in subcats:
                                st.markdown(f"  • {subcat.get('name_raw', 'Unknown subcategory')}")
                        else:
                            st.caption("  ℹ️ No subcategories found")
                else:
                    st.caption("No categories found for this page.")


def _render_phase2(items_data: Dict[str, Any]):
    """Render Phase 2 results: Items."""
    if not items_data or not items_data.get("pages"):
        st.info("No results yet. Run Phase 2 after Phase 1.")
        return

    pages = items_data["pages"]
    labels = [f"Page {p.get('page_number', '?')}" for p in pages]
    tabs = st.tabs(labels)

    for tab, page in zip(tabs, pages):
        with tab:
            with st.container(border=True):
                st.markdown(f"**Page {page.get('page_number', '?')}**")
                categories = page.get("categories", [])
                for cat in categories:
                    cat_name = cat.get("name_raw", "Unknown category")
                    st.markdown(f"### {cat_name}")

                    # Category items (directly under category)
                    cat_items = cat.get("category_items", [])
                    for cat_item_block in cat_items:
                        items = cat_item_block.get("items", [])
                        desc = cat_item_block.get("description_raw")
                        if desc:
                            st.info(f"ℹ️ {desc}")
                        if items:
                            st.caption("**Items:**")
                        for item in items:
                            _render_item(item)

                    # Subcategory items
                    subcat_items = cat.get("subcategory_items", [])
                    for subcat in subcat_items:
                        subcat_name = subcat.get("name_raw", "Unknown subcategory")
                        st.markdown(f"  #### {subcat_name}")
                        desc = subcat.get("description_raw")
                        if desc:
                            st.caption(f"  ℹ️ {desc}")
                        items = subcat.get("items", [])
                        if items:
                            st.caption("  **Items:**")
                        for item in items:
                            _render_item(item, indent="  ")

                    note = cat.get("note")
                    if note:
                        st.info(f"📝 Note: {note}")


def _render_item(item: Dict[str, Any], indent: str = ""):
    """Render an individual item."""
    item_name = item.get("name_raw", "Unknown item")
    desc = item.get("description_raw")
    base_price = _format_price(item.get("base_price"))
    size = item.get("size")
    variations = item.get("variations", [])

    # Build item header with clear labels
    item_header = f"**{item_name}**"
    if base_price:
        item_header += f" • Price: {base_price}"
    if size:
        item_header += f" • Size: {size}"

    st.markdown(f"{indent}- {item_header}")
    if desc:
        st.caption(f"{indent}  📝 {desc}")

    # Render variations if present
    if variations:
        for var in variations:
            var_name = var.get("name_raw", "Variation")
            var_price = _format_price(var.get("price"))
            var_size = var.get("size")
            
            var_label = f"{var_name}"
            if var_price:
                var_label += f" • Price: {var_price}"
            if var_size:
                var_label += f" • Size: {var_size}"
            
            st.caption(f"{indent}    ↳ {var_label}")


def _render_phase3(bases_data: Dict[str, Any]):
    """Render Phase 3 results: Base options."""
    if not bases_data or not bases_data.get("pages"):
        st.info("No results yet. Run Phase 3 after Phase 2.")
        return

    pages = bases_data["pages"]
    labels = [f"Page {p.get('page_number', '?')}" for p in pages]
    tabs = st.tabs(labels)

    for tab, page in zip(tabs, pages):
        with tab:
            with st.container(border=True):
                st.markdown(f"**Page {page.get('page_number', '?')}**")
                categories = page.get("categories", [])
                for cat in categories:
                    cat_name = cat.get("name_raw", "Unknown category")
                    st.markdown(f"### {cat_name}")

                    base_options = cat.get("base_options", [])
                    if base_options:
                        st.markdown("**🔧 Base Options:**")
                        for opt in base_options:
                            _render_option(opt, indent="  ")
                    else:
                        st.caption("  ℹ️ No base options specified")

                    subcats_base = cat.get("subcategories_base", [])
                    if subcats_base:
                        st.markdown("**🔧 Subcategory Base Options:**")
                        for opt in subcats_base:
                            _render_option(opt, indent="  ")


def _render_option(opt: Dict[str, Any], indent: str = ""):
    """Render a base or addon option."""
    name = opt.get("name_raw", "Unknown option")
    price = _format_price(opt.get("price"))
    default = opt.get("default", False)
    price_by_var = opt.get("price_by_variation")

    # Build option label with clear attributes
    option_label = f"**{name}**"
    if price:
        option_label += f" • Price: {price}"
    if default:
        option_label += f" • ✓ Default"

    st.caption(f"{indent}- {option_label}")

    # Show price variations if any
    if price_by_var:
        for pvar in price_by_var:
            var_name = pvar.get("variation_name", "Variation")
            var_price = _format_price(pvar.get("price"))
            if var_price:
                st.caption(f"{indent}    ↳ {var_name}: {var_price}")


def _render_phase4(addons_data: Dict[str, Any]):
    """Render Phase 4 results: Addons."""
    if not addons_data or not addons_data.get("pages"):
        st.info("No results yet. Run Phase 4 after Phase 3.")
        return

    pages = addons_data["pages"]
    labels = [f"Page {p.get('page_number', '?')}" for p in pages]
    tabs = st.tabs(labels)

    for tab, page in zip(tabs, pages):
        with tab:
            with st.container(border=True):
                st.markdown(f"**Page {page.get('page_number', '?')}**")
                categories = page.get("categories", [])
                for cat in categories:
                    cat_name = cat.get("name_raw", "Unknown category")
                    st.markdown(f"### {cat_name}")

                    # Items with addons directly under category
                    items_addons = cat.get("items_addons", [])
                    if items_addons:
                        st.markdown("**🎨 Items with Add-ons:**")
                        for item_addon in items_addons:
                            _render_item_addons(item_addon, indent="  ")
                    else:
                        st.caption("  ℹ️ No items with add-ons")

                    # Subcategory items with addons
                    subcat_items = cat.get("subcategory_items", [])
                    for subcat in subcat_items:
                        subcat_name = subcat.get("name_raw", "Unknown subcategory")
                        st.markdown(f"  #### {subcat_name}")
                        items_addons_sub = subcat.get("items_addons", [])
                        if items_addons_sub:
                            st.markdown("  **🎨 Items with Add-ons:**")
                            for item_addon in items_addons_sub:
                                _render_item_addons(item_addon, indent="    ")


def _render_item_addons(item_addon: Dict[str, Any], indent: str = ""):
    """Render item addons."""
    item_name = item_addon.get("name_raw", "Unknown item")
    st.markdown(f"{indent}**{item_name}**")
    addons = item_addon.get("addons", [])
    if addons:
        st.caption(f"{indent}Available add-ons ({len(addons)}):")
        for addon in addons:
            _render_addon_option(addon, indent=indent + "  ")
    else:
        st.caption(f"{indent}  ℹ️ No add-ons available")


def _render_addon_option(addon: Dict[str, Any], indent: str = ""):
    """Render an addon option."""
    name = addon.get("name_raw", "Unknown addon")
    price = _format_price(addon.get("price"))
    default = addon.get("default", False)
    price_by_var = addon.get("price_by_variation")

    # Build addon label with clear attributes
    addon_label = f"**{name}**"
    if price:
        addon_label += f" • Price: {price}"
    if default:
        addon_label += f" • ✓ Included by default"

    st.caption(f"{indent}- {addon_label}")

    # Show price variations if any
    if price_by_var:
        for pvar in price_by_var:
            var_name = pvar.get("variation_name", "Variation")
            var_price = _format_price(pvar.get("price"))
            if var_price:
                st.caption(f"{indent}    ↳ For {var_name}: {var_price}")


# ----------------------------
# Main Streamlit App
# ----------------------------
def main():
    st.set_page_config(page_title="Menu Extraction - 4 Phase Pipeline", layout="wide")
    st.title("🍽️ Menu Extraction Pipeline")
    st.write("Upload a PDF menu and run through the 4-phase extraction process.")

    # Sidebar settings
    with st.sidebar:
        st.header("⚙️ Settings")

        service: Literal["Groq", "OpenRouter"] = st.selectbox(
            "LLM Service", ["OpenRouter", "Groq"], index=0
        )

        if service == "OpenRouter":
            default_model = os.getenv("OPENROUTER_DEFAULT_MODEL", "google/gemini-flash-1.5").strip('"')
        else:
            default_model = os.getenv("GROQ_DEFAULT_MODEL", "llama-3.3-70b-versatile").strip('"')

        model = st.text_input("Model", value=default_model)
        st.caption("💡 OpenRouter tip: If you get a 404 error, check your [privacy settings](https://openrouter.ai/settings/privacy) or try a different model.")
        restaurant_name = st.text_input("Restaurant name", value="Sample Restaurant")

        st.divider()
        st.subheader("📄 Upload PDF")
        uploaded_pdf = st.file_uploader("Upload menu PDF", type=["pdf"])

        if uploaded_pdf:
            pdf_bytes = uploaded_pdf.getvalue()
            token = (uploaded_pdf.name, len(pdf_bytes))
            if st.session_state.get("pdf_token") != token:
                with st.spinner("Converting PDF to images..."):
                    st.session_state["pdf_token"] = token
                    # Run async function
                    images = asyncio.run(convert_pdf_into_images(pdf_bytes))
                    st.session_state["base64_images"] = images
                    # Clear previous results
                    for key in ["phase1_result", "phase2_result", "phase3_result", "phase4_result"]:
                        st.session_state.pop(key, None)
                st.success(f"✅ Loaded {len(st.session_state['base64_images'])} page(s)")

    base64_images = st.session_state.get("base64_images")

    # Phase execution buttons
    st.subheader("🚀 Run Phases")
    phase_cols = st.columns(4)

    with phase_cols[0]:
        disabled = base64_images is None
        if st.button("▶️ Run Phase 1", disabled=disabled, use_container_width=True):
            with st.spinner("Running Phase 1: Category extraction..."):
                result = asyncio.run(
                    execute_phase1(service, model, restaurant_name, base64_images)
                )
            st.session_state["phase1_result"] = result
            # Clear downstream phases
            for key in ["phase2_result", "phase3_result", "phase4_result"]:
                st.session_state.pop(key, None)
            st.success("✅ Phase 1 completed")
            st.rerun()

    with phase_cols[1]:
        disabled = base64_images is None or not st.session_state.get("phase1_result")
        if st.button("▶️ Run Phase 2", disabled=disabled, use_container_width=True):
            with st.spinner("Running Phase 2: Item extraction..."):
                result = asyncio.run(
                    execute_phase2(
                        service,
                        model,
                        restaurant_name,
                        base64_images,
                        st.session_state["phase1_result"],
                    )
                )
            st.session_state["phase2_result"] = result
            # Clear downstream phases
            for key in ["phase3_result", "phase4_result"]:
                st.session_state.pop(key, None)
            st.success("✅ Phase 2 completed")
            st.rerun()

    with phase_cols[2]:
        disabled = base64_images is None or not st.session_state.get("phase2_result")
        if st.button("▶️ Run Phase 3", disabled=disabled, use_container_width=True):
            with st.spinner("Running Phase 3: Base options extraction..."):
                result = asyncio.run(
                    execute_phase3(
                        service,
                        model,
                        restaurant_name,
                        base64_images,
                        st.session_state["phase2_result"],
                    )
                )
            st.session_state["phase3_result"] = result
            # Clear downstream phases
            st.session_state.pop("phase4_result", None)
            st.success("✅ Phase 3 completed")
            st.rerun()

    with phase_cols[3]:
        disabled = (
            base64_images is None
            or not st.session_state.get("phase2_result")
            or not st.session_state.get("phase3_result")
        )
        if st.button("▶️ Run Phase 4", disabled=disabled, use_container_width=True):
            with st.spinner("Running Phase 4: Addons extraction..."):
                result = asyncio.run(
                    execute_phase4(
                        service,
                        model,
                        restaurant_name,
                        base64_images,
                        st.session_state["phase2_result"],
                        st.session_state["phase3_result"],
                    )
                )
            st.session_state["phase4_result"] = result
            st.success("✅ Phase 4 completed")
            st.rerun()

    st.divider()

    # Status metrics
    st.subheader("📊 Pipeline Status")
    phase1_result = st.session_state.get("phase1_result")
    phase2_result = st.session_state.get("phase2_result")
    phase3_result = st.session_state.get("phase3_result")
    phase4_result = st.session_state.get("phase4_result")

    status_cols = st.columns(4)
    status_cols[0].metric("Phase 1", "✅ Ready" if phase1_result else "⏳ Not run")
    status_cols[1].metric("Phase 2", "✅ Ready" if phase2_result else "⏳ Not run")
    status_cols[2].metric("Phase 3", "✅ Ready" if phase3_result else "⏳ Not run")
    status_cols[3].metric("Phase 4", "✅ Ready" if phase4_result else "⏳ Not run")

    # Results tabs
    st.subheader("📋 Results")
    tab1, tab2, tab3, tab4 = st.tabs([
        "Phase 1 - Categories",
        "Phase 2 - Items",
        "Phase 3 - Base Options",
        "Phase 4 - Addons",
    ])

    with tab1:
        st.caption("Category extraction (phase 1)")
        if phase1_result:
            with st.expander("✏️ Edit JSON", expanded=False):
                phase1_result = _editable_json(
                    "Phase 1 JSON",
                    "phase1_result",
                    downstream_keys=["phase2_result", "phase3_result", "phase4_result"],
                )
            _render_phase1(phase1_result)
            with st.expander("📄 View Raw JSON", expanded=False):
                st.json(phase1_result)
            st.download_button(
                label="⬇️ Download categories.json",
                data=json.dumps(phase1_result, indent=2, ensure_ascii=False),
                file_name="categories.json",
                mime="application/json",
            )
        else:
            st.info("No results yet. Run Phase 1 after uploading the PDF.")

    with tab2:
        st.caption("Menu items with variations (phase 2)")
        if phase2_result:
            with st.expander("✏️ Edit JSON", expanded=False):
                phase2_result = _editable_json(
                    "Phase 2 JSON",
                    "phase2_result",
                    downstream_keys=["phase3_result", "phase4_result"],
                )
            _render_phase2(phase2_result)
            with st.expander("📄 View Raw JSON", expanded=False):
                st.json(phase2_result)
            st.download_button(
                label="⬇️ Download items.json",
                data=json.dumps(phase2_result, indent=2, ensure_ascii=False),
                file_name="items.json",
                mime="application/json",
            )
        else:
            st.info("No results yet. Run Phase 2 after Phase 1.")

    with tab3:
        st.caption("Category base options (phase 3)")
        if phase3_result:
            with st.expander("✏️ Edit JSON", expanded=False):
                phase3_result = _editable_json(
                    "Phase 3 JSON",
                    "phase3_result",
                    downstream_keys=["phase4_result"],
                )
            _render_phase3(phase3_result)
            with st.expander("📄 View Raw JSON", expanded=False):
                st.json(phase3_result)
            st.download_button(
                label="⬇️ Download base_options.json",
                data=json.dumps(phase3_result, indent=2, ensure_ascii=False),
                file_name="base_options.json",
                mime="application/json",
            )
        else:
            st.info("No results yet. Run Phase 3 after Phase 2.")

    with tab4:
        st.caption("Item-level addons (phase 4)")
        if phase4_result:
            with st.expander("✏️ Edit JSON", expanded=False):
                phase4_result = _editable_json(
                    "Phase 4 JSON",
                    "phase4_result",
                    downstream_keys=[],
                )
            _render_phase4(phase4_result)
            with st.expander("📄 View Raw JSON", expanded=False):
                st.json(phase4_result)
            st.download_button(
                label="⬇️ Download addons.json",
                data=json.dumps(phase4_result, indent=2, ensure_ascii=False),
                file_name="addons.json",
                mime="application/json",
            )
        else:
            st.info("No results yet. Run Phase 4 after Phase 3.")


if __name__ == "__main__":
    main()
