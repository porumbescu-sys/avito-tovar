from __future__ import annotations

import html
import io
import json
import math
import re
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="Мой Товар", page_icon="📦", layout="wide")

APP_TITLE = "Мой Товар"
DEFAULT_DISCOUNT_1 = 12.0
DEFAULT_DISCOUNT_2 = 20.0
DEFAULT_TEMPLATE1_FOOTER = (
    "Цeна с НДC : +17%\n\n"
    "Работaeм по будням, c 10 дo 18:00. Самовывоз по адресу: Москва, ул. Сущёвский Вал, 5с20\n\n"
    "Еcли пoтрeбуeтся пepeсылкa - oтпpaвляeм толькo Авитo-Яндeкc, Авито-СДЭК или Авито-Авито. Отправляем без наценки."
)

COLUMN_ALIASES = {
    "article": ["Артикул", "артикул", "код", "sku", "артикл", "article"],
    "name": ["Номенклатура", "Наименование", "название", "товар", "name"],
    "brand": [
        "Номенклатура.Производитель",
        "Производитель",
        "бренд",
        "марка",
        "brand",
    ],
    "free_qty": ["Свободно", "Свободный остаток", "остаток", "наличие", "free"],
    "total_qty": ["Всего", "Количество", "всего на складе", "total"],
    "price": ["Цена", "Цена продажи", "Продажа", "розница", "price"],
}

COLOR_KEYWORDS = [
    ("желтый", "желтый"),
    ("yellow", "желтый"),
    ("cyan", "голубой"),
    ("голубой", "голубой"),
    ("синий", "синий"),
    ("blue", "синий"),
    ("magenta", "пурпурный"),
    ("пурпур", "пурпурный"),
    ("фиолет", "пурпурный"),
    ("purple", "пурпурный"),
    ("red", "красный"),
    ("красный", "красный"),
    ("black", "черный"),
    ("черный", "черный"),
    ("чёрный", "черный"),
    ("grey", "серый"),
    ("gray", "серый"),
    ("серый", "серый"),
    ("green", "зеленый"),
    ("зел", "зеленый"),
]


def init_state() -> None:
    defaults = {
        "catalog_df": None,
        "catalog_name": "ещё не загружен",
        "search_input": "",
        "submitted_query": "",
        "last_result": None,
        "price_mode": "-12%",
        "custom_discount": 10.0,
        "round100": True,
        "search_mode": "Только артикул",
        "template1_footer": DEFAULT_TEMPLATE1_FOOTER,
        "price_patch_input": "",
        "patch_message": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


init_state()


def normalize_text(value: object) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).strip())


def normalize_article(value: object) -> str:
    text = normalize_text(value)
    return re.sub(r"[^A-Za-zА-Яа-я0-9]", "", text).upper()


def tokenize_text(value: object) -> list[str]:
    text = normalize_text(value)
    if not text:
        return []
    return [t for t in re.split(r"[^A-Za-zА-Яа-я0-9]+", text.upper()) if t]




def unique_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = normalize_text(item)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def find_column(columns: list[str], candidates: list[str]) -> Optional[str]:
    lower_map = {str(col).strip().lower(): col for col in columns}
    for candidate in candidates:
        col = lower_map.get(candidate.strip().lower())
        if col is not None:
            return col
    for candidate in candidates:
        c_low = candidate.strip().lower()
        for original in columns:
            o_low = str(original).strip().lower()
            if c_low in o_low or o_low in c_low:
                return original
    return None


def detect_columns(df: pd.DataFrame) -> Dict[str, Optional[str]]:
    return {key: find_column(list(df.columns), aliases) for key, aliases in COLUMN_ALIASES.items()}


