from pathlib import Path
from textwrap import dedent
import re

SOURCE = Path('streamlit_app.py')
TARGET = Path('streamlit_app_FIXED.py')

src = SOURCE.read_text(encoding='utf-8')

def require_contains(text, needle, label):
    if needle not in text:
        raise RuntimeError(f'Не найден блок для замены: {label}')

src = src.replace(
    '.compact-helper-card, .catalog-helper-hint {font-size: 13px;}',
    '.compact-helper-card, .catalog-helper-hint {{font-size: 13px;}}',
)
src = src.replace('.slim-helper {padding-top: 6px;}', '.slim-helper {{padding-top: 6px;}}')
src = src.replace('.mini-admin-title {font-size: 16px; font-weight: 700; color: #0f172a; margin-bottom: 6px;}', '.mini-admin-title {{font-size: 16px; font-weight: 700; color: #0f172a; margin-bottom: 6px;}}')
src = src.replace('.catalog-helper-hint {margin-top: 30px; color: #64748b; line-height: 1.45;}', '.catalog-helper-hint {{margin-top: 30px; color: #64748b; line-height: 1.45;}}')

anchor = "def load_persisted_purchase_source_into_state() -> bool:\n"
require_contains(src, anchor, 'anchor after purchase loader')
insert_after = dedent('''

def expense_price_summary_text() -> str:
    expense_df = st.session_state.get("expense_price_df")
    if not isinstance(expense_df, pd.DataFrame) or expense_df.empty:
        return "Прайс расхода: файл ещё не загружен"
    return f"Прайс расхода: {len(expense_df)} строк"


@st.cache_data(show_spinner=False, ttl=3600, max_entries=4)
def load_expense_price_file(file_name: str, file_bytes: bytes) -> pd.DataFrame:
    suffix = Path(file_name).suffix.lower()
    bio = io.BytesIO(file_bytes)
    if suffix == ".csv":
        try:
            raw = pd.read_csv(bio)
        except UnicodeDecodeError:
            bio.seek(0)
            raw = pd.read_csv(bio, encoding="cp1251")
    else:
        raw = pd.read_excel(bio, engine="openpyxl" if suffix in {".xlsx", ".xlsm"} or file_bytes[:2] == b"PK" else None)

    raw = raw.dropna(how="all").copy()
    if raw.empty:
        return pd.DataFrame(columns=[
            "expense_article", "expense_article_norm", "expense_name", "expense_codes",
            "expense_price", "expense_source_name"
        ])

    raw.columns = [normalize_text(c) for c in raw.columns]
    price_aliases = ["Цена расход", "цена расход", "Расход", "расход", "Цена", "price"]
    article_col = find_column(list(raw.columns), CATALOG_COLUMN_ALIASES["article"])
    name_col = find_column(list(raw.columns), CATALOG_COLUMN_ALIASES["name"])
    price_col = find_column(list(raw.columns), price_aliases)

    if not price_col:
        raise ValueError("В файле прайса расхода не найдена колонка с ценой. Ожидаю 'Цена расход' / 'Расход' / 'Цена'.")

    rows = []
    for _, r in raw.iterrows():
        article = normalize_text(r.get(article_col, "")) if article_col else ""
        name = normalize_text(r.get(name_col, "")) if name_col else ""
        price = safe_float(r.get(price_col), 0.0)
        if price <= 0:
            continue
        codes = build_row_compare_codes(article, name)
        article_norm = normalize_article(article) if article else ""
        if not article_norm and codes:
            article_norm = normalize_article(codes[0])
        if not article_norm and not codes and not name:
            continue
        rows.append({
            "expense_article": article,
            "expense_article_norm": article_norm,
            "expense_name": name,
            "expense_codes": codes,
            "expense_price": price,
            "expense_source_name": normalize_text(file_name),
        })
    return pd.DataFrame(rows)


def build_expense_price_indexes(expense_df: pd.DataFrame | None) -> tuple[dict[str, float], dict[str, float]]:
    by_article: dict[str, float] = {}
    by_code: dict[str, float] = {}
    if not isinstance(expense_df, pd.DataFrame) or expense_df.empty:
        return by_article, by_code
    for _, r in expense_df.iterrows():
        price = safe_float(r.get("expense_price"), 0.0)
        if price <= 0:
            continue
        art_norm = normalize_text(r.get("expense_article_norm", ""))
        if art_norm:
            by_article.setdefault(art_norm, price)
        for code in r.get("expense_codes", []) or []:
            code_norm = normalize_article(code)
            if code_norm:
                by_code.setdefault(code_norm, price)
    return by_article, by_code


def apply_expense_price_map(df: pd.DataFrame | None, expense_df: pd.DataFrame | None) -> pd.DataFrame | None:
    if df is None:
        return None
    out = df.copy()
    if "expense_price" not in out.columns:
        out["expense_price"] = None
    if not isinstance(expense_df, pd.DataFrame) or expense_df.empty:
        return out
    by_article, by_code = build_expense_price_indexes(expense_df)

    def _resolve(row):
        article_norm = normalize_text(row.get("article_norm", ""))
        if article_norm and article_norm in by_article:
            return by_article[article_norm]
        row_codes = row.get("row_codes", [])
        if not isinstance(row_codes, list):
            row_codes = build_row_compare_codes(row.get("article", ""), row.get("name", ""))
        for code in row_codes or []:
            code_norm = normalize_article(code)
            if code_norm in by_code:
                return by_code[code_norm]
        return None

    out["expense_price"] = out.apply(_resolve, axis=1)
    return out
''')
src = src.replace(anchor, insert_after + anchor, 1)

