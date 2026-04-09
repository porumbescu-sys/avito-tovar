
from __future__ import annotations

import io
import json
import math
import re
import threading
import webbrowser
from pathlib import Path
from typing import Any

import pandas as pd
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
STATE_FILE = DATA_DIR / "catalog_state.pkl"
META_FILE = DATA_DIR / "catalog_meta.json"

STATE: dict[str, Any] = {
    "df": None,
    "filename": None,
    "template1_common_text": None,
}

EXPECTED_COLUMNS = {
    "article": ["Артикул", "артикул", "Код", "код", "SKU", "sku", "Article", "article"],
    "name": ["Номенклатура", "Наименование", "Название", "name", "Name", "Product"],
    "brand": ["Номенклатура.Производитель", "Производитель", "Бренд", "brand", "Brand"],
    "free_qty": ["Свободно", "свободно", "Свободный остаток", "В наличии", "available"],
    "total_qty": ["Всего", "всего", "Остаток", "Количество", "qty", "Qty"],
    "sale_price": ["Цена", "цена", "Цена продажи", "sale_price", "price", "Price"],
}

COLOR_KEYWORDS = [
    ("желтый", ["желтый", "yellow", "yel"]),
    ("красный", ["красный", "magenta", "red", "пурпурный"]),
    ("синий", ["синий", "голубой", "cyan", "blue"]),
    ("черный", ["черный", "black", "bk"]),
    ("пурпурный", ["пурпурный", "purple"]),
    ("голубой", ["голубой", "cyan"]),
    ("белый", ["белый", "white"]),
    ("серый", ["серый", "gray", "grey"]),
]


DEFAULT_TEMPLATE1_COMMON_TEXT = """Цeна с НДC : +17%

Работaeм по будням, c 10 дo 18:00. Самовывоз по адресу: Москва, ул. Сущёвский Вал, 5с20

Еcли пoтрeбуeтся пepeсылкa - oтпpaвляeм толькo Авитo-Яндeкc, Авито-СДЭК или Авито-Авито. Отправляем без наценки."""


def resolve_price_update_mask(df: pd.DataFrame, article_norm: str) -> tuple[pd.Series | None, str | None]:
    exact_mask = df["article_norm"] == article_norm
    if bool(exact_mask.any()):
        return exact_mask, "exact"

    linked_df = df[df["name_code_list"].apply(lambda codes: article_norm in codes)]
    linked_df = rank_linked_candidates(linked_df, article_norm).head(1)
    if not linked_df.empty:
        target_article_norm = str(linked_df.iloc[0]["article_norm"])
        return df["article_norm"] == target_article_norm, "linked"

    return None, None


def current_template1_common_text() -> str:
    text = STATE.get("template1_common_text")
    if text is None:
        return DEFAULT_TEMPLATE1_COMMON_TEXT
    return str(text)


def build_template1_hashtags(tokens: list[str], chosen: dict[str, Any] | None) -> str:
    raw_tags = list(tokens or [])
    if not raw_tags and chosen:
        raw_tags = [str(chosen.get("article", "") or "")]

    cleaned: list[str] = []
    for tag in raw_tags:
        tag_text = normalize_text(tag)
        if not tag_text:
            continue
        cleaned.append(tag_text)

    cleaned = unique_preserve_order(cleaned)
    return ",".join(f"#{tag}" for tag in cleaned)


LINKED_SEARCH_NEGATIVE_MARKERS = [
    "УЦЕНКА",
    "СОВМЕСТ",
    "COMPAT",
    "COMPATIBLE",
    "CACTUS",
    "КОНТРАКТ",
]

ARTICLE_PIECE_RE = re.compile(r"^[A-Za-zА-Яа-я0-9._-]{3,}$")


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().upper()
    text = re.sub(r"\s+", "", text)
    return text


def contains_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().upper()
    text = re.sub(r"\s+", " ", text)
    return text


def to_float(value: Any) -> float:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    text = text.replace("\xa0", "").replace(" ", "").replace(",", ".")
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(m.group(0)) if m else 0.0


def format_money(value: float) -> str:
    value = int(round(value))
    return f"{value:,}".replace(",", " ")


