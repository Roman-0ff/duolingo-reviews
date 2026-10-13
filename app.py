"""
Duolingo Reviews Analyzer — Streamlit-приложение.
Собирает отзывы из Google Play и App Store, анализирует их
и позволяет скачать CSV + Markdown.

Запуск:
    pip install streamlit google-play-scraper pandas requests
    streamlit run app.py
"""

import re
import sys
import time
from collections import Counter
from datetime import datetime
from urllib.error import URLError

import pandas as pd
import requests
import streamlit as st
from google_play_scraper import reviews, Sort
from google_play_scraper.exceptions import NotFoundError, ExtraHTTPError


# ============================================================
# КОНСТАНТЫ
# ============================================================

DEFAULT_GOOGLE_PLAY_ID = "com.duolingo"
DEFAULT_APP_STORE_ID = "570060128"
APP_STORE_MAX_PAGES = 10  # RSS Apple отдаёт не более 10 страниц

STOP_WORDS = {
    'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
    'of', 'with', 'by', 'from', 'is', 'are', 'was', 'were', 'be', 'been',
    'being', 'it', 'its', 'this', 'that', 'these', 'those', 'i', 'you',
    'he', 'she', 'we', 'they', 'me', 'him', 'her', 'us', 'them', 'my',
    'your', 'his', 'our', 'their', 'not', 'no', 'so', 'if', 'as', 'up',
    'out', 'do', 'does', 'did', 'have', 'has', 'had', 'can', 'could',
    'will', 'would', 'should', 'may', 'might', 'must', 'am', 'very',
    'just', 'about', 'also', 'more', 'some', 'any', 'all', 'each',
    'every', 'both', 'few', 'many', 'much', 'other', 'another', 'such',
    'only', 'own', 'same', 'than', 'too', 'then', 'now', 'here', 'there',
    'when', 'where', 'why', 'how', 'what', 'which', 'who', 'whom',
    'get', 'got', 'use', 'used', 'using', 'one', 'two', 'like', 'even',
    'still', 'because', 'after', 'before', 'into', 'over',
}


# ============================================================
# ИСКЛЮЧЕНИЯ
# ============================================================

class AppNotFoundError(Exception):
    """Приложение не найдено в магазине."""


class ScrapingError(Exception):
    """Не удалось собрать отзывы (сеть, HTTP, парсинг)."""


# ============================================================
# ПАРСИНГ ВХОДНЫХ ДАННЫХ
# ============================================================

def parse_app_input(input_str: str) -> tuple:
    """Определяет платформу и извлекает ID приложения из строки или ссылки."""
    if not input_str or not input_str.strip():
        return ('google_play', DEFAULT_GOOGLE_PLAY_ID)

    s = input_str.strip()

    if "play.google.com" in s:
        match = re.search(r'[?&]id=([a-zA-Z0-9_.]+)', s)
        if match:
            return ('google_play', match.group(1))
        raise ValueError("Не удалось извлечь ID из ссылки Google Play.")

    if "apps.apple.com" in s or "itunes.apple.com" in s:
        match = re.search(r'/id(\d+)', s)
        if match:
            return ('app_store', match.group(1))
        raise ValueError("Не удалось извлечь ID из ссылки App Store.")

    if re.fullmatch(r'[a-zA-Z0-9_.]+', s):
        if '.' in s:
            return ('google_play', s)
        if s.isdigit():
            return ('app_store', s)

    raise ValueError(f"Не удалось определить платформу и ID из строки: '{input_str}'")


# ============================================================
# СБОР ОТЗЫВОВ
# ============================================================

def scrape_google_play(app_id: str, count: int = 500) -> list:
    """Собирает отзывы из Google Play с пагинацией и retry-логикой."""
    all_reviews = []
    continuation_token = None
    max_retries = 3
    fetched = 0

    while fetched < count:
        remaining = count - fetched
        batch_size = min(200, remaining)

        result = None
        for attempt in range(1, max_retries + 1):
            try:
                result, continuation_token = reviews(
                    app_id, lang='en', country='us',
                    sort=Sort.NEWEST, count=batch_size,
                    continuation_token=continuation_token,
                )
                break
            except NotFoundError:
                raise AppNotFoundError(
                    f"Приложение '{app_id}' не найдено в Google Play."
                )
            except (URLError, ExtraHTTPError) as e:
                wait = 2 ** attempt
                print(f"[Google Play] Сетевая ошибка (попытка "
                      f"{attempt}/{max_retries}): {e}. Повтор через {wait} с...",
                      file=sys.stderr)
                time.sleep(wait)
            except Exception as e:
                print(f"[Google Play] Ошибка парсинга пакета: {e}",
                      file=sys.stderr)
                break

        if result is None:
            if not all_reviews:
                raise ScrapingError(
                    f"Не удалось получить отзывы из Google Play для '{app_id}'."
                )
            break
        if not result:
            break

        for r in result:
            all_reviews.append({
                'reviewId': r.get('reviewId', ''),
                'userName': r.get('userName', ''),
                'content': r.get('content', ''),
                'score': r.get('score', 0),
                'thumbsUpCount': r.get('thumbsUpCount', 0),
                'reviewCreatedVersion': r.get('reviewCreatedVersion', ''),
                'at': str(r.get('at', '')),
                'replyContent': r.get('replyContent') or '',
                'repliedAt': str(r.get('repliedAt') or ''),
                'appVersion': r.get('appVersion', ''),
            })

        fetched = len(all_reviews)
        time.sleep(1)
        if continuation_token is None:
            break

    return all_reviews