old = '        "hot_items_last_sync_sig": "",\n        "search_input": "",'
new = '        "hot_items_last_sync_sig": "",\n        "expense_price_df": None,\n        "expense_price_name": "ещё не загружен",\n        "search_input": "",'
require_contains(src, old, 'init_state defaults')
src = src.replace(old, new, 1)

pat = re.compile(r"def build_search_procurement_summary_df\([\s\S]*?\n\ndef get_procurement_summary_display_columns", re.M)
m = pat.search(src)
if not m:
    raise RuntimeError('Не найден build_search_procurement_summary_df')
replacement = dedent('''

def build_search_procurement_summary_df(
    result_df: pd.DataFrame | None,
    photo_df: pd.DataFrame | None,
    avito_df: pd.DataFrame | None,
    min_qty: float,
    sheet_name: str,
    sheet_label: str,
) -> pd.DataFrame:
    if not isinstance(result_df, pd.DataFrame) or result_df.empty:
        return pd.DataFrame()

    products_df = build_crm_workspace_products_df(result_df, photo_df, avito_df, min_qty, sheet_name, sheet_label)
    products_df = apply_expense_price_map(products_df, st.session_state.get("expense_price_df")) if isinstance(products_df, pd.DataFrame) else products_df
    if not isinstance(products_df, pd.DataFrame) or products_df.empty:
        return pd.DataFrame()

    decision_df = build_procurement_decision_df(products_df)
    if not isinstance(decision_df, pd.DataFrame) or decision_df.empty:
        return pd.DataFrame()

    extra_cols = [c for c in ["article_norm", "purchase_avg_cost", "expense_price", "supplier_valid_offers"] if c in products_df.columns]
    extra_df = products_df[extra_cols].copy() if extra_cols else pd.DataFrame()
    merged = decision_df.merge(extra_df, on="article_norm", how="left", suffixes=("", "_prod")) if not extra_df.empty else decision_df.copy()

    threshold_pct = float(st.session_state.get("distributor_threshold", 35.0) or 35.0)
    signal_records = merged.apply(lambda r: _classify_procurement_market_signals(r, threshold_pct=threshold_pct), axis=1)
    signal_df = pd.DataFrame(list(signal_records)) if len(signal_records) else pd.DataFrame()
    if not signal_df.empty:
        signal_df.index = merged.index
        merged["Лучшая цена рынка"] = signal_df.get("supplier_price_any")
    else:
        merged["Лучшая цена рынка"] = None

    out = pd.DataFrame({
        "Артикул": merged.get("Артикул", merged.get("article", "")),
        "Наименование": merged.get("Товар", merged.get("name", "")),
        "Наша цена": pd.to_numeric(merged.get("Наша цена", merged.get("sale_price", None)), errors="coerce"),
        "Сред цена": pd.to_numeric(merged.get("purchase_avg_cost", None), errors="coerce"),
        "Цена расход": pd.to_numeric(merged.get("expense_price", None), errors="coerce"),
        "Свободно": pd.to_numeric(merged.get("Наш остаток", merged.get("free_qty", None)), errors="coerce"),
        "Транзит": pd.to_numeric(merged.get("Транзит", merged.get("transit_qty", None)), errors="coerce"),
        "Мин у конкурентов": pd.to_numeric(merged.get("Лучшая цена рынка", merged.get("Цена поставщика", None)), errors="coerce"),
        "Продажи, шт/мес": pd.to_numeric(merged.get("Продажи, шт/мес", None), errors="coerce"),
        "Запас, мес": pd.to_numeric(merged.get("Запас, мес", None), errors="coerce"),
        "Лучший поставщик": merged.get("Лучший поставщик", ""),
        "Решение": merged.get("Решение", ""),
        "Почему": merged.get("Почему", ""),
        "article_norm": merged.get("article_norm", ""),
    })
    out = out.sort_values(["Артикул"], kind="stable").reset_index(drop=True)
    return out


def get_procurement_summary_display_columns''')
src = src[:m.start()] + replacement + src[m.end():]