def round_up_to_100(value: float) -> float:
    if value <= 0:
        return 0.0
    return math.ceil(value / 100.0) * 100.0


def infer_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    lowered = {str(col).strip().lower(): col for col in df.columns}
    for cand in candidates:
        if cand.strip().lower() in lowered:
            return lowered[cand.strip().lower()]
    for col in df.columns:
        col_text = str(col).strip().lower()
        for cand in candidates:
            cand_text = cand.strip().lower()
            if cand_text in col_text:
                return col
    return None


def is_candidate_article_norm(norm: str) -> bool:
    if not norm:
        return False
    if norm.isdigit():
        return len(norm) >= 5
    return len(norm) >= 3 and any(ch.isdigit() for ch in norm) and any(ch.isalpha() for ch in norm)


def article_like_token(token: str) -> bool:
    token = str(token or "").strip()
    if not token:
        return False
    if not ARTICLE_PIECE_RE.fullmatch(token):
        return is_candidate_article_norm(normalize_text(token))
    return is_candidate_article_norm(normalize_text(token))


def extract_article_candidates_from_text(text: Any) -> list[str]:
    raw = str(text or "").upper()
    prepared = re.sub(r"[|/\\,;:()\[\]{}]+", " ", raw)
    prepared = prepared.replace("№", " ")
    chunks = re.findall(r"[A-ZА-Я0-9._-]{3,}", prepared)
    result: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        norm = normalize_text(chunk)
        if not is_candidate_article_norm(norm):
            continue
        if norm in seen:
            continue
        seen.add(norm)
        result.append(norm)
    return result


def standardize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    mapping: dict[str, str | None] = {}
    for key, candidates in EXPECTED_COLUMNS.items():
        mapping[key] = infer_column(df, candidates)

    result = pd.DataFrame()
    result["article"] = df[mapping["article"]] if mapping["article"] else ""
    result["name"] = df[mapping["name"]] if mapping["name"] else ""
    result["brand"] = df[mapping["brand"]] if mapping["brand"] else ""
    result["free_qty"] = df[mapping["free_qty"]] if mapping["free_qty"] else 0
    result["total_qty"] = df[mapping["total_qty"]] if mapping["total_qty"] else 0
    result["sale_price"] = df[mapping["sale_price"]] if mapping["sale_price"] else 0

    result["article"] = result["article"].fillna("").astype(str).str.strip()
    result["name"] = result["name"].fillna("").astype(str).str.strip()
    result["brand"] = result["brand"].fillna("").astype(str).str.strip()
    result["free_qty"] = result["free_qty"].apply(to_float)
    result["total_qty"] = result["total_qty"].apply(to_float)
    result["sale_price"] = result["sale_price"].apply(to_float)

    result["article_norm"] = result["article"].apply(normalize_text)
    result["name_norm"] = result["name"].apply(normalize_text)
    result["brand_norm"] = result["brand"].apply(normalize_text)
    result["search_blob"] = (
        result["article"].astype(str).fillna("") + " " +
        result["name"].astype(str).fillna("") + " " +
        result["brand"].astype(str).fillna("")
    ).apply(contains_text)
    result["name_tokens"] = result["name"].astype(str).fillna("").apply(contains_text)
    result["name_code_list"] = result["name"].astype(str).fillna("").apply(extract_article_candidates_from_text)

    result = result[result["article_norm"] != ""].copy()
    result.reset_index(drop=True, inplace=True)
    return result


def read_uploaded_file(file_storage) -> pd.DataFrame:
    filename = file_storage.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix in [".xlsx", ".xlsm", ".xltx", ".xltm"]:
        return pd.read_excel(file_storage)
    if suffix == ".xls":
        return pd.read_excel(file_storage, engine="xlrd")
    if suffix == ".csv":
        raw = file_storage.read()
        for enc in ["utf-8-sig", "cp1251", "utf-8", "latin1"]:
            try:
                return pd.read_csv(io.BytesIO(raw), encoding=enc)
            except Exception:
                continue
        raise ValueError("Не удалось прочитать CSV файл.")
    raise ValueError("Поддерживаются только XLSX, XLS, XLSM и CSV.")


