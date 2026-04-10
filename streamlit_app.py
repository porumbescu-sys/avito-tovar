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
        "series_mode": "Только оригиналы",
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


ARTICLE_PIECE_RE = re.compile(r"^[A-Za-zА-Яа-я0-9._-]{3,}$")
SERIES_SUFFIX_ORDER = {
    "A": 0,
    "AC": 1,
    "X": 2,
    "XH": 3,
    "XC": 4,
    "Y": 5,
    "M": 6,
    "C": 7,
    "K": 8,
}
NEGATIVE_SERIES_MARKERS = ["УЦЕН", "СОВМЕСТ", "СОВМ", "COMPAT", "COMPATIBLE", "CACTUS", "КОНТРАКТ", "REFURB", "ВОССТ", "REMAN"]


def is_candidate_article_norm(norm: str) -> bool:
    if not norm:
        return False
    if norm.isdigit():
        return len(norm) >= 5
    return len(norm) >= 3 and any(ch.isdigit() for ch in norm) and any(ch.isalpha() for ch in norm)


def extract_article_candidates_from_text(text: object) -> list[str]:
    raw = str(text or "").upper()
    prepared = re.sub(r"[|/\,;:()\[\]{}]+", " ", raw)
    prepared = prepared.replace("№", " ")
    chunks = re.findall(r"[A-ZА-Я0-9._-]{3,}", prepared)
    out: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        norm = normalize_article(chunk)
        if not is_candidate_article_norm(norm) or norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
    return out


def row_has_negative_series_markers(row: pd.Series) -> bool:
    text = f"{row.get('article', '')} {row.get('name', '')}".upper()
    return any(marker in text for marker in NEGATIVE_SERIES_MARKERS)


def split_article_family_suffix(article_norm: str) -> tuple[str, str]:
    m = re.match(r"^(.*?\d)([A-ZА-Я]{1,3})$", article_norm)
    if m:
        return m.group(1), m.group(2)
    return article_norm, ""


def natural_chunks(value: str) -> list[object]:
    parts = re.split(r"(\d+)", value)
    result: list[object] = []
    for part in parts:
        if not part:
            continue
        result.append(int(part) if part.isdigit() else part)
    return result


def series_sort_key(candidate: dict[str, object]) -> tuple[object, ...]:
    article_norm = str(candidate.get("article_norm", ""))
    family, suffix = split_article_family_suffix(article_norm)
    rank = SERIES_SUFFIX_ORDER.get(suffix, 50)
    return (*natural_chunks(family), rank, suffix, article_norm)


def group_label_for_article(article_norm: str) -> str:
    family, _ = split_article_family_suffix(article_norm)
    return family


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

def is_negative_substitute_row(row: pd.Series) -> bool:
    text = f"{row.get('article', '')} {row.get('name', '')}".upper()
    markers = [
        "УЦЕН", "СОВМЕСТ", "СОВМ", "COMPAT", "COMPATIBLE", "CACTUS",
        "STATIC CONTROL", "PROFILINE", "NV PRINT", "KATUN", "SAKURA",
        "REMAN", "REFURB", "ВОССТ", "КОНТРАКТ", "Б/У", "БУ ", " USED "
    ]
    return any(marker in text for marker in markers)



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
    data["name_code_list"] = data["name"].map(extract_article_candidates_from_text)
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