@st.cache_data(show_spinner=False)
def load_price_file(file_name: str, file_bytes: bytes) -> pd.DataFrame:
    suffix = Path(file_name).suffix.lower()
    bio = io.BytesIO(file_bytes)
    if suffix == ".csv":
        try:
            raw = pd.read_csv(bio)
        except UnicodeDecodeError:
            bio.seek(0)
            raw = pd.read_csv(bio, encoding="cp1251")
    else:
        raw = pd.read_excel(bio)

    raw = raw.dropna(how="all")
    mapping = detect_columns(raw)
    required = ["article", "name", "price"]
    missing = [k for k in required if not mapping.get(k)]
    if missing:
        raise ValueError("Не удалось определить обязательные колонки: " + ", ".join(missing))

    data = pd.DataFrame()
    data["article"] = raw[mapping["article"]].map(normalize_text)
    data["article_norm"] = raw[mapping["article"]].map(normalize_article)
    data["name"] = raw[mapping["name"]].map(normalize_text)
    data["brand"] = raw[mapping["brand"]].map(normalize_text) if mapping.get("brand") else ""
    data["free_qty"] = (
        pd.to_numeric(raw[mapping["free_qty"]], errors="coerce").fillna(0)
        if mapping.get("free_qty")
        else 0
    )
    data["total_qty"] = (
        pd.to_numeric(raw[mapping["total_qty"]], errors="coerce").fillna(0)
        if mapping.get("total_qty")
        else 0
    )
    data["sale_price"] = pd.to_numeric(raw[mapping["price"]], errors="coerce")
    data = data.dropna(subset=["sale_price"])
    data = data[data["article_norm"] != ""].copy()
    data = data.drop_duplicates(subset=["article_norm"], keep="first")

    data["sale_price"] = data["sale_price"].astype(float)
    data["price_12"] = data["sale_price"] * (1 - DEFAULT_DISCOUNT_1 / 100)
    data["price_20"] = data["sale_price"] * (1 - DEFAULT_DISCOUNT_2 / 100)
    data["name_tokens"] = data["name"].map(tokenize_text)
    data["search_blob"] = (
        data["article_norm"].fillna("")
        + " "
        + data["name"].fillna("")
        + " "
        + data["brand"].fillna("")
    ).str.upper()
    return data.reset_index(drop=True)


def round_up_to_100(value: float) -> int:
    return int(math.ceil(float(value) / 100.0) * 100)


def current_discount(price_mode: str, custom_discount: float) -> float:
    if price_mode == "-12%":
        return DEFAULT_DISCOUNT_1
    if price_mode == "-20%":
        return DEFAULT_DISCOUNT_2
    return max(0.0, float(custom_discount))


def current_price_label(price_mode: str, custom_discount: float) -> str:
    disc = current_discount(price_mode, custom_discount)
    if float(disc).is_integer():
        return f"Цена -{int(disc)}%"
    return f"Цена -{str(round(disc, 2)).replace('.', ',')}%"


def get_selected_price_raw(row: pd.Series, price_mode: str, round100: bool, custom_discount: float) -> float:
    disc = current_discount(price_mode, custom_discount)
    value = float(row["sale_price"]) * (1 - disc / 100)
    return float(round_up_to_100(value)) if round100 else float(round(value, 2))


def fmt_price(value: float | int) -> str:
    if pd.isna(value):
        return ""
    value = float(value)
    if value.is_integer():
        return f"{int(value):,}".replace(",", " ")
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


def fmt_price_with_rub(value: float | int) -> str:
    return f"{fmt_price(value)} руб."


def fmt_qty(value: float | int) -> str:
    try:
        v = float(value)
    except Exception:
        return str(value)
    return str(int(v)) if v.is_integer() else f"{v:,.2f}".replace(",", " ").replace(".", ",")


def looks_like_article_token(token: str) -> bool:
    token = normalize_text(token)
    if not token:
        return False
    compact = re.sub(r"[\s\-_./]", "", token)
    has_digit = any(ch.isdigit() for ch in compact)
    has_alpha = any(ch.isalpha() for ch in compact)
    return len(compact) >= 3 and has_digit and has_alpha


def split_query_parts(query: str) -> list[str]:
    parts: list[str] = []
    raw_chunks = re.split(r"[\n,;]+", query)
    for chunk in raw_chunks:
        chunk = normalize_text(chunk)
        if not chunk:
            continue
        if "/" in chunk:
            slash_parts = [normalize_text(x) for x in re.split(r"\s*/\s*", chunk) if normalize_text(x)]
            if len(slash_parts) > 1:
                parts.extend(slash_parts)
                continue
        space_parts = [normalize_text(x) for x in re.split(r"\s+", chunk) if normalize_text(x)]
        if len(space_parts) > 1 and all(looks_like_article_token(x) for x in space_parts):
            parts.extend(space_parts)
        else:
            parts.append(chunk)
    return parts


def normalize_query_for_display(query: str) -> str:
    return "\n".join(split_query_parts(query))


def detect_color(name: str) -> str:
    low = normalize_text(name).lower()
    for needle, label in COLOR_KEYWORDS:
        if needle in low:
            return label
    return ""


def is_available(row: pd.Series) -> bool:
    try:
        return float(row.get("free_qty", 0)) > 0
    except Exception:
        return False