def extract_color(name: str) -> str:
    text = contains_text(name).lower()
    for normalized, keywords in COLOR_KEYWORDS:
        if any(kw.lower() in text for kw in keywords):
            return normalized
    return ""


def selected_price(sale_price: float, price_mode: str, custom_discount: float, round100: bool) -> float:
    if price_mode == "minus12":
        price = sale_price * 0.88
    elif price_mode == "minus20":
        price = sale_price * 0.80
    else:
        price = sale_price * (1 - custom_discount / 100.0)
    if round100:
        price = round_up_to_100(price)
    return price


def avito_price(sale_price: float, round100: bool) -> float:
    price = sale_price * 0.88
    if round100:
        price = round_up_to_100(price)
    return price


def cash_price_from_avito(avito: float, round100: bool) -> float:
    price = avito * 0.90
    if round100:
        price = round_up_to_100(price)
    return price


def result_row_from_series(row: pd.Series, match_type: str, query: str, price_mode: str, custom_discount: float, round100: bool) -> dict[str, Any]:
    sale = float(row["sale_price"])
    selected = selected_price(sale, price_mode, custom_discount, round100)
    avito = avito_price(sale, round100)
    cash = cash_price_from_avito(avito, round100)

    return {
        "query": query,
        "match_type": match_type,
        "article": row["article"],
        "name": row["name"],
        "brand": row["brand"],
        "free_qty": float(row["free_qty"]),
        "total_qty": float(row["total_qty"]),
        "sale_price": sale,
        "sale_price_fmt": format_money(sale),
        "selected_price": selected,
        "selected_price_fmt": format_money(selected),
        "price_minus12": avito,
        "price_minus12_fmt": format_money(avito),
        "price_minus20": selected_price(sale, "minus20", custom_discount, round100),
        "price_minus20_fmt": format_money(selected_price(sale, "minus20", custom_discount, round100)),
        "cash_price": cash,
        "cash_price_fmt": format_money(cash),
        "color": extract_color(row["name"]),
        "in_stock": float(row["free_qty"]) > 0,
        "row_key": row["article_norm"],
    }


def row_has_negative_link_markers(row: pd.Series) -> bool:
    text = f"{row.get('article', '')} {row.get('name', '')}".upper()
    return any(marker in text for marker in LINKED_SEARCH_NEGATIVE_MARKERS)


def rank_linked_candidates(linked_df: pd.DataFrame, token_norm: str) -> pd.DataFrame:
    if linked_df.empty:
        return linked_df

    ranked = linked_df.copy()
    ranked["_negative"] = ranked.apply(row_has_negative_link_markers, axis=1)
    ranked["_in_stock"] = ranked["free_qty"].astype(float) > 0
    ranked["_code_index"] = ranked["name_code_list"].apply(lambda codes: codes.index(token_norm) if token_norm in codes else 999)
    ranked["_name_len"] = ranked["name"].astype(str).str.len()

    if (~ranked["_negative"]).any():
        ranked = ranked[~ranked["_negative"]].copy()

    ranked = ranked.sort_values(by=["_in_stock", "_code_index", "_name_len", "sale_price"], ascending=[False, True, True, False])
    return ranked.drop(columns=["_negative", "_in_stock", "_code_index", "_name_len"], errors="ignore")