def round_to_nearest_100(value: float) -> int:
    return int(math.floor(float(value) / 100.0 + 0.5) * 100)


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
    linked_hits: list[str] = []
    seen_done: set[str] = set()

    for article_norm, new_price in updates:
        if article_norm in seen_done:
            continue

        mask = out["article_norm"] == article_norm
        match_source = "exact"
        if not mask.any():
            linked = out[out["name_tokens"].map(lambda toks: article_norm in toks)]
            if not linked.empty:
                safe_linked = linked[~linked.apply(is_negative_substitute_row, axis=1)]
                if not safe_linked.empty:
                    chosen = safe_linked.iloc[0]
                    mask = out["article_norm"] == str(chosen["article_norm"])
                    match_source = "linked"

        if mask.any():
            out.loc[mask, "sale_price"] = float(new_price)
            out.loc[mask, "price_12"] = float(new_price) * (1 - DEFAULT_DISCOUNT_1 / 100)
            out.loc[mask, "price_20"] = float(new_price) * (1 - DEFAULT_DISCOUNT_2 / 100)
            updated_count += 1
            seen_done.add(article_norm)
            if match_source == "linked":
                first_row = out.loc[mask].iloc[0]
                linked_hits.append(f"{article_norm}→{first_row['article']}")
        else:
            missed.append(article_norm)

    message = f"Обновлено цен: {updated_count}"
    if linked_hits:
        message += " | Связанные: " + ", ".join(linked_hits[:8])
    if missed:
        message += " | Не найдено: " + ", ".join(missed[:10])
    return out, message

def find_best_row_for_token(df: pd.DataFrame, token: str, search_mode: str) -> tuple[Optional[pd.Series], str]:
    article_norm = normalize_article(token)
    if not article_norm:
        return None, ""

    exact = df[df["article_norm"] == article_norm]
    if not exact.empty:
        exact_safe = exact[~exact.apply(is_negative_substitute_row, axis=1)]
        chosen = exact_safe.iloc[0] if not exact_safe.empty else exact.iloc[0]
        return chosen, "exact"

    # Связанные короткие/длинные артикулы часто живут только в названии.
    # Но совместимые/уценка нельзя подсовывать автоматически вместо отсутствующей позиции.
    name_matches = df[df["name_tokens"].map(lambda toks: article_norm in toks)]
    if not name_matches.empty:
        safe_name_matches = name_matches[~name_matches.apply(is_negative_substitute_row, axis=1)]
        if not safe_name_matches.empty:
            chosen = safe_name_matches.iloc[0]
            return chosen, "linked"
        return None, ""

    if search_mode in {"Умный", "Артикул + название + бренд"}:
        contains = df[df["search_blob"].str.contains(re.escape(token.upper()), na=False)]
        if not contains.empty:
            safe_contains = contains[~contains.apply(is_negative_substitute_row, axis=1)]
            if not safe_contains.empty:
                chosen = safe_contains.iloc[0]
                return chosen, "similar"
            return None, ""

    return None, ""


def resolve_query_tokens(df: pd.DataFrame, query: str, search_mode: str) -> tuple[list[tuple[str, pd.Series, str]], list[str]]:
    resolved: list[tuple[str, pd.Series, str]] = []
    missing: list[str] = []
    for part in split_query_parts(query):
        row, match_type = find_best_row_for_token(df, part, search_mode)
        if row is None:
            missing.append(part)
        else:
            resolved.append((part, row, match_type))
    return resolved, missing

def perform_search(df: pd.DataFrame, query: str, search_mode: str) -> pd.DataFrame:
    resolved, _ = resolve_query_tokens(df, query, search_mode)
    if not resolved:
        return df.iloc[0:0].copy()

    rows = []
    seen_articles: set[str] = set()
    rank_map = {"exact": 0, "linked": 1, "similar": 2}

    for part, row, match_type in resolved:
        article_key = str(row["article_norm"])
        if article_key in seen_articles:
            continue
        seen_articles.add(article_key)
        row_dict = row.to_dict()
        row_dict["match_type"] = match_type
        row_dict["match_query"] = part
        row_dict["_rank"] = rank_map.get(match_type, 99)
        rows.append(row_dict)

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