def parse_price_updates(text: str) -> list[tuple[str, float]]:
    updates: list[tuple[str, float]] = []
    for line in text.splitlines():
        line = normalize_text(line)
        if not line:
            continue
        cleaned = line.replace("🔽", " ").replace("🔼", " ").replace("—", "-")
        m = re.search(r"([A-Za-zА-Яа-я0-9./_-]+)\s*-?\s*([0-9][0-9\s.,]*)", cleaned)
        if not m:
            continue
        article = normalize_article(m.group(1))
        price_txt = re.sub(r"[^0-9,\.]", "", m.group(2)).replace(",", ".")
        try:
            price = float(price_txt)
        except ValueError:
            continue
        if article:
            updates.append((article, price))
    return updates


def apply_price_updates(df: pd.DataFrame, updates_text: str) -> tuple[pd.DataFrame, str]:
    updates = parse_price_updates(updates_text)
    if not updates:
        return df, "Не нашёл строк для правки цен."

    out = df.copy()
    updated_count = 0
    missed: list[str] = []
    seen_done: set[str] = set()

    for article_norm, new_price in updates:
        if article_norm in seen_done:
            continue
        mask = out["article_norm"] == article_norm
        if mask.any():
            out.loc[mask, "sale_price"] = float(new_price)
            out.loc[mask, "price_12"] = float(new_price) * (1 - DEFAULT_DISCOUNT_1 / 100)
            out.loc[mask, "price_20"] = float(new_price) * (1 - DEFAULT_DISCOUNT_2 / 100)
            updated_count += 1
            seen_done.add(article_norm)
        else:
            missed.append(article_norm)

    message = f"Обновлено цен: {updated_count}"
    if missed:
        message += " | Не найдено: " + ", ".join(missed[:10])
    return out, message


def find_best_row_for_token(df: pd.DataFrame, token: str, search_mode: str) -> tuple[Optional[pd.Series], str]:
    article_norm = normalize_article(token)
    if not article_norm:
        return None, ""

    exact = df[df["article_norm"] == article_norm]
    if not exact.empty:
        return exact.iloc[0], "exact"

    if search_mode == "Только артикул":
        return None, ""

    name_matches = df[df["name_tokens"].map(lambda toks: article_norm in toks)]
    if not name_matches.empty:
        preferred = name_matches[~name_matches["name"].str.contains(r"уцен|совмест|совм", case=False, na=False)]
        chosen = preferred.iloc[0] if not preferred.empty else name_matches.iloc[0]
        return chosen, "linked"

    if search_mode == "Артикул + название + бренд":
        contains = df[df["search_blob"].str.contains(re.escape(token.upper()), na=False)]
        if not contains.empty:
            return contains.iloc[0], "similar"

    return None, ""


def perform_search(df: pd.DataFrame, query: str, search_mode: str) -> pd.DataFrame:
    parts = split_query_parts(query)
    if not parts:
        return df.iloc[0:0].copy()

    rows = []
    seen_articles: set[str] = set()
    rank_map = {"exact": 0, "linked": 1, "similar": 2}

    for part in parts:
        row, match_type = find_best_row_for_token(df, part, search_mode)
        if row is None:
            continue
        article_key = str(row["article_norm"])
        if article_key in seen_articles:
            continue
        seen_articles.add(article_key)
        row_dict = row.to_dict()
        row_dict["match_type"] = match_type
        row_dict["match_query"] = part
        row_dict["_rank"] = rank_map.get(match_type, 99)
        rows.append(row_dict)

    if not rows:
        return df.iloc[0:0].copy()

    out = pd.DataFrame(rows).sort_values(["_rank", "article_norm"]).drop(columns=["_rank"]).reset_index(drop=True)
    return out


def build_display_df(df: pd.DataFrame, price_mode: str, round100: bool, custom_discount: float) -> pd.DataFrame:
    out = df.copy()
    out["selected_price"] = out.apply(lambda row: get_selected_price_raw(row, price_mode, round100, custom_discount), axis=1)
    label = current_price_label(price_mode, custom_discount)
    return pd.DataFrame(
        {
            "Артикул": out["article"],
            "Название": out["name"],
            "Производитель": out["brand"],
            "Свободно": out["free_qty"].map(fmt_qty),
            "Всего": out["total_qty"].map(fmt_qty),
            "Цена продажи": out["sale_price"].map(fmt_price),
            label: out["selected_price"].map(fmt_price),
        }
    )