pat = re.compile(r"def render_search_procurement_summary_block\([\s\S]*?\n\ndef render_sheet_workspace", re.M)
m = pat.search(src)
if not m:
    raise RuntimeError('Не найден render_search_procurement_summary_block')
replacement = dedent('''

def render_search_procurement_summary_block(
    result_df: pd.DataFrame | None,
    photo_df: pd.DataFrame | None,
    avito_df: pd.DataFrame | None,
    min_qty: float,
    sheet_name: str,
    sheet_label: str,
    tab_key: str,
) -> None:
    summary_df = build_search_procurement_summary_df(result_df, photo_df, avito_df, min_qty, sheet_name, sheet_label)
    if not isinstance(summary_df, pd.DataFrame) or summary_df.empty:
        return

    st.markdown('<div class="section-shell-card">', unsafe_allow_html=True)
    st.markdown(
        f"""
        <div class='section-shell-top'>
          <div>
            <div class='section-shell-badge'>📊 Закупочная сводка</div>
            <div class='section-shell-title'>Сразу вся ключевая информация по найденным позициям</div>
            <div class='section-shell-sub'>Главный экран для принятия решения. CRM, сырые строки поиска и сервисные инструменты открываются отдельно, чтобы не дублировать смысл на одной странице.</div>
          </div>
          <div class='section-shell-side'>
            <div class='stat-pill'>Лист: {html.escape(sheet_label)}</div>
            <div class='stat-pill'>Позиций: {len(summary_df)}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    show_df = summary_df.copy()
    numeric_cols = ["Наша цена", "Сред цена", "Цена расход", "Свободно", "Транзит", "Мин у конкурентов"]
    for col in numeric_cols:
        if col in show_df.columns:
            show_df[col] = show_df[col].apply(lambda x: "" if pd.isna(x) else (int(float(x)) if float(x).is_integer() else round(float(x), 2)))
    main_cols = [c for c in ["Артикул", "Наименование", "Наша цена", "Сред цена", "Цена расход", "Свободно", "Транзит", "Мин у конкурентов"] if c in show_df.columns]
    st.dataframe(show_df[main_cols], use_container_width=True, hide_index=True, height=min(460, 145 + len(show_df) * 35))

    with st.expander('Показать служебные поля', expanded=False):
        st.dataframe(summary_df, use_container_width=True, hide_index=True, height=min(680, 165 + len(summary_df) * 35))

    st.download_button(
        '⬇️ Скачать сводку',
        report_to_excel_bytes(summary_df),
        file_name=f'procurement_summary_{tab_key}.xlsx',
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        use_container_width=True,
        key=f'download_procurement_summary_{tab_key}',
    )
    st.markdown('</div>', unsafe_allow_html=True)


def render_sheet_workspace''')
src = src[:m.start()] + replacement + src[m.end():]

pat = re.compile(r"def render_analytics_workspace\([\s\S]*?\n\ndef classify_search_procurement_stock_status", re.M)
m = pat.search(src)
if not m:
    raise RuntimeError('Не найден render_analytics_workspace')