def search_for_token(df: pd.DataFrame, token: str, mode: str, price_mode: str, custom_discount: float, round100: bool) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    q_norm = normalize_text(token)
    q_contains = contains_text(token)
    if not q_norm and not q_contains:
        return [], None

    if article_like_token(token):
        exact_df = df[df["article_norm"] == q_norm].head(1)
        if not exact_df.empty:
            exact_results = [result_row_from_series(r, "exact", token, price_mode, custom_discount, round100) for _, r in exact_df.iterrows()]
            return exact_results, exact_results[0]

        linked_df = df[df["name_code_list"].apply(lambda codes: q_norm in codes)]
        linked_df = rank_linked_candidates(linked_df, q_norm).head(20)
        if not linked_df.empty:
            linked_results = [result_row_from_series(r, "similar", token, price_mode, custom_discount, round100) for _, r in linked_df.iterrows()]
            return linked_results, linked_results[0]

    if mode == "article":
        contains_df = df[df["article_norm"].str.contains(re.escape(q_norm), na=False, regex=True)]
    elif mode == "name":
        contains_df = df[df["search_blob"].str.contains(re.escape(q_contains), na=False, regex=True)]
    else:
        contains_df = df[
            df["article_norm"].str.contains(re.escape(q_norm), na=False, regex=True)
            | df["search_blob"].str.contains(re.escape(q_contains), na=False, regex=True)
        ]

    contains_df = contains_df.head(30)
    similar_results = [result_row_from_series(r, "similar", token, price_mode, custom_discount, round100) for _, r in contains_df.iterrows()]
    preferred = similar_results[0] if similar_results else None
    return similar_results, preferred