def build_offer_template(df: pd.DataFrame, query: str, round100: bool, footer_text: str, search_mode: str) -> str:
    parts = split_query_parts(query)
    if not parts:
        return ""

    groups: dict[str, dict] = {}
    missing_tokens: list[str] = []

    for part in parts:
        row, _ = find_best_row_for_token(df, part, search_mode)
        if row is None:
            missing_tokens.append(part)
            continue
        key = str(row["article_norm"])
        grp = groups.setdefault(
            key,
            {
                "row": row,
                "tokens": [],
            },
        )
        grp["tokens"].append(part)

    lines: list[str] = []
    hashtag_parts: list[str] = []

    for item in groups.values():
        row = item["row"]
        tokens = []
        seen = set()
        for t in item["tokens"] + [str(row["article"])] + tokenize_text(str(row["name"])):
            t_norm = normalize_article(t)
            if not t_norm or t_norm in seen:
                continue
            if len(t_norm) < 4:
                continue
            if t_norm in tokenize_text(str(row["name"])) or t_norm == str(row["article_norm"]):
                tokens.append(t_norm)
                seen.add(t_norm)

        if is_available(row):
            avito = float(row["sale_price"]) * (1 - DEFAULT_DISCOUNT_1 / 100)
            cash = avito * 0.90
            if round100:
                avito = round_up_to_100(avito)
                cash = round_up_to_100(cash)
            else:
                avito = round(avito)
                cash = round(cash)
            head = f"{row['article']} --- {fmt_price_with_rub(avito)} - Авито / {fmt_price_with_rub(cash)} за наличный расчет"
        else:
            color = detect_color(str(row["name"]))
            prefix = f"{row['article']} {color}".strip()
            head = f"{prefix} --- продан"

        lines.append(head)
        hashtag_parts.extend([f"#{t}" for t in tokens[:12]])

    for token in missing_tokens:
        lines.append(f"{token} --- продан")
        hashtag_parts.append(f"#{normalize_article(token)}")

    hashtag_parts = unique_preserve_order(hashtag_parts)
    footer = footer_text.strip()
    if lines and footer:
        lines.append(footer)
    if lines and hashtag_parts:
        lines.append(",".join(hashtag_parts))

    return "\n\n".join(lines)


def build_selected_price_template(df: pd.DataFrame, query: str, price_mode: str, round100: bool, custom_discount: float, search_mode: str) -> str:
    parts = split_query_parts(query)
    lines: list[str] = []
    seen_articles: set[str] = set()
    for part in parts:
        row, _ = find_best_row_for_token(df, part, search_mode)
        if row is None:
            continue
        key = str(row["article_norm"])
        if key in seen_articles or not is_available(row):
            continue
        seen_articles.add(key)
        selected_price = get_selected_price_raw(row, price_mode, round100, custom_discount)
        lines.append(f"{normalize_text(row['name'])} --- {fmt_price_with_rub(selected_price)}")
    return "\n\n".join(lines)


def render_copy_big_button(text_value: str, button_label: str = "📋 Скопировать весь шаблон") -> None:
    escaped = json.dumps(text_value, ensure_ascii=False)
    html_block = f"""
    <div style='margin-top:8px;'>
      <button onclick='navigator.clipboard.writeText({escaped}).then(() => {{ this.innerText = "Скопировано"; setTimeout(() => this.innerText = {json.dumps(button_label, ensure_ascii=False)}, 1200); }})'
        style='border:none;background:#315efb;color:white;font-weight:800;border-radius:12px;padding:12px 16px;cursor:pointer;min-width:220px;'>
        {html.escape(button_label)}
      </button>
    </div>
    """
    components.html(html_block, height=58)