replacement = dedent('''

def render_analytics_workspace(sheet_df: pd.DataFrame | None, photo_df: pd.DataFrame | None, avito_df: pd.DataFrame | None, sheet_name: str, sheet_label: str, min_qty: float) -> None:
    products_df = get_cached_crm_workspace_products_df(sheet_df, photo_df, avito_df, min_qty, sheet_name, sheet_label)
    bundle = get_cached_operational_analytics_bundle(sheet_df, photo_df, avito_df, min_qty, sheet_label, st.session_state.get("hot_items_df")) if isinstance(sheet_df, pd.DataFrame) and not sheet_df.empty else {}
    decision_df = get_cached_procurement_decision_df(products_df)

    st.markdown('<div class="section-shell-card">', unsafe_allow_html=True)
    can_buy_count = int((decision_df["Можно закупать"] == "Да").sum()) if isinstance(decision_df, pd.DataFrame) and not decision_df.empty else 0
    dead_count = int((decision_df["Залежался"] == "Да").sum()) if isinstance(decision_df, pd.DataFrame) and not decision_df.empty else 0
    st.markdown(
        f"""
        <div class='section-shell-top'>
          <div>
            <div class='section-shell-badge'>📊 Аналитика · {html.escape(sheet_label)}</div>
            <div class='section-shell-title'>Картина по листу без визуального шума</div>
            <div class='section-shell-sub'>Этот экран оставляем только под чтение: рынок, спрос, качество карточек, склад и действия закупщика. Ничего не меняем в ядре comparison — только подаём данные в более собранном виде.</div>
          </div>
          <div class='section-shell-side'>
            <div class='stat-pill'>Позиций: {len(products_df) if isinstance(products_df, pd.DataFrame) else 0}</div>
            <div class='stat-pill'>Можно закупать: {can_buy_count}</div>
            <div class='stat-pill'>Залежалые: {dead_count}</div>
          </div>
        </div>
        <div class='slim-divider'></div>
        """,
        unsafe_allow_html=True,
    )
    if not isinstance(products_df, pd.DataFrame) or products_df.empty:
        st.info('По активному листу пока нет данных для аналитики.')
        st.markdown('</div>', unsafe_allow_html=True)
        return

    quality = bundle.get('quality', {}) if isinstance(bundle, dict) else {}
    top_df = bundle.get('top_df', pd.DataFrame()) if isinstance(bundle, dict) else pd.DataFrame()
    account_df = bundle.get('account_df', pd.DataFrame()) if isinstance(bundle, dict) else pd.DataFrame()
    quality_df = bundle.get('quality_df', pd.DataFrame()) if isinstance(bundle, dict) else pd.DataFrame()
    series_df = bundle.get('series_df', pd.DataFrame()) if isinstance(bundle, dict) else pd.DataFrame()
    source_df = bundle.get('source_df', pd.DataFrame()) if isinstance(bundle, dict) else pd.DataFrame()
    tasks_df = bundle.get('tasks_df', pd.DataFrame()) if isinstance(bundle, dict) else pd.DataFrame()
    patch_history_df = bundle.get('patch_history_df', pd.DataFrame()) if isinstance(bundle, dict) else pd.DataFrame()

    hot_count = int((decision_df['Ходовой'] == 'Да').sum()) if not decision_df.empty else 0
    ready_count = int((decision_df['Готов к размещению'] == 'Да').sum()) if not decision_df.empty else 0

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric('Позиций', len(products_df))
    m2.metric('Можно закупать', can_buy_count)
    m3.metric('Ходовые', hot_count)
    m4.metric('Залежалые', dead_count)
    m5.metric('Без фото', int(quality.get('without_photo', 0)))
    m6.metric('Готово к размещению', ready_count)

    render_info_banner(
        'Как читать этот экран',
        "Сначала смотри 'Сегодня' и 'Цена и рынок', потом 'Склад и спрос', а уже после этого 'Качество' и 'Аккаунты / серии'. Так ты быстрее поймёшь, что именно делать по листу прямо сейчас.",
        icon='🧠',
        chips=[f'лист: {sheet_label}', 'read-only analytics', 'поверх старого ядра'],
        tone='green',
    )

    st.markdown('<div class="workspace-segment-wrap">', unsafe_allow_html=True)
    analytics_section = st.radio(
        'Раздел аналитики',
        ['Сегодня', 'Цена и рынок', 'Склад и спрос', 'Качество', 'Аккаунты / серии'],
        key='analytics_workspace_section',
        horizontal=True,
        label_visibility='collapsed',
    )
    st.markdown("<div class='workspace-mini-caption'>Открываем один аналитический экран за раз: так интерфейс чище, а тяжёлые таблицы не шумят на старте.</div></div>", unsafe_allow_html=True)

    if analytics_section == 'Сегодня':
        if isinstance(tasks_df, pd.DataFrame) and not tasks_df.empty:
            st.markdown('#### Что делать сегодня')
            st.dataframe(tasks_df, use_container_width=True, hide_index=True)
        today_rows = []
        for label, df_slice, note in [
            ('Можно брать', filter_procurement_queue(decision_df, 'Можно брать'), 'Ходовые позиции с выгодным входом от поставщика'),
            ('К пополнению', filter_procurement_queue(decision_df, 'К пополнению'), 'Товар продаётся, запас проседает'),
            ('Требует цены', filter_procurement_queue(decision_df, 'Требует цены'), 'Наша цена выше рынка или запас залежался'),
            ('Без фото', filter_procurement_queue(decision_df, 'Без фото'), 'Нужно дотянуть карточки'),
            ('Без Avito', filter_procurement_queue(decision_df, 'Без Avito'), 'Есть товар, но нет размещения'),
        ]:
            today_rows.append({'Очередь': label, 'Позиций': len(df_slice), 'Что делать': note})
        st.dataframe(pd.DataFrame(today_rows), use_container_width=True, hide_index=True)
        hot_view = decision_df[decision_df['Ходовой'] == 'Да'].head(30)
        if not hot_view.empty:
            st.markdown('#### Ходовые позиции')
            st.dataframe(hot_view[[c for c in ['Артикул', 'Товар', 'Продажи, шт/мес', 'Запас, мес', 'Лучший поставщик', 'Цена поставщика', 'Разница, %', 'Решение'] if c in hot_view.columns]], use_container_width=True, hide_index=True, height=380)

    elif analytics_section == 'Цена и рынок':
        if isinstance(top_df, pd.DataFrame) and not top_df.empty:
            st.markdown('#### Приоритет на пересмотр цены')
            st.dataframe(top_df[[c for c in ['Артикул', 'Название', 'Продажи, шт/мес', 'Наш запас, мес', 'Наша цена', 'Лучшая цена дистрибьютора', 'Рекомендую, руб', 'Лучший поставщик', 'Разница, руб', 'Разница, %'] if c in top_df.columns]].head(150), use_container_width=True, hide_index=True, height=460)
        else:
            st.info('На текущем листе нет позиций, где рынок дешевле нас.')
        if isinstance(source_df, pd.DataFrame) and not source_df.empty:
            st.markdown('#### Кто чаще всего лучший по цене')
            st.dataframe(source_df, use_container_width=True, hide_index=True)
        if isinstance(patch_history_df, pd.DataFrame) and not patch_history_df.empty:
            st.markdown('#### Последние ручные правки цены')
            st.dataframe(patch_history_df[[c for c in ['changed_at', 'article', 'sheet_name', 'old_price', 'new_price', 'change_source', 'note'] if c in patch_history_df.columns]].head(40), use_container_width=True, hide_index=True, height=320)

    elif analytics_section == 'Склад и спрос':
        low_stock_df = decision_df[decision_df['Низкий запас'] == 'Да'].copy()
        dead_stock_df = decision_df[decision_df['Залежался'] == 'Да'].copy()
        overstock_df = decision_df[decision_df['Избыточный запас'] == 'Да'].copy()
        s1, s2, s3 = st.columns(3)
        s1.metric('Низкий запас', len(low_stock_df))
        s2.metric('Избыточный запас', len(overstock_df))
        s3.metric('Залежалый остаток', len(dead_stock_df))
        if not low_stock_df.empty:
            st.markdown('#### Нужно пополнение')
            st.dataframe(low_stock_df[[c for c in ['Артикул', 'Товар', 'Наш остаток', 'Продажи, шт/мес', 'Запас, мес', 'Лучший поставщик', 'Цена поставщика', 'Разница, %', 'Решение'] if c in low_stock_df.columns]].head(120), use_container_width=True, hide_index=True, height=360)
        if not dead_stock_df.empty:
            st.markdown('#### Залежалый склад')
            st.dataframe(dead_stock_df[[c for c in ['Артикул', 'Товар', 'Наш остаток', 'Продажи, шт/мес', 'Запас, мес', 'Решение', 'Почему'] if c in dead_stock_df.columns]].head(120), use_container_width=True, hide_index=True, height=360)
        elif low_stock_df.empty:
            st.info('По текущему листу нет явных проблем по запасу.')

    elif analytics_section == 'Качество':
        if isinstance(quality_df, pd.DataFrame) and not quality_df.empty:
            st.markdown('#### Покрытие качества карточек')
            st.dataframe(quality_df, use_container_width=True, hide_index=True)
        no_photo_df = filter_procurement_queue(decision_df, 'Без фото')
        no_avito_df = filter_procurement_queue(decision_df, 'Без Avito')
        ready_df = filter_procurement_queue(decision_df, 'Готово к размещению')
        q1, q2, q3 = st.columns(3)
        q1.metric('Без фото', len(no_photo_df))
        q2.metric('Без Avito', len(no_avito_df))
        q3.metric('Готово к размещению', len(ready_df))
        if not no_photo_df.empty:
            st.markdown('#### Позиции без фото')
            st.dataframe(no_photo_df[[c for c in ['Артикул', 'Товар', 'Наш остаток', 'Есть Avito', 'Решение', 'Почему'] if c in no_photo_df.columns]].head(120), use_container_width=True, hide_index=True, height=320)
        if not no_avito_df.empty:
            st.markdown('#### Позиции без Avito')
            st.dataframe(no_avito_df[[c for c in ['Артикул', 'Товар', 'Наш остаток', 'Есть фото', 'Готов к размещению', 'Решение'] if c in no_avito_df.columns]].head(120), use_container_width=True, hide_index=True, height=320)

    elif analytics_section == 'Аккаунты / серии':
        if isinstance(account_df, pd.DataFrame) and not account_df.empty:
            st.markdown('#### Аналитика по аккаунтам Avito')
            st.dataframe(account_df, use_container_width=True, hide_index=True)
        else:
            st.caption('В Avito пока нет данных по аккаунтам для этого листа.')
        if isinstance(series_df, pd.DataFrame) and not series_df.empty:
            st.markdown('#### Серийная аналитика')
            st.dataframe(series_df.head(120), use_container_width=True, hide_index=True, height=380)
        else:
            st.caption('На текущем листе не найдено серий, требующих отдельной сводки.')

    export_bundle = bundle if isinstance(bundle, dict) else {}
    if export_bundle:
        st.download_button(
            '⬇️ Скачать аналитику в Excel',
            analytics_bundle_to_excel_bytes(export_bundle),
            file_name=f'analytics_workspace_{sheet_name}.xlsx',
            mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            use_container_width=True,
            key=f'download_analytics_workspace_{sheet_name}',
        )
    st.markdown('</div>', unsafe_allow_html=True)


def classify_search_procurement_stock_status''')
src = src[:m.start()] + replacement + src[m.end():]

