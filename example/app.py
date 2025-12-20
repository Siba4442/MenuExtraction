import json
from typing import Literal

import streamlit as st

from util.client import first_phase, second_phase, third_phase
from util.process import convert_pdf_into_images


def _format_price(price):
    if isinstance(price, (int, float)):
        return f"${price:,.2f}".replace(".00", "")
    return price


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


def _render_phase1(categories_data):
    if not categories_data:
        st.info("No results yet. Run Phase 1 after converting the PDF.")
        return
    labels = [f"Page {p.get('page_number', '?')}" for p in categories_data]
    tabs = st.tabs(labels)
    for tab, page in zip(tabs, categories_data):
        with tab:
            with st.container(border=True):
                st.markdown(f"**Page {page.get('page_number', '?')}** - {page.get('restaurant_name', '')}")
                cats = page.get("categories", [])
                if cats:
                    st.markdown("\n".join([f"- {c}" for c in cats]))
                else:
                    st.caption("No categories found for this page.")


def _render_phase2(items_data):
    if not items_data:
        st.info("No results yet. Run Phase 2 after Phase 1.")
        return
    labels = [f"Page {p.get('page', '?')}" for p in items_data]
    tabs = st.tabs(labels)
    for tab, page in zip(tabs, items_data):
        with tab:
            with st.container(border=True):
                st.markdown(f"**Page {page.get('page', '?')}**")
                for block in page.get("menu_categories", []):
                    cat_dict = block.get("Category_items", {}) or {}
                    cat_description = block.get("category_description")
                    for cat_name, cat_payload in cat_dict.items():
                        st.markdown(f"**{cat_name}**")
                        if cat_description:
                            st.caption(cat_description)
                        for item in cat_payload.get("items", []):
                            item_name = item.get("item_name", "Unknown item")
                            desc = item.get("item_description")
                            st.markdown(f"- {item_name}")
                            if desc:
                                st.caption(f"  {desc}")
                note = page.get("note")
                if note:
                    st.caption(f"Note: {note}")


def _render_phase3(full_data):
    if not full_data:
        st.info("No results yet. Run Phase 3 after Phase 2.")
        return
    labels = [f"Page {p.get('page', '?')}" for p in full_data]
    tabs = st.tabs(labels)
    for tab, page in zip(tabs, full_data):
        with tab:
            with st.container(border=True):
                st.markdown(f"**Page {page.get('page', '?')}**")
                for cat in page.get("menu_categories", []):
                    cat_name = cat.get("category_name", "")
                    st.markdown(f"**{cat_name}**")
                    addons = cat.get("category_addons")
                    if addons:
                        st.caption("Add-ons:")
                        _render_addons(addons, indent="  ")
                    for item in cat.get("items", []):
                        item_name = item.get("item_name", "Unknown item")
                        st.markdown(f"- {item_name}")
                        piece = item.get("piece")
                        addons_item = item.get("addons")
                        if piece:
                            st.caption(f"  Pieces: {piece}")
                        if addons_item:
                            st.caption("  Add-ons:")
                            _render_addons(addons_item, indent="    ")
                        variations = item.get("variations", [])
                        for var in variations:
                            var_name = var.get("variation_name")
                            price = _format_price(var.get("price"))
                            desc = var.get("description")
                            label_parts = []
                            if var_name:
                                label_parts.append(var_name)
                            if price is not None:
                                label_parts.append(str(price))
                            label = " - ".join(label_parts) if label_parts else "Variation"
                            st.caption(f"    {label}")
                            if desc:
                                st.caption(f"      {desc}")
                st.markdown("")


def _render_addons(addons, indent: str = ""):
    if not addons:
        return
    if isinstance(addons, list):
        for add in addons:
            if isinstance(add, dict):
                name = add.get("name") or add.get("addon_name") or add.get("id") or "Addon"
                price = _format_price(add.get("price"))
                parts = [name]
                if price is not None:
                    parts.append(str(price))
                st.caption(f"{indent}- {' - '.join(parts)}")
            else:
                st.caption(f"{indent}- {add}")
    elif isinstance(addons, dict):
        for key, val in addons.items():
            if isinstance(val, (int, float)):
                st.caption(f"{indent}- {key}: {_format_price(val)}")
            else:
                st.caption(f"{indent}- {key}: {val}")
    else:
        st.caption(f"{indent}- {addons}")