def render_results_table(df: pd.DataFrame, price_mode: str, round100: bool, custom_discount: float) -> None:
    selected_label = current_price_label(price_mode, custom_discount)
    rows_html = []
    for _, row in df.iterrows():
        selected_raw = get_selected_price_raw(row, price_mode, round100, custom_discount)
        selected_fmt = fmt_price(selected_raw)
        match_type = str(row.get("match_type", ""))
        if match_type == "exact":
            badge_html = "<div class='match-badge match-badge-exact'>Точное совпадение</div>"
        elif match_type == "linked":
            badge_html = "<div class='match-badge match-badge-linked'>Найдено по названию</div>"
        else:
            badge_html = "<div class='match-badge match-badge-similar'>Похожее совпадение</div>"
        rows_html.append(
            f"""
            <tr>
              <td><span class='article-pill'>{html.escape(str(row['article']))}</span></td>
              <td><div class='name-cell'>{html.escape(str(row['name']))}</div>{badge_html}</td>
              <td>{html.escape(str(row['brand'] or ''))}</td>
              <td>{fmt_qty(row['free_qty'])}</td>
              <td>{fmt_qty(row['total_qty'])}</td>
              <td class='sale-col'>{fmt_price(row['sale_price'])} руб.</td>
              <td class='selected-col'>{selected_fmt}</td>
              <td><button class='copy-btn' onclick="navigator.clipboard.writeText('{selected_fmt}').then(() => {{ this.innerText = 'Скопировано'; setTimeout(() => this.innerText = 'Копировать цену', 1200); }})">Копировать цену</button></td>
            </tr>
            """
        )
    table_html = f"""
    <!doctype html>
    <html><head><meta charset='utf-8'/>
    <style>
      body {{ margin:0; font-family: Inter, Arial, sans-serif; background: transparent; }}
      .wrap {{ background:white; border:1px solid #dbe5f1; border-radius:18px; overflow:hidden; }}
      table {{ width:100%; border-collapse:collapse; font-size:14px; }}
      thead th {{ background:#eef3fb; color:#334155; text-align:left; padding:14px; font-weight:800; border-bottom:1px solid #d7e1ef; }}
      tbody td {{ padding:14px; border-bottom:1px solid #e5edf6; vertical-align:top; color:#1e293b; }}
      tbody tr:last-child td {{ border-bottom:none; }}
      .article-pill {{ display:inline-block; padding:6px 10px; border-radius:999px; background:#edf2ff; color:#315efb; font-weight:800; }}
      .name-cell {{ font-weight:800; line-height:1.35; color:#1e293b; margin-bottom:6px; }}
      .match-badge {{ display:inline-block; padding:5px 10px; border-radius:999px; font-size:12px; font-weight:800; }}
      .match-badge-exact {{ background:#e8f7ee; color:#15803d; }}
      .match-badge-linked {{ background:#e8f1ff; color:#1d4ed8; }}
      .match-badge-similar {{ background:#fff0df; color:#c26a00; }}
      .sale-col {{ font-weight:800; }}
      .selected-col {{ background:#eef4ff; border-left:1px solid #c7d7ff; border-right:1px solid #c7d7ff; font-weight:900; color:#315efb; }}
      .copy-btn {{ border:none; background:#e9efff; color:#315efb; font-weight:800; border-radius:14px; padding:11px 14px; cursor:pointer; min-width:130px; }}
    </style></head><body>
      <div class='wrap'><table>
        <thead><tr><th>Артикул</th><th>Название</th><th>Производитель</th><th>Свободно</th><th>Всего</th><th>Цена продажи</th><th>{html.escape(selected_label)}</th><th>Действие</th></tr></thead>
        <tbody>{''.join(rows_html)}</tbody>
      </table></div>
    </body></html>
    """
    height = min(max(170, 66 + len(df) * 74), 900)
    components.html(table_html, height=height, scrolling=True)


def to_excel_bytes(df: pd.DataFrame, price_mode: str, round100: bool, custom_discount: float) -> bytes:
    label = current_price_label(price_mode, custom_discount)
    export_df = pd.DataFrame(
        {
            "Артикул": df["article"],
            "Название": df["name"],
            "Производитель": df["brand"],
            "Свободно": df["free_qty"],
            "Всего": df["total_qty"],
            "Цена продажи": df["sale_price"],
            label: df.apply(lambda row: get_selected_price_raw(row, price_mode, round100, custom_discount), axis=1),
        }
    )
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        export_df.to_excel(writer, index=False, sheet_name="Результаты")
    bio.seek(0)
    return bio.read()