def scrape_app_store(app_id: str, count: int = 250,
                     country: str = 'us') -> list:
    """Собирает отзывы из App Store через публичный RSS-фид Apple."""
    all_reviews = []
    max_retries = 3
    page = 1

    while len(all_reviews) < count and page <= APP_STORE_MAX_PAGES:
        url = (f"https://itunes.apple.com/{country}/rss/customerreviews/"
               f"page={page}/id={app_id}/sortby=mostrecent/json")

        data = None
        for attempt in range(1, max_retries + 1):
            try:
                resp = requests.get(url, timeout=15)
                if resp.status_code == 404:
                    raise AppNotFoundError(
                        f"Приложение '{app_id}' не найдено в App Store ({country})."
                    )
                resp.raise_for_status()
                data = resp.json()
                break
            except AppNotFoundError:
                raise
            except requests.exceptions.RequestException as e:
                wait = 2 ** attempt
                print(f"[App Store] Сетевая ошибка (попытка "
                      f"{attempt}/{max_retries}): {e}. Повтор через {wait} с...",
                      file=sys.stderr)
                time.sleep(wait)

        if data is None:
            if not all_reviews:
                raise ScrapingError(
                    f"Не удалось получить отзывы из App Store для '{app_id}'."
                )
            break

        entries = data.get('feed', {}).get('entry', [])
        if not entries:
            break

        for entry in entries:
            if 'im:rating' not in entry:
                continue
            try:
                all_reviews.append({
                    'reviewId': entry.get('id', {}).get('label', ''),
                    'userName': entry.get('author', {}).get('name', {}).get('label', ''),
                    'review': entry.get('content', {}).get('label', ''),
                    'rating': int(entry.get('im:rating', {}).get('label', 0)),
                    'title': entry.get('title', {}).get('label', ''),
                    'date': entry.get('updated', {}).get('label', ''),
                    'appVersion': entry.get('im:version', {}).get('label', ''),
                    'developerResponse': '',  # RSS Apple не отдаёт ответы
                })
            except Exception as e:
                print(f"[App Store] Ошибка парсинга отзыва: {e}", file=sys.stderr)
                continue

        next_page = None
        for link in data.get('feed', {}).get('link', []):
            if link.get('attributes', {}).get('rel') == 'next':
                next_page = link['attributes']['href']
                break
        if next_page is None:
            break

        page += 1
        time.sleep(1)

    return all_reviews


# ============================================================
# АНАЛИЗ
# ============================================================

def _text_col(platform: str) -> str:
    return 'content' if platform == 'google_play' else 'review'


def _score_col(platform: str) -> str:
    return 'score' if platform == 'google_play' else 'rating'


def _columns_for(platform: str) -> list:
    if platform == 'google_play':
        return ['reviewId', 'userName', 'content', 'score', 'thumbsUpCount',
                'reviewCreatedVersion', 'at', 'replyContent', 'repliedAt',
                'appVersion']
    return ['reviewId', 'userName', 'review', 'rating', 'title',
            'date', 'appVersion', 'developerResponse']


def build_dataframe(reviews_list: list, platform: str) -> pd.DataFrame:
    """Преобразует список отзывов в DataFrame с единым порядком колонок."""
    cols = _columns_for(platform)
    if not reviews_list:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(reviews_list)
    for col in cols:
        if col not in df.columns:
            df[col] = ''
    return df[cols]


def _tokenize(text: str) -> list:
    words = re.findall(r'[a-zA-Zа-яА-ЯёЁ]+', str(text).lower())
    return [w for w in words if len(w) > 2 and w not in STOP_WORDS]