def main():
    st.set_page_config(page_title="Menu Extraction")
    st.title("Menu Extraction")
    st.write("Upload a PDF, choose service, and run phases individually or all together.")

    # Sidebar controls
    with st.sidebar:
        st.header("Settings")
        service: Literal["Groq", "OpenRouter"] = st.selectbox(
            "LLM service", ["OpenRouter", "Groq"], index=0
        )
        restaurant_name = st.text_input("Restaurant name", value="Sample Restaurant")
        uploaded_pdf = st.file_uploader("Upload menu PDF", type=["pdf"])

        if uploaded_pdf:
            pdf_bytes = uploaded_pdf.getvalue()
            token = (uploaded_pdf.name, len(pdf_bytes))
            if st.session_state.get("pdf_token") != token:
                st.session_state["pdf_token"] = token
                st.session_state["base64_images"] = convert_pdf_into_images(pdf_bytes)
                st.success(f"Loaded {len(st.session_state['base64_images'])} page(s). You can now run phases.")

    base64_images = st.session_state.get("base64_images")

    # Phase actions
    st.subheader("Run Phases")
    phase_cols = st.columns(3)

    with phase_cols[0]:
        disabled = base64_images is None
        if st.button("Run / Re-run Phase 1", disabled=disabled):
            with st.spinner("Running category extraction (phase 1)..."):
                result = first_phase(service, restaurant_name, base64_images)
            st.success("Category extraction completed.")
            st.session_state["category_json"] = result

    with phase_cols[1]:
        disabled = base64_images is None or not st.session_state.get("category_json")
        if st.button("Run / Re-run Phase 2", disabled=disabled):
            with st.spinner("Running category item extraction (phase 2)..."):
                result = second_phase(
                    service,
                    base64_images,
                    st.session_state.get("category_json"),
                )
            st.success("Menu item extraction completed.")
            st.session_state["items_json"] = result

    with phase_cols[2]:
        disabled = base64_images is None or not st.session_state.get("items_json")
        if st.button("Run / Re-run Phase 3", disabled=disabled):
            with st.spinner("Running full menu extraction (phase 3)..."):
                result = third_phase(
                    service,
                    base64_images,
                    st.session_state.get("items_json"),
                )
            st.success("Full menu extraction completed.")
            st.session_state["full_json"] = result

    st.divider()

    # Status badges and outputs
    st.subheader("Outputs")
    category_json = st.session_state.get("category_json")
    items_json = st.session_state.get("items_json")
    full_json = st.session_state.get("full_json")

    status_cols = st.columns(3)
    status_cols[0].metric("Phase 1", "Ready" if category_json else "Not run")
    status_cols[1].metric("Phase 2", "Ready" if items_json else "Not run")
    status_cols[2].metric("Phase 3", "Ready" if full_json else "Not run")

    tab1, tab2, tab3 = st.tabs([
        "Phase 1 - Categories",
        "Phase 2 - Items",
        "Phase 3 - Full Menu",
    ])

    with tab1:
        st.caption("Category extraction (phase 1)")
        if category_json:
            with st.expander("Edit categories JSON", expanded=False):
                category_json = _editable_json(
                    "Phase 1 JSON",
                    "category_json",
                    downstream_keys=["items_json", "full_json"],
                )
            _render_phase1(category_json)
            with st.expander("View JSON", expanded=False):
                st.json(category_json)
            st.download_button(
                label="Download category_extraction.json",
                data=json.dumps(category_json, indent=2, ensure_ascii=False),
                file_name="category_extraction.json",
                mime="application/json",
            )
        else:
            st.info("No results yet. Run Phase 1 after converting the PDF.")

    with tab2:
        st.caption("Menu items (phase 2)")
        if items_json:
            with st.expander("Edit menu items JSON", expanded=False):
                items_json = _editable_json(
                    "Phase 2 JSON",
                    "items_json",
                    downstream_keys=["full_json"],
                )
            _render_phase2(items_json)
            with st.expander("View JSON", expanded=False):
                st.json(items_json)
            st.download_button(
                label="Download menu_items_full.json",
                data=json.dumps(items_json, indent=2, ensure_ascii=False),
                file_name="menu_items_full.json",
                mime="application/json",
            )
        else:
            st.info("No results yet. Run Phase 2 after Phase 1.")

    with tab3:
        st.caption("Full menu (phase 3)")
        if full_json:
            with st.expander("Edit full menu JSON", expanded=False):
                full_json = _editable_json(
                    "Phase 3 JSON",
                    "full_json",
                    downstream_keys=[],
                )
            _render_phase3(full_json)
            with st.expander("View JSON", expanded=False):
                st.json(full_json)
            st.download_button(
                label="Download menu_items_whole.json",
                data=json.dumps(full_json, indent=2, ensure_ascii=False),
                file_name="menu_items_whole.json",
                mime="application/json",
            )
        else:
            st.info("No results yet. Run Phase 3 after Phase 2.")


if __name__ == "__main__":
    main()