old = """    st.markdown(f'<div class=\"sidebar-mini\">{html.escape(purchase_cost_summary_text())}</div>', unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class=\"sidebar-card\">', unsafe_allow_html=True)
    render_sidebar_card_header(\"Отчёт и цены\", \"📊\", \"Порог выгоды и минимальный остаток для пересчёта лучшей цены.\")"""
new = """    st.markdown(f'<div class=\"sidebar-mini\">{html.escape(purchase_cost_summary_text())}</div>', unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class=\"sidebar-card\">', unsafe_allow_html=True)
    render_sidebar_card_header(\"Прайс расхода\", \"🧮\", \"Ручной загрузчик прайса расхода. Используется для колонки 'Цена расход' в закупочной сводке.\")
    expense_uploaded = st.file_uploader(
        \"Загрузить прайс расхода\",
        type=[\"xlsx\", \"xlsm\", \"csv\"],
        key=\"expense_price_uploader\",
        label_visibility=\"collapsed\",
        help=\"Ожидается файл с колонками Артикул / Наименование и Цена расход. Можно просто колонку Цена.\"
    )
    st.caption(\"ⓘ Эта загрузка нужна только для колонки 'Цена расход' в закупочной сводке.\")
    if expense_uploaded is not None:
        try:
            expense_bytes = expense_uploaded.getvalue()
            st.session_state.expense_price_df = load_expense_price_file(expense_uploaded.name, expense_bytes)
            st.session_state.expense_price_name = expense_uploaded.name
            log_operation(f\"Обновлён прайс расхода: {expense_uploaded.name}\", \"success\")
        except Exception as exc:
            log_operation(f\"Ошибка прайса расхода: {exc}\", \"warning\")
            st.error(f\"Ошибка прайса расхода: {exc}\")
    st.markdown(f'<div class=\"sidebar-status\">Прайс расхода: {html.escape(st.session_state.get(\"expense_price_name\", \"ещё не загружен\"))}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class=\"sidebar-mini\">{html.escape(expense_price_summary_text())}</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class=\"sidebar-card\">', unsafe_allow_html=True)
    render_sidebar_card_header(\"Отчёт и цены\", \"📊\", \"Порог выгоды и минимальный остаток для пересчёта лучшей цены.\")"""
require_contains(src, old, 'sidebar purchase/report anchor')
src = src.replace(old, new, 1)

TARGET.write_text(src, encoding='utf-8')
print(f'Готово: {TARGET}')