def analyze_reviews(df: pd.DataFrame, platform: str) -> dict:
    """Считает метрики: средний рейтинг, распределение, топ слов в негативе."""
    if df is None or len(df) == 0:
        return {
            'avg_rating': None, 'total': 0, 'distribution': {},
            'negative_count': 0, 'top_words': [],
        }

    score_col = _score_col(platform)
    text_col = _text_col(platform)

    avg_rating = float(df[score_col].mean())
    dist = df[score_col].value_counts().sort_index(ascending=False).to_dict()

    negative = df[df[score_col] <= 2]
    words = []
    for text in negative[text_col].dropna():
        words.extend(_tokenize(text))
    top_words = Counter(words).most_common(10)

    return {
        'avg_rating': avg_rating,
        'total': len(df),
        'distribution': {int(k): int(v) for k, v in dist.items()},
        'negative_count': int(len(negative)),
        'top_words': top_words,
    }


def build_markdown(df: pd.DataFrame, platform: str, app_id: str,
                   analysis: dict) -> str:
    """Генерирует Markdown-отчёт."""
    lines = [
        f"# Анализ отзывов: {platform} — {app_id}",
        f"\n**Дата сбора:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Всего отзывов:** {analysis['total']}",
    ]

    if analysis['total'] == 0:
        lines.append("\nNo reviews found.")
        return '\n'.join(lines)

    lines.append(f"\n**Средний рейтинг:** {analysis['avg_rating']:.2f} / 5")
    lines.append("\n## Распределение оценок\n")
    lines.append("| Оценка | Количество | Доля |")
    lines.append("|--------|-----------|------|")
    total = analysis['total']
    for score in [5, 4, 3, 2, 1]:
        cnt = analysis['distribution'].get(score, 0)
        pct = cnt / total * 100
        lines.append(f"| {score} ★ | {cnt} | {pct:.1f}% |")

    lines.append(f"\n## Негативные отзывы (оценка ≤ 2): "
                 f"{analysis['negative_count']} шт.\n")

    if analysis['top_words']:
        lines.append("### Топ-5 болей пользователей\n")
        lines.append("| # | Боль (слово) | Частота |")
        lines.append("|---|--------------|---------|")
        for i, (w, f) in enumerate(analysis['top_words'][:5], 1):
            lines.append(f"| {i} | {w} | {f} |")

        lines.append("\n### Топ-10 слов в негативных отзывах\n")
        lines.append("| # | Слово | Частота |")
        lines.append("|---|-------|---------|")
        for i, (w, f) in enumerate(analysis['top_words'], 1):
            lines.append(f"| {i} | {w} | {f} |")
    else:
        lines.append("*Негативных отзывов не найдено.*")

    return '\n'.join(lines)


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    """CSV-байты с BOM — корректно открывается в Excel."""
    return df.to_csv(index=False).encode('utf-8-sig')


# ============================================================
# STREAMLIT UI
# ============================================================

st.set_page_config(
    page_title="Duolingo Reviews Analyzer",
    page_icon="🦉",
    layout="wide",
)


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_reviews(platform: str, app_id: str, count: int,
                  country: str) -> list:
    """Кэширует результаты сбора на 30 минут."""
    if platform == 'google_play':
        return scrape_google_play(app_id, count=count)
    return scrape_app_store(app_id, count=count, country=country)


def _resolve_platform(user_input: str, platform_choice: str) -> tuple:
    """Разрешает (platform, app_id) с учётом выбора пользователя."""
    if platform_choice == "Авто-определение":
        return parse_app_input(user_input)

    s = (user_input or "").strip()
    if not s:
        if platform_choice == "Google Play":
            return ('google_play', DEFAULT_GOOGLE_PLAY_ID)
        return ('app_store', DEFAULT_APP_STORE_ID)

    _, app_id = parse_app_input(s)
    target = 'google_play' if platform_choice == "Google Play" else 'app_store'
    return (target, app_id)


# ---------- Sidebar ----------
with st.sidebar:
    st.title("⚙️ Настройки")

    user_input = st.text_input(
        "ID приложения или ссылка",
        value="",
        placeholder="com.duolingo | 570060128 | ссылка на магазин",
        help="Оставьте пустым, чтобы использовать значения по умолчанию.",
    )

    platform_choice = st.radio(
        "Платформа",
        ["Авто-определение", "Google Play", "App Store"],
        index=0,
        help="При «Авто» платформа определяется из ID/ссылки.",
    )

    count = st.slider(
        "Количество отзывов",
        min_value=50, max_value=1000, value=500, step=50,
    )

    country = st.selectbox(
        "Страна (App Store)",
        ["us", "ru", "gb", "de", "fr", "es", "it"],
        index=0,
        help="Apple RSS отдаёт отзывы только для выбранной страны.",
    )

    force_refresh = st.checkbox("Сбросить кэш", value=False)

    start = st.button("🚀 Собрать отзывы", type="primary", use_container_width=True)