st.markdown(
    """
    <style>
    .stApp { background: #eef3f9; }
    header[data-testid="stHeader"] { background: rgba(0,0,0,0); }
    [data-testid="stDecoration"] { display: none; }
    .block-container { max-width: 1560px; padding-top: 3.4rem; padding-bottom: 1.2rem; }
    [data-testid="stSidebar"] { background: linear-gradient(180deg, #0f172a 0%, #172554 100%); border-right: 1px solid rgba(255,255,255,.08); }
    [data-testid="stSidebar"] * { color: #e5ecff !important; }
    [data-testid="stSidebar"] .stFileUploader section {
        background: rgba(255,255,255,0.06) !important;
        border: 1px dashed rgba(255,255,255,0.25) !important;
        border-radius: 14px !important;
    }
    [data-testid="stSidebar"] .stFileUploader button,
    [data-testid="stSidebar"] .stFileUploader button[kind],
    [data-testid="stSidebar"] .stFileUploader [data-testid="stFileUploaderDropzone"] button,
    [data-testid="stSidebar"] .stFileUploader [data-testid="baseButton-secondary"],
    [data-testid="stSidebar"] .stFileUploader [data-baseweb="button"] {
        background: linear-gradient(180deg, #315efb 0%, #1d4ed8 100%) !important;
        color: #ffffff !important;
        -webkit-text-fill-color: #ffffff !important;
        border: 1px solid #2b57f1 !important;
        border-radius: 12px !important;
        font-weight: 800 !important;
        opacity: 1 !important;
        box-shadow: 0 8px 18px rgba(49, 94, 251, 0.28) !important;
    }
    [data-testid="stSidebar"] .stFileUploader small,
    [data-testid="stSidebar"] .stFileUploader span,
    [data-testid="stSidebar"] .stFileUploader label {
        color: #e5ecff !important;
        -webkit-text-fill-color: #e5ecff !important;
        opacity: 1 !important;
    }
    [data-testid="stSidebar"] .stNumberInput input,
    [data-testid="stSidebar"] .stTextInput input,
    [data-testid="stSidebar"] .stTextArea textarea,
    [data-testid="stSidebar"] [data-baseweb="textarea"] textarea,
    [data-testid="stSidebar"] [data-baseweb="input"] input,
    [data-testid="stSidebar"] [data-baseweb="base-input"] input {
        background: #ffffff !important;
        color: #0f172a !important;
        -webkit-text-fill-color: #0f172a !important;
        caret-color: #0f172a !important;
        opacity: 1 !important;
    }
    [data-testid="stSidebar"] .stNumberInput button,
    [data-testid="stSidebar"] .stNumberInput [data-baseweb="button"],
    [data-testid="stSidebar"] .stNumberInput button svg,
    [data-testid="stSidebar"] .stNumberInput [data-baseweb="button"] svg {
        background: #1e40af !important;
        color: #ffffff !important;
        fill: #ffffff !important;
        stroke: #ffffff !important;
        -webkit-text-fill-color: #ffffff !important;
        border-color: #1d4ed8 !important;
        opacity: 1 !important;
    }
    [data-testid="stSidebar"] .stTextArea textarea::placeholder,
    [data-testid="stSidebar"] [data-baseweb="textarea"] textarea::placeholder,
    [data-testid="stSidebar"] .stNumberInput input::placeholder,
    [data-testid="stSidebar"] .stTextInput input::placeholder {
        color: #64748b !important;
        -webkit-text-fill-color: #64748b !important;
        opacity: 1 !important;
    }
    [data-testid="stSidebar"] [data-baseweb="textarea"],
    [data-testid="stSidebar"] [data-baseweb="input"] {
        background: #ffffff !important;
        border-radius: 14px !important;
    }
    [data-testid="stSidebar"] [data-baseweb="select"] > div,
    [data-testid="stSidebar"] [data-baseweb="select"] span,
    [data-testid="stSidebar"] [data-baseweb="select"] input {
        color: #0f172a !important;
        -webkit-text-fill-color: #0f172a !important;
    }

    [data-testid="stSidebar"] .stButton > button,
    [data-testid="stSidebar"] .stDownloadButton > button {
        background: linear-gradient(180deg, #315efb 0%, #1d4ed8 100%) !important;
        color: #ffffff !important;
        border: 1px solid #2b57f1 !important;
        border-radius: 14px !important;
        font-weight: 800 !important;
        box-shadow: 0 8px 18px rgba(49, 94, 251, 0.28) !important;
    }
    [data-testid="stSidebar"] .stButton > button:hover,
    [data-testid="stSidebar"] .stDownloadButton > button:hover {
        background: linear-gradient(180deg, #3b6bff 0%, #2457ef 100%) !important;
        color: #ffffff !important;
        border-color: #3b6bff !important;
    }
    [data-testid="stSidebar"] .stButton > button:disabled,
    [data-testid="stSidebar"] .stDownloadButton > button:disabled {
        background: #1e293b !important;
        color: #ffffff !important;
        border: 1px solid #475569 !important;
        opacity: 1 !important;
        box-shadow: none !important;
    }
    .topbar { background: linear-gradient(90deg, #0f172a 0%, #1d4ed8 100%); color: white; padding: 16px 18px; border-radius: 18px; margin-top: 0.4rem; margin-bottom: 10px; box-shadow: 0 12px 28px rgba(15, 23, 42, .18); }
    .topbar-grid { display:grid; grid-template-columns: 1.6fr 1fr 1fr 1fr; gap: 10px; align-items:center; }
    .brand-box { display:flex; gap:12px; align-items:center; }
    .logo { width:54px;height:54px;border-radius:14px;background:rgba(255,255,255,.14); display:flex;align-items:center;justify-content:center;font-size:26px;font-weight:700; }
    .brand-title { font-size: 24px; font-weight: 900; line-height: 1; }
    .brand-sub { font-size: 13px; opacity: .9; margin-top: 5px; }
    .stat-box { background: rgba(255,255,255,.12); border: 1px solid rgba(255,255,255,.12); border-radius: 14px; padding: 10px 12px; min-height: 70px; }
    .stat-cap { font-size: 12px; opacity: .82; margin-bottom: 4px; }
    .stat-val { font-size: 16px; font-weight: 800; }
    .toolbar, .result-wrap { background: white; border: 1px solid #dbe5f1; border-radius: 16px; padding: 12px 14px; margin-bottom: 10px; box-shadow: 0 6px 18px rgba(15, 23, 42, .05); }
    .toolbar-title, .section-title { font-size: 18px; font-weight: 900; color:#0f172a; margin-bottom:4px; }
    .toolbar-sub, .section-sub { font-size: 12px; color:#64748b; margin-bottom:10px; }
    </style>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.markdown("## 📦 Мой Товар")
    st.caption("Streamlit-версия для загрузки в облако")
    uploaded = st.file_uploader("Загрузить прайс", type=["xlsx", "xls", "xlsm", "csv"])
    if uploaded is not None:
        try:
            st.session_state.catalog_df = load_price_file(uploaded.name, uploaded.getvalue())
            st.session_state.catalog_name = uploaded.name
            st.success(f"Загружен: {uploaded.name}")
        except Exception as exc:
            st.error(f"Ошибка: {exc}")

    st.markdown("### Быстрая правка цен")
    st.text_area(
        "Вставьте строки вроде: CE278A 8900",
        key="price_patch_input",
        height=110,
        label_visibility="collapsed",
        placeholder="CE278A 8900\nCE278AC 7900\nCF364A - 29700 🔽",
    )
    if st.button("Править цены в прайсе", use_container_width=True):
        if isinstance(st.session_state.catalog_df, pd.DataFrame):
            st.session_state.catalog_df, st.session_state.patch_message = apply_price_updates(
                st.session_state.catalog_df, st.session_state.price_patch_input
            )
        else:
            st.session_state.patch_message = "Сначала загрузите прайс."
    if st.session_state.patch_message:
        st.caption(st.session_state.patch_message)

    st.divider()
    st.markdown("### ⚙️ Настройки")
    st.selectbox("Режим поиска", ["Только артикул", "Умный", "Артикул + название + бренд"], key="search_mode")
    st.radio("Какая цена главная", ["-12%", "-20%", "Своя скидка"], key="price_mode")
    st.number_input("Своя скидка, %", min_value=0.0, max_value=99.0, step=1.0, key="custom_discount")
    st.checkbox("Округлять вверх до 100", key="round100")
    st.text_area("Текст шаблона 1", key="template1_footer", height=170)

catalog_df = st.session_state.get("catalog_df")
file_name = st.session_state.get("catalog_name", "ещё не загружен")
rows_count = len(catalog_df) if isinstance(catalog_df, pd.DataFrame) else 0
price_mode = st.session_state.price_mode
round100 = st.session_state.round100
custom_discount = float(st.session_state.custom_discount)
search_mode = st.session_state.search_mode
price_label = current_price_label(price_mode, custom_discount)

st.markdown(f"""
<div class="topbar"><div class="topbar-grid">
<div class="brand-box"><div class="logo">📦</div><div><div class="brand-title">{APP_TITLE}</div><div class="brand-sub">Streamlit • поиск • шаблоны • правка цен</div></div></div>
<div class="stat-box"><div class="stat-cap">Текущий прайс</div><div class="stat-val">{html.escape(file_name)}</div></div>
<div class="stat-box"><div class="stat-cap">Строк в каталоге</div><div class="stat-val">{rows_count}</div></div>
<div class="stat-box"><div class="stat-cap">Режим цены</div><div class="stat-val">{html.escape(price_label)}{' • округл.' if round100 else ''}</div></div>
</div></div>
""", unsafe_allow_html=True)

st.markdown('<div class="toolbar">', unsafe_allow_html=True)
st.markdown('<div class="toolbar-title">Поиск товара</div><div class="toolbar-sub">Можно искать по одному или нескольким артикулам. Пробелы, /, запятые и Enter тоже поддерживаются.</div>', unsafe_allow_html=True)

with st.form("search_form", clear_on_submit=False):
    search_value = st.text_area(
        "Поисковый запрос",
        value=st.session_state.search_input,
        placeholder="Например:\nCC530AC CC531AC CC532AC\nили\n842025 / 841913 / 841711 / 842339\nили Xerox 700",
        height=90,
        label_visibility="collapsed",
    )
    c1, c2, c3 = st.columns([1, 1, 2.4])
    find_clicked = c1.form_submit_button("🔎 Найти", use_container_width=True, type="primary")
    clear_clicked = c2.form_submit_button("🧹 Очистить", use_container_width=True)
    c3.markdown("<div style='padding-top:9px;color:#64748b;font-size:12px;'>Если код не найден в колонке «Артикул», приложение пробует найти его как связанный код в названии позиции.</div>", unsafe_allow_html=True)
st.markdown('</div>', unsafe_allow_html=True)

if clear_clicked:
    st.session_state.search_input = ""
    st.session_state.submitted_query = ""
    st.session_state.last_result = None
    st.rerun()

if find_clicked:
    normalized_query = normalize_query_for_display(search_value)
    st.session_state.search_input = normalized_query
    st.session_state.submitted_query = normalized_query
    st.session_state.last_result = (
        perform_search(st.session_state.catalog_df, normalized_query, search_mode)
        if isinstance(st.session_state.catalog_df, pd.DataFrame)
        else None
    )
    st.rerun()

current_df = st.session_state.catalog_df
submitted_query = st.session_state.submitted_query
result_df = st.session_state.last_result

st.markdown('<div class="result-wrap">', unsafe_allow_html=True)
st.markdown('<div class="section-title">Результаты</div><div class="section-sub">Точное совпадение — по колонке «Артикул». Найдено по названию — когда код сидит в названии той же позиции.</div>', unsafe_allow_html=True)

if current_df is None:
    st.info("Сначала загрузите прайс в левой панели 👈")
elif not submitted_query.strip():
    st.info("Введите артикул или название и нажмите **Найти**.")
elif result_df is None or result_df.empty:
    st.warning("Ничего не найдено. Попробуйте другой артикул, бренд или часть названия.")
else:
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Найдено", len(result_df))
    m2.metric("Цена", price_label)
    m3.metric("Округление", "вкл" if round100 else "выкл")
    m4.metric("Каталог", len(current_df))
    render_results_table(result_df.head(200), price_mode, round100, custom_discount)
    with st.expander("Показать техническую таблицу"):
        st.dataframe(build_display_df(result_df, price_mode, round100, custom_discount), use_container_width=True, hide_index=True, height=300)
    st.download_button("⬇️ Скачать найденное в Excel", to_excel_bytes(result_df, price_mode, round100, custom_discount), file_name="moy_tovar_results.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

st.markdown('</div>', unsafe_allow_html=True)

st.markdown('<div class="result-wrap">', unsafe_allow_html=True)
st.markdown("""<div class="section-title">Шаблон 1 — Авито / наличный расчёт</div><div class="section-sub">Авито = цена продажи -12%. Наличный = ещё -10% от цены Авито. Если товара нет по «Свободно», будет «продан».</div>""", unsafe_allow_html=True)

if current_df is None:
    st.info("Сначала загрузите прайс в левой панели 👈")
elif not submitted_query.strip():
    st.info("Введите артикулы, затем нажмите **Найти**.")
else:
    template_text = build_offer_template(current_df, submitted_query, round100, st.session_state.template1_footer, search_mode)
    line_count = len([x for x in template_text.split("\n\n") if x.strip()]) if template_text.strip() else 0
    st.text_area("Готовый шаблон", value=template_text, height=min(500, max(180, 72 + line_count * 40)), key="offer_template_text")
    if template_text.strip():
        render_copy_big_button(template_text, "📋 Скопировать шаблон 1")

st.markdown('</div>', unsafe_allow_html=True)

st.markdown('<div class="result-wrap">', unsafe_allow_html=True)
st.markdown(f"""<div class="section-title">Шаблон 2 — название + выбранная цена</div><div class="section-sub">Цена берётся из выбранного режима слева ({html.escape(price_label)}). Во второй шаблон попадают только позиции, где «Свободно» больше нуля.</div>""", unsafe_allow_html=True)

if current_df is None:
    st.info("Сначала загрузите прайс в левой панели 👈")
elif not submitted_query.strip():
    st.info("Введите артикулы, затем нажмите **Найти**.")
else:
    second_template_text = build_selected_price_template(current_df, submitted_query, price_mode, round100, custom_discount, search_mode)
    st.text_area("Готовый шаблон 2", value=second_template_text, height=min(360, max(150, 52 + max(1, second_template_text.count('\n\n') + 1) * 42)), key="selected_price_template_text")
    if second_template_text.strip():
        render_copy_big_button(second_template_text, "📋 Скопировать шаблон 2")
    else:
        st.info("Во втором шаблоне нечего показывать: найденных позиций в наличии нет.")

st.markdown('</div>', unsafe_allow_html=True)