def compact_multiline(text: str) -> str:
    lines = [normalize_text(line) for line in str(text).splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)

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
        # Хэштеги и группировка строятся только по тем артикулам, которые реально ввёл пользователь
        # и которые привели к этой позиции.
        tokens = unique_preserve_order([normalize_article(t) for t in item["tokens"] if normalize_article(t)])
        if not tokens:
            tokens = [str(row["article_norm"])]

        if is_available(row):
            avito_raw = float(row["sale_price"]) * (1 - DEFAULT_DISCOUNT_1 / 100)
            cash_raw = avito_raw * 0.90
            if round100:
                avito = round_up_to_100(avito_raw)
                cash = round_to_nearest_100(cash_raw)
            else:
                avito = round(avito_raw)
                cash = round(cash_raw)
            head = f"{row['article']} --- {fmt_price_with_rub(avito)} - Авито / {fmt_price_with_rub(cash)} за наличный расчет"
        else:
            color = detect_color(str(row["name"]))
            prefix = f"{row['article']} {color}".strip()
            head = f"{prefix} --- продан"

        lines.append(head)
        hashtag_parts.extend([f"#{t}" for t in tokens])

    for token in missing_tokens:
        lines.append(f"{token} --- продан")
        tok = normalize_article(token)
        if tok:
            hashtag_parts.append(f"#{tok}")

    hashtag_parts = unique_preserve_order(hashtag_parts)
    footer = compact_multiline(footer_text)
    out_lines = [line for line in lines if normalize_text(line)]
    if footer:
        out_lines.extend(footer.split("\n"))
    if hashtag_parts:
        out_lines.append(",".join(hashtag_parts))

    return "\n".join(out_lines)

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


def get_series_candidates(df: pd.DataFrame, raw_query: str, series_mode: str = "Только оригиналы") -> dict[str, object]:
    tokens = split_query_parts(raw_query)
    if len(tokens) != 1:
        return {"prefix": "", "candidates": []}

    token = tokens[0]
    token_norm = normalize_article(token)
    if len(token_norm) < 4:
        return {"prefix": token, "candidates": []}

    candidates_by_key: dict[str, dict[str, object]] = {}

    direct_df = df[df["article_norm"].str.startswith(token_norm, na=False)].copy()
    for _, row in direct_df.iterrows():
        candidate = {
            "article": str(row.get("article", "")),
            "article_norm": str(row.get("article_norm", "")),
            "name": str(row.get("name", "")),
            "brand": str(row.get("brand", "")),
            "free_qty": float(row.get("free_qty", 0) or 0),
            "sale_price": float(row.get("sale_price", 0) or 0),
            "is_original": not row_has_negative_series_markers(row),
        }
        candidates_by_key[candidate["article_norm"]] = candidate

    linked_mask = df["name_code_list"].apply(lambda codes: any(str(code).startswith(token_norm) for code in codes))
    linked_df = df[linked_mask].copy()
    for _, row in linked_df.iterrows():
        candidate = {
            "article": str(row.get("article", "")),
            "article_norm": str(row.get("article_norm", "")),
            "name": str(row.get("name", "")),
            "brand": str(row.get("brand", "")),
            "free_qty": float(row.get("free_qty", 0) or 0),
            "sale_price": float(row.get("sale_price", 0) or 0),
            "is_original": not row_has_negative_series_markers(row),
        }
        if candidate["article_norm"] not in candidates_by_key:
            candidates_by_key[candidate["article_norm"]] = candidate

    candidates = list(candidates_by_key.values())
    if series_mode != "Показывать всё":
        original_candidates = [c for c in candidates if bool(c.get("is_original", True))]
        if original_candidates:
            candidates = original_candidates

    candidates.sort(key=series_sort_key)
    if len(candidates) < 2:
        return {"prefix": token, "candidates": []}

    return {"prefix": token, "candidates": candidates}