# ---------- Main ----------
st.title("🦉 Анализ отзывов Duolingo")
st.caption(
    "Сбор отзывов из Google Play и App Store для анализа болей "
    "и паттернов геймификации."
)


if start:
    try:
        platform, app_id = _resolve_platform(user_input, platform_choice)
    except ValueError as e:
        st.error(f"❌ Ошибка ввода: {e}")
        st.stop()

    st.info(f"**Платформа:** `{platform}`  |  **App ID:** `{app_id}`")

    if force_refresh:
        fetch_reviews.clear()

    with st.spinner(f"Собираем отзывы ({platform}) — это может занять 10–60 секунд..."):
        try:
            reviews_list = fetch_reviews(platform, app_id, count, country)
        except AppNotFoundError:
            st.error("App not found. Check the app ID.")
            st.stop()
        except ScrapingError as e:
            st.error(f"Ошибка сбора: {e}")
            st.stop()

    if not reviews_list:
        st.warning("No reviews found. Будет сохранён пустой CSV с заголовками.")

    st.session_state['reviews'] = reviews_list
    st.session_state['platform'] = platform
    st.session_state['app_id'] = app_id


# ---------- Отображение результатов ----------
if 'reviews' in st.session_state:
    reviews_list = st.session_state['reviews']
    platform = st.session_state['platform']
    app_id = st.session_state['app_id']

    df = build_dataframe(reviews_list, platform)
    analysis = analyze_reviews(df, platform)

    # --- Метрики ---
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Всего отзывов", analysis['total'])
    c2.metric(
        "Средний рейтинг",
        f"{analysis['avg_rating']:.2f} ★" if analysis['avg_rating'] is not None else "—",
    )
    c3.metric("Негативных (≤2)", analysis['negative_count'])

    if platform == 'google_play':
        replied = int((df['replyContent'].fillna('') != '').sum())
    else:
        replied = int((df['developerResponse'].fillna('') != '').sum())
    c4.metric("С ответом разработчика", replied)

    tab_analysis, tab_data, tab_pains, tab_download = st.tabs(
        ["📊 Анализ", "📋 Данные", "🔥 Боли", "💾 Скачать"]
    )

    with tab_analysis:
        st.subheader("Распределение оценок")
        if analysis['distribution']:
            dist_data = pd.DataFrame({
                'Оценка': [f"{s} ★" for s in [5, 4, 3, 2, 1]],
                'Количество': [analysis['distribution'].get(s, 0)
                               for s in [5, 4, 3, 2, 1]],
            }).set_index('Оценка')
            st.bar_chart(dist_data, use_container_width=True)

        st.subheader("Числовая статистика по рейтингу")
        score_col = _score_col(platform)
        stats = df[score_col].describe().to_frame(name='Значение')
        st.dataframe(stats, use_container_width=True)

    with tab_data:
        st.subheader(f"Все отзывы ({len(df)})")
        st.dataframe(df, use_container_width=True, height=500)

    with tab_pains:
        if analysis['top_words']:
            st.subheader("Топ-10 слов в негативных отзывах")
            words_df = pd.DataFrame(
                analysis['top_words'], columns=['Слово', 'Частота']
            ).set_index('Слово')
            st.bar_chart(words_df, use_container_width=True)

            st.subheader("Топ-5 болей пользователей")
            top5_data = [
                {'#': i, 'Боль': w, 'Частота': f}
                for i, (w, f) in enumerate(analysis['top_words'][:5], 1)
            ]
            st.table(pd.DataFrame(top5_data).set_index('#'))
        else:
            st.info("Негативных отзывов не найдено — болей не выявлено.")

    with tab_download:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = f"duolingo_reviews_{platform}_{ts}"

        csv_bytes = to_csv_bytes(df)
        md_text = build_markdown(df, platform, app_id, analysis)

        col_a, col_b = st.columns(2)
        with col_a:
            st.download_button(
                label="⬇️ Скачать CSV",
                data=csv_bytes,
                file_name=f"{base_name}.csv",
                mime="text/csv",
                use_container_width=True,
            )
        with col_b:
            st.download_button(
                label="⬇️ Скачать Markdown",
                data=md_text.encode('utf-8'),
                file_name=f"{base_name}.md",
                mime="text/markdown",
                use_container_width=True,
            )

        with st.expander("📄 Предпросмотр Markdown-отчёта"):
            st.markdown(md_text)

else:
    st.info(
        "👈 Настройте параметры в боковой панели и нажмите "
        "**🚀 Собрать отзывы**."
    )