def unique_preserve_order(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        norm = normalize_text(item)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        result.append(item)
    return result


def parse_tokens(raw_query: str) -> list[str]:
    raw = str(raw_query or "")
    if not raw.strip():
        return []

    raw = raw.replace("\r", "\n")
    preliminary = [p.strip() for p in re.split(r"[\n,;/|]+", raw) if p and p.strip()]
    tokens: list[str] = []

    for part in preliminary:
        compact = re.sub(r"\s+", " ", part).strip()
        if not compact:
            continue

        chunks = [c.strip() for c in compact.split(" ") if c.strip()]
        looks_like_article_pack = (
            len(chunks) > 1
            and all(article_like_token(c) for c in chunks)
        )

        if looks_like_article_pack:
            tokens.extend(chunks)
        else:
            tokens.append(compact)

    return unique_preserve_order(tokens)


def parse_template_groups(raw_query: str) -> list[dict[str, list[str] | str]]:
    raw = str(raw_query or "")
    if not raw.strip():
        return []

    raw = raw.replace("\r", "\n")
    raw_groups = [p.strip() for p in re.split(r"[\n,;]+", raw) if p and p.strip()]
    groups: list[dict[str, list[str] | str]] = []

    for raw_group in raw_groups:
        cleaned_group = re.sub(r"\s+", " ", raw_group).strip()
        if not cleaned_group:
            continue

        if "/" in cleaned_group:
            token_candidates = [t.strip() for t in re.split(r"\s*/\s*", cleaned_group) if t and t.strip()]
            token_candidates = [t for t in token_candidates if article_like_token(t)]
            token_candidates = unique_preserve_order(token_candidates)
            if token_candidates:
                display_value = "/".join(token_candidates)
                groups.append({"display": display_value, "tokens": token_candidates})
                continue

        groups.append({"display": cleaned_group, "tokens": parse_tokens(cleaned_group)})

    return groups


def persist_state() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df = STATE.get("df")
    filename = STATE.get("filename")
    if isinstance(df, pd.DataFrame) and not df.empty:
        df.to_pickle(STATE_FILE)
        META_FILE.write_text(
            json.dumps({
                "filename": filename or "неизвестный прайс",
                "template1_common_text": current_template1_common_text(),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    else:
        if STATE_FILE.exists():
            STATE_FILE.unlink()
        if META_FILE.exists():
            META_FILE.unlink()


def restore_state() -> None:
    if not STATE_FILE.exists():
        return
    try:
        df = pd.read_pickle(STATE_FILE)
        meta = {}
        if META_FILE.exists():
            meta = json.loads(META_FILE.read_text(encoding="utf-8"))
        if isinstance(df, pd.DataFrame) and not df.empty:
            STATE["df"] = df
            STATE["filename"] = meta.get("filename") or "сохранённый прайс"
            STATE["template1_common_text"] = meta.get("template1_common_text", DEFAULT_TEMPLATE1_COMMON_TEXT)
    except Exception:
        STATE["df"] = None
        STATE["filename"] = None
        STATE["template1_common_text"] = DEFAULT_TEMPLATE1_COMMON_TEXT


def parse_price_updates(raw_text: str) -> tuple[list[dict[str, Any]], list[str]]:
    lines = str(raw_text or "").replace("\r", "\n").split("\n")
    parsed: dict[str, dict[str, Any]] = {}
    invalid: list[str] = []

    article_re = re.compile(r"[A-Za-zА-Яа-я0-9._/-]{3,}")
    number_re = re.compile(r"\d[\d\s.,]*")

    for original_line in lines:
        line = original_line.strip()
        if not line:
            continue

        cleaned = (
            line.replace("🔽", " ")
            .replace("🔼", " ")
            .replace("⬇", " ")
            .replace("⬆", " ")
            .replace("↓", " ")
            .replace("↑", " ")
        )
        cleaned = re.sub(r"\[[^\]]+\]", " ", cleaned)

        article = None
        for token in article_re.findall(cleaned):
            norm = normalize_text(token)
            if is_candidate_article_norm(norm):
                article = token.strip()
                break

        price = None
        number_matches = number_re.findall(cleaned)
        for number_text in reversed(number_matches):
            value = to_float(number_text)
            if value > 0:
                price = value
                break

        if article and price is not None:
            article_norm = normalize_text(article)
            parsed[article_norm] = {
                "article": article,
                "article_norm": article_norm,
                "price": float(price),
            }
        else:
            invalid.append(original_line)

    return list(parsed.values()), invalid


restore_state()
if STATE.get("template1_common_text") is None:
    STATE["template1_common_text"] = DEFAULT_TEMPLATE1_COMMON_TEXT


@app.route("/")
def index():
    df: pd.DataFrame | None = STATE.get("df")
    has_loaded = isinstance(df, pd.DataFrame) and not df.empty
    filename = STATE.get("filename") or "не загружен"
    rows = int(len(df)) if has_loaded else 0
    load_status = f"Загружен: {filename}" if has_loaded else "Прайс ещё не загружен"
    return render_template(
        "index.html",
        initial_filename=filename if has_loaded else "не загружен",
        initial_rows=rows,
        initial_load_status=load_status,
        initial_template1_common_text=current_template1_common_text(),
    )


@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "Файл не выбран."}), 400

    uploaded = request.files["file"]
    if not uploaded.filename:
        return jsonify({"ok": False, "error": "Файл не выбран."}), 400

    try:
        raw_df = read_uploaded_file(uploaded)
        df = standardize_dataframe(raw_df)
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Не удалось прочитать файл: {exc}"}), 400

    if df.empty:
        return jsonify({"ok": False, "error": "После чтения файл оказался пустым."}), 400

    STATE["df"] = df
    STATE["filename"] = uploaded.filename
    persist_state()
    return jsonify({
        "ok": True,
        "filename": uploaded.filename,
        "rows": int(len(df)),
        "columns": ["Артикул", "Название", "Производитель", "Свободно", "Всего", "Цена продажи"]
    })


@app.route("/search", methods=["POST"])
def search():
    try:
        df: pd.DataFrame | None = STATE.get("df")
        if df is None:
            return jsonify({"ok": False, "error": "Сначала загрузите прайс."}), 400

        data = request.get_json(silent=True) or {}
        raw_query = str(data.get("query", "") or "")
        price_mode = str(data.get("price_mode", "minus12") or "minus12")
        search_mode = str(data.get("search_mode", "smart") or "smart")
        round100 = bool(data.get("round100", False))
        custom_discount = to_float(data.get("custom_discount", 10))

        tokens = parse_tokens(raw_query)
        if not tokens:
            return jsonify({"ok": False, "error": "Введите артикул или название."}), 400

        all_results: list[dict[str, Any]] = []
        chosen_for_templates: list[dict[str, Any] | None] = []
        seen_row_to_index: dict[str, int] = {}

        for token in tokens:
            rows, preferred = search_for_token(df, token, search_mode, price_mode, custom_discount, round100)
            chosen_for_templates.append(preferred)
            for item in rows:
                row_key = str(item.get("row_key") or normalize_text(item.get("article", "")))
                existing_index = seen_row_to_index.get(row_key)
                if existing_index is None:
                    seen_row_to_index[row_key] = len(all_results)
                    all_results.append(item)
                    continue

                existing_item = all_results[existing_index]
                existing_exact = existing_item.get("match_type") == "exact"
                new_exact = item.get("match_type") == "exact"

                if new_exact and not existing_exact:
                    all_results[existing_index] = item

        template1_lines: list[str] = []
        template2_lines: list[str] = []

        token_choice_map: dict[str, dict[str, Any] | None] = {
            normalize_text(token): chosen for token, chosen in zip(tokens, chosen_for_templates)
        }

        template1_entries: list[dict[str, Any]] = []

        for group in parse_template_groups(raw_query):
            group_display = str(group["display"])
            group_tokens = list(group["tokens"])

            resolved_entries: list[tuple[str, dict[str, Any] | None]] = []
            for group_token in group_tokens:
                resolved_entries.append((group_token, token_choice_map.get(normalize_text(group_token))))

            row_groups: dict[str, dict[str, Any]] = {}
            unresolved_tokens: list[str] = []

            for group_token, candidate in resolved_entries:
                if not candidate:
                    unresolved_tokens.append(group_token)
                    continue

                row_key = str(candidate.get("row_key") or normalize_text(candidate.get("article", group_token)))
                if row_key not in row_groups:
                    row_groups[row_key] = {
                        "tokens": [group_token],
                        "chosen": candidate,
                    }
                else:
                    row_groups[row_key]["tokens"].append(group_token)
                    current_best = row_groups[row_key]["chosen"]
                    if current_best and not current_best.get("in_stock") and candidate.get("in_stock"):
                        row_groups[row_key]["chosen"] = candidate

            if len(row_groups) == 1 and not unresolved_tokens and len(group_tokens) > 1:
                chosen = next(iter(row_groups.values()))["chosen"]
                template1_entries.append({
                    "type": "row",
                    "row_key": str(chosen.get("row_key") or normalize_text(chosen.get("article", group_display))) if chosen else group_display,
                    "tokens": unique_preserve_order(group_tokens),
                    "chosen": chosen,
                    "group_display": group_display,
                })
                continue

            for unresolved in unresolved_tokens:
                template1_entries.append({"type": "unresolved", "token": unresolved})

            for row_key, group_info in row_groups.items():
                template1_entries.append({
                    "type": "row",
                    "row_key": row_key,
                    "tokens": unique_preserve_order(group_info["tokens"]),
                    "chosen": group_info["chosen"],
                    "group_display": group_display,
                })

        merged_template1_entries: list[dict[str, Any]] = []
        row_entry_index: dict[str, int] = {}
        template1_hashtag_parts: list[str] = []

        for entry in template1_entries:
            if entry.get("type") != "row":
                merged_template1_entries.append(entry)
                continue

            row_key = str(entry.get("row_key") or "")
            if not row_key:
                merged_template1_entries.append(entry)
                continue

            existing_index = row_entry_index.get(row_key)
            if existing_index is None:
                entry["tokens"] = unique_preserve_order(list(entry.get("tokens") or []))
                merged_template1_entries.append(entry)
                row_entry_index[row_key] = len(merged_template1_entries) - 1
                continue

            existing_entry = merged_template1_entries[existing_index]
            merged_tokens = list(existing_entry.get("tokens") or []) + list(entry.get("tokens") or [])
            existing_entry["tokens"] = unique_preserve_order(merged_tokens)

            current_best = existing_entry.get("chosen")
            new_best = entry.get("chosen")
            if current_best and not current_best.get("in_stock") and new_best and new_best.get("in_stock"):
                existing_entry["chosen"] = new_best

        for entry in merged_template1_entries:
            if entry.get("type") == "unresolved":
                token = str(entry["token"])
                template1_lines.append(f"{token} --- продан")
                if token:
                    template1_hashtag_parts.append(f"#{normalize_text(token)}")
                continue

            chosen = entry.get("chosen")
            tokens_for_row = unique_preserve_order(list(entry.get("tokens") or []))
            group_display = str(entry.get("group_display") or "")

            if len(tokens_for_row) > 1:
                prefix = "/".join(tokens_for_row)
            elif tokens_for_row:
                single_token = tokens_for_row[0]
                if chosen and normalize_text(single_token) == normalize_text(chosen.get("article", "")):
                    color_part = f" {chosen['color']}" if chosen.get("color") else ""
                    prefix = f"{chosen['article']}{color_part}".strip()
                else:
                    prefix = single_token
            else:
                prefix = group_display or str(chosen.get("article", "")) if chosen else group_display

            if not chosen or not chosen.get("in_stock"):
                template1_lines.append(f"{prefix} --- продан")
            else:
                base_line = f"{prefix} --- {chosen['price_minus12_fmt']} руб. - Авито / {chosen['cash_price_fmt']} руб. за наличный расчет"
                template1_lines.append(base_line)

            hashtags = build_template1_hashtags(tokens_for_row, chosen)
            if hashtags:
                template1_hashtag_parts.extend([x for x in hashtags.split(",") if x.strip()])

        template1_common_text = current_template1_common_text().strip()
        template1_hashtags = ",".join(unique_preserve_order(template1_hashtag_parts))
        if template1_lines and template1_common_text:
            template1_lines.append(template1_common_text)
        if template1_lines and template1_hashtags:
            template1_lines.append(template1_hashtags)

        template2_seen_rows: set[str] = set()
        for token, chosen in zip(tokens, chosen_for_templates):
            if not chosen or not chosen["in_stock"]:
                continue

            row_key = str(chosen.get("row_key") or normalize_text(chosen.get("article", token)))
            if row_key in template2_seen_rows:
                continue
            template2_seen_rows.add(row_key)

            template2_lines.append(
                f"{chosen['name']} --- {chosen['selected_price_fmt']} руб."
            )

        summary = {
            "filename": STATE.get("filename"),
            "result_count": len(all_results),
            "loaded_rows": int(len(df)),
            "main_result": all_results[0] if all_results else None,
            "results": all_results,
            "template1": "\n\n".join(template1_lines),
            "template2": "\n\n".join(template2_lines),
            "tokens": tokens,
        }
        return jsonify({"ok": True, "data": summary})
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Ошибка поиска: {exc}"}), 500


@app.route("/apply_price_updates", methods=["POST"])
def apply_price_updates():
    df: pd.DataFrame | None = STATE.get("df")
    if df is None:
        return jsonify({"ok": False, "error": "Сначала загрузите прайс."}), 400

    data = request.get_json(silent=True) or {}
    raw_updates = str(data.get("updates", "") or "")
    updates, invalid_lines = parse_price_updates(raw_updates)
    if not updates:
        return jsonify({"ok": False, "error": "Не удалось распознать ни одной строки с артикулом и ценой."}), 400

    updated: list[dict[str, Any]] = []
    not_found: list[str] = []

    for item in updates:
        mask, match_source = resolve_price_update_mask(df, item["article_norm"])
        if mask is None or not bool(mask.any()):
            not_found.append(item["article"])
            continue

        df.loc[mask, "sale_price"] = float(item["price"])
        first_row = df.loc[mask].iloc[0]
        updated.append({
            "article": str(first_row["article"]),
            "source_article": str(item["article"]),
            "match_source": match_source or "exact",
            "price": float(item["price"]),
            "price_fmt": format_money(float(item["price"])),
        })

    STATE["df"] = df
    persist_state()

    return jsonify({
        "ok": True,
        "updated_count": len(updated),
        "updated": updated,
        "not_found": not_found,
        "invalid_lines": invalid_lines,
        "filename": STATE.get("filename"),
        "rows": int(len(df)),
    })


@app.route("/save_template1_common_text", methods=["POST"])
def save_template1_common_text():
    data = request.get_json(silent=True) or {}
    text = str(data.get("text", "") or "")
    STATE["template1_common_text"] = text.strip()
    persist_state()
    return jsonify({
        "ok": True,
        "text": current_template1_common_text(),
        "message": "Текст шаблона 1 сохранён",
    })


def open_browser() -> None:
    webbrowser.open_new("http://127.0.0.1:5000")


if __name__ == "__main__":
    threading.Timer(1.2, open_browser).start()
    app.run(host="127.0.0.1", port=5000, debug=False)