st.markdown(
    """
    <style>
    .stApp { background: #eef3f9; }
    header[data-testid="stHeader"] { background: rgba(0,0,0,0); }
    [data-testid="stDecoration"] { display: none; }
    .block-container { max-width: 1560px; padding-top: 3.4rem; padding-bottom: 1.2rem; }

    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #08122f 0%, #102358 55%, #172a63 100%);
        border-right: 1px solid rgba(255,255,255,.08);
    }
    [data-testid="stSidebar"] * { color: #e9efff !important; }

    .sidebar-brand {
        display:flex; align-items:center; gap:12px;
        margin: 0.15rem 0 0.95rem 0;
        padding: 0.15rem 0.1rem 0.35rem 0.1rem;
    }
    .sidebar-brand-logo {
        width:44px; height:44px; border-radius:14px;
        background: linear-gradient(180deg, rgba(255,255,255,.18), rgba(255,255,255,.08));
        display:flex; align-items:center; justify-content:center;
        box-shadow: inset 0 1px 0 rgba(255,255,255,.15);
        font-size:22px;
    }
    .sidebar-brand-title { font-size: 1.22rem; font-weight: 900; line-height:1.05; color:#ffffff !important; }
    .sidebar-brand-sub { font-size: .82rem; color: #c7d6ff !important; margin-top: 4px; }

    .sidebar-card {
        background: linear-gradient(180deg, rgba(255,255,255,.055), rgba(255,255,255,.04));
        border: 1px solid rgba(255,255,255,.12);
        border-radius: 20px;
        padding: 1rem 0.95rem 0.95rem 0.95rem;
        margin: 0.95rem 0 1.05rem 0;
        box-shadow: 0 10px 22px rgba(2, 8, 23, .22), inset 0 1px 0 rgba(255,255,255,.05);
    }
    .sidebar-card-title {
        font-size: 1.02rem; font-weight: 900; color:#ffffff !important; margin-bottom: .45rem;
    }
    .sidebar-card-note {
        font-size: .78rem; line-height: 1.45; color:#c7d6ff !important; margin-bottom: .6rem;
    }
    .sidebar-status {
        background: rgba(7, 31, 74, .9);
        border: 1px solid rgba(255,255,255,.06);
        border-radius: 14px;
        padding: .72rem .78rem;
        color:#ffffff !important;
        font-weight: 800;
        margin-top: .55rem;
    }
    .sidebar-mini { font-size:.78rem; color:#c7d6ff !important; line-height:1.45; margin-top:.65rem; }

    [data-testid="stSidebar"] .stFileUploader section {
        background: rgba(255,255,255,0.03) !important;
        border: 1px dashed rgba(255,255,255,0.22) !important;
        border-radius: 16px !important;
        padding: 0.6rem !important;
    }
    [data-testid="stSidebar"] .stFileUploader button,
    [data-testid="stSidebar"] .stFileUploader button[kind],
    [data-testid="stSidebar"] .stFileUploader [data-testid="stFileUploaderDropzone"] button,
    [data-testid="stSidebar"] .stFileUploader [data-testid="baseButton-secondary"],
    [data-testid="stSidebar"] .stFileUploader [data-baseweb="button"] {
        background: linear-gradient(180deg, #3767ff 0%, #2455ef 100%) !important;
        color: #ffffff !important;
        -webkit-text-fill-color: #ffffff !important;
        border: none !important;
        border-radius: 14px !important;
        font-weight: 800 !important;
        opacity: 1 !important;
        box-shadow: 0 10px 20px rgba(49, 94, 251, 0.30) !important;
    }
    [data-testid="stSidebar"] .stFileUploader small,
    [data-testid="stSidebar"] .stFileUploader span,
    [data-testid="stSidebar"] .stFileUploader label {
        color: #dbe6ff !important;
        -webkit-text-fill-color: #dbe6ff !important;
        opacity: 1 !important;
    }
    [data-testid="stSidebar"] .stButton > button,
    [data-testid="stSidebar"] .stDownloadButton > button {
        width: 100% !important;
        min-height: 48px !important;
        background: linear-gradient(180deg, #3767ff 0%, #2455ef 100%) !important;
        color: #ffffff !important;
        border: none !important;
        border-radius: 16px !important;
        font-weight: 900 !important;
        font-size: 1rem !important;
        box-shadow: 0 10px 20px rgba(49, 94, 251, 0.30) !important;
    }
    [data-testid="stSidebar"] .stButton > button:hover,
    [data-testid="stSidebar"] .stDownloadButton > button:hover {
        background: linear-gradient(180deg, #4673ff 0%, #2a5cf2 100%) !important;
        color: #ffffff !important;
    }
    [data-testid="stSidebar"] .stButton > button:disabled,
    [data-testid="stSidebar"] .stDownloadButton > button:disabled {
        background: #5f6f96 !important;
        color: #edf2ff !important;
        opacity: .84 !important;
        box-shadow: none !important;
    }
    [data-testid="stSidebar"] .stNumberInput input,
    [data-testid="stSidebar"] .stTextInput input,
    [data-testid="stSidebar"] .stTextArea textarea,
    [data-testid="stSidebar"] [data-baseweb="textarea"] textarea,
    [data-testid="stSidebar"] [data-baseweb="input"] input,
    [data-testid="stSidebar"] [data-baseweb="base-input"] input,
    [data-testid="stSidebar"] [data-baseweb="select"] > div {
        background: #ffffff !important;
        color: #0f172a !important;
        -webkit-text-fill-color: #0f172a !important;
        caret-color: #0f172a !important;
        border-radius: 16px !important;
        border: none !important;
        box-shadow: inset 0 0 0 1px #dbe4f3 !important;
    }
    [data-testid="stSidebar"] .stTextArea textarea { line-height: 1.55 !important; }
    [data-testid="stSidebar"] .stTextArea textarea::placeholder,
    [data-testid="stSidebar"] [data-baseweb="textarea"] textarea::placeholder,
    [data-testid="stSidebar"] .stNumberInput input::placeholder,
    [data-testid="stSidebar"] .stTextInput input::placeholder {
        color: #7b8798 !important;
        -webkit-text-fill-color: #7b8798 !important;
        opacity: 1 !important;
    }
    [data-testid="stSidebar"] .stNumberInput button,
    [data-testid="stSidebar"] .stNumberInput [data-baseweb="button"],
    [data-testid="stSidebar"] .stNumberInput button svg,
    [data-testid="stSidebar"] .stNumberInput [data-baseweb="button"] svg {
        background: #edf3ff !important;
        color: #1d4ed8 !important;
        fill: #1d4ed8 !important;
        stroke: #1d4ed8 !important;
        border-color: #d9e4ff !important;
        opacity: 1 !important;
    }
    [data-testid="stSidebar"] .stRadio > label,
    [data-testid="stSidebar"] .stSelectbox > label,
    [data-testid="stSidebar"] .stCheckbox > label,
    [data-testid="stSidebar"] .stNumberInput > label,
    [data-testid="stSidebar"] .stTextArea > label,
    [data-testid="stSidebar"] .stFileUploader > label {
        color:#ffffff !important;
        font-weight: 800 !important;
        font-size: .92rem !important;
    }
    [data-testid="stSidebar"] .stCheckbox p,
    [data-testid="stSidebar"] .stRadio p { color:#eef3ff !important; }

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
    st.markdown(
        """
        <div class="sidebar-brand">
          <div class="sidebar-brand-logo">📦</div>
          <div>
            <div class="sidebar-brand-title">Мой Товар</div>
            <div class="sidebar-brand-sub">Почти как локальная версия 💙</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="sidebar-card">', unsafe_allow_html=True)
    st.markdown('<div class="sidebar-card-title">Загрузить прайс</div>', unsafe_allow_html=True)
    uploaded = st.file_uploader("Загрузить прайс", type=["xlsx", "xls", "xlsm", "csv"], label_visibility="collapsed")
    if uploaded is not None:
        try:
            st.session_state.catalog_df = load_price_file(uploaded.name, uploaded.getvalue())
            st.session_state.catalog_name = uploaded.name
        except Exception as exc:
            st.error(f"Ошибка: {exc}")
    file_caption = st.session_state.get("catalog_name", "Файл ещё не выбран")
    st.markdown(f'<div class="sidebar-status">Загружен: {html.escape(file_caption)}</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="sidebar-card">', unsafe_allow_html=True)
    st.markdown('<div class="sidebar-card-title">Быстрая правка цен</div>', unsafe_allow_html=True)
    st.markdown('<div class="sidebar-card-note">Вставьте строки вида <b>CE278A 8900</b>, <b>CF364A - 29700</b> или прямо текст из Telegram.</div>', unsafe_allow_html=True)
    st.text_area(
        "Вставьте строки вроде: CE278A 8900",
        key="price_patch_input",
        height=110,
        label_visibility="collapsed",
        placeholder="""CE278A 8900
CE278AC 7900
CF364A - 29700 🔽""",
    )
    if st.button("Править цены в прайсе", use_container_width=True):
        if isinstance(st.session_state.catalog_df, pd.DataFrame):
            updated_df, patch_message = apply_price_updates(
                st.session_state.catalog_df, st.session_state.price_patch_input
            )
            st.session_state.catalog_df = updated_df
            st.session_state.patch_message = patch_message
            submitted_query = normalize_text(st.session_state.get("submitted_query", ""))
            if submitted_query:
                st.session_state.last_result = perform_search(updated_df, submitted_query, st.session_state.get("search_mode", "Только артикул"))
        else:
            st.session_state.patch_message = "Сначала загрузите прайс."
    if st.session_state.patch_message:
        st.markdown(f'<div class="sidebar-mini">{html.escape(st.session_state.patch_message)}</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="sidebar-mini">Прайс сохраняется локально. После правок цены не пропадут до загрузки нового файла.</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="sidebar-card">', unsafe_allow_html=True)
    st.markdown('<div class="sidebar-card-title">⚙️ Настройки</div>', unsafe_allow_html=True)
    st.selectbox("Режим поиска", ["Только артикул", "Умный", "Артикул + название + бренд"], key="search_mode")
    st.radio("Какая цена главная", ["-12%", "-20%", "Своя скидка"], key="price_mode")
    st.number_input("Своя скидка, %", min_value=0.0, max_value=99.0, step=1.0, key="custom_discount")
    st.checkbox("Округлять вверх до 100", key="round100")
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="sidebar-card">', unsafe_allow_html=True)
    st.markdown('<div class="sidebar-card-title">Текст шаблона 1</div>', unsafe_allow_html=True)
    st.markdown('<div class="sidebar-card-note">Этот текст добавляется один раз в конце шаблона 1. Хэштеги по артикулам подставляются автоматически.</div>', unsafe_allow_html=True)
    st.text_area("Текст шаблона 1", key="template1_footer", height=170, label_visibility="collapsed")
    st.markdown('<div class="sidebar-mini">Текст сохраняется локально и останется до следующего изменения.</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

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

# Блок серии / цветов по части артикула
series_info = get_series_candidates(current_df, submitted_query, st.session_state.series_mode) if isinstance(current_df, pd.DataFrame) and submitted_query.strip() else {"prefix": "", "candidates": []}
series_candidates = series_info.get("candidates", []) if isinstance(series_info, dict) else []

if current_df is not None and submitted_query.strip() and series_candidates:
    st.markdown('<div class="result-wrap">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Серия / цвета по части артикула</div><div class="section-sub">Если по части артикула находится серия, можно быстро отметить нужные позиции и добавить их в основной поиск.</div>', unsafe_allow_html=True)

    st.radio("Режим серии", ["Только оригиналы", "Показывать всё"], key="series_mode", horizontal=True)
    # Пересчитаем после возможного изменения режима
    series_info = get_series_candidates(current_df, submitted_query, st.session_state.series_mode)
    series_candidates = series_info.get("candidates", []) if isinstance(series_info, dict) else []

    if series_candidates:
        st.caption(f"По префиксу {series_info.get('prefix', '')} найдено позиций: {len(series_candidates)}")

        # Кнопки управления выбором до рендера чекбоксов — иначе Streamlit ругается на session_state.
        c_add, c_all, c_clear = st.columns(3)
        select_all_clicked = c_all.button("Выбрать все", use_container_width=True, key=f"series_select_all_{normalize_article(str(series_info.get('prefix', '')))}")
        clear_all_clicked = c_clear.button("Очистить выбор", use_container_width=True, key=f"series_clear_all_{normalize_article(str(series_info.get('prefix', '')))}")

        if select_all_clicked:
            for cand in series_candidates:
                st.session_state[f"series_pick_{cand['article_norm']}"] = True
            st.rerun()

        if clear_all_clicked:
            for cand in series_candidates:
                st.session_state[f"series_pick_{cand['article_norm']}"] = False
            st.rerun()

        # Визуальная группировка по семейству артикула (например W2030A/X/XH, потом W2031...)
        family_counts: dict[str, int] = {}
        for cand in series_candidates:
            family = group_label_for_article(str(cand["article_norm"]))
            family_counts[family] = family_counts.get(family, 0) + 1

        current_family = None
        selected_articles: list[str] = []
        for cand in series_candidates:
            family = group_label_for_article(str(cand["article_norm"]))
            if family != current_family and family_counts.get(family, 0) > 1:
                st.markdown(f"**{html.escape(family)}**")
                current_family = family

            key = f"series_pick_{cand['article_norm']}"
            checked = st.checkbox(
                f"{cand['article']} — свободно: {fmt_qty(cand['free_qty'])} • {fmt_price_with_rub(cand['sale_price'])} • {cand['name']}",
                key=key,
            )
            if checked:
                selected_articles.append(str(cand["article"]))

        add_clicked = c_add.button("Добавить отмеченные в поиск", use_container_width=True, key=f"series_add_{normalize_article(str(series_info.get('prefix', '')))}")
        if add_clicked and selected_articles:
            normalized_query = "\n".join(unique_preserve_order(selected_articles))
            st.session_state.search_input = normalized_query
            st.session_state.submitted_query = normalized_query
            st.session_state.last_result = perform_search(current_df, normalized_query, search_mode)
            st.rerun()
    else:
        st.info("По этой части артикула серия не найдена или подходящих позиций меньше двух.")

    st.markdown('</div>', unsafe_allow_html=True)

st.markdown('<div class="result-wrap">', unsafe_allow_html=True)
st.markdown("""<div class="section-title">Шаблон 1 — Авито / наличный расчёт</div><div class="section-sub">Авито = цена продажи -12%. Наличный = ещё -10% от цены Авито. Если товара нет по «Свободно», будет «продан».</div>""", unsafe_allow_html=True)

if current_df is None:
    st.info("Сначала загрузите прайс в левой панели 👈")
elif not submitted_query.strip():
    st.info("Введите артикулы, затем нажмите **Найти**.")
else:
    template_text = build_offer_template(current_df, submitted_query, round100, st.session_state.template1_footer, search_mode)
    st.session_state["offer_template_text"] = template_text
    line_count = len([x for x in template_text.split("\n\n") if x.strip()]) if template_text.strip() else 0
    st.text_area("Готовый шаблон", height=min(500, max(180, 72 + line_count * 40)), key="offer_template_text")
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
    st.session_state["selected_price_template_text"] = second_template_text
    st.text_area("Готовый шаблон 2", height=min(360, max(150, 52 + max(1, second_template_text.count('\n\n') + 1) * 42)), key="selected_price_template_text")
    if second_template_text.strip():
        render_copy_big_button(second_template_text, "📋 Скопировать шаблон 2")
    else:
        st.info("Во втором шаблоне нечего показывать: найденных позиций в наличии нет.")

st.markdown('</div>', unsafe_allow_html=True)
