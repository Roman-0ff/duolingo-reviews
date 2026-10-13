# Duolingo Reviews Analyzer

Streamlit-приложение для сбора и анализа отзывов о Duolingo
из Google Play и App Store.

## Возможности

- Ввод ID приложения или ссылки на страницу магазина.
- Авто-определение платформы (Google Play / App Store).
- Метрики: средний рейтинг, распределение оценок, число негативных отзывов.
- Топ-10 слов в негативных отзывах и таблица топ-5 «болей».
- Экспорт в CSV (UTF-8 с BOM) и Markdown.

## Локальный запуск

```bash
pip install -r requirements.txt
streamlit run app.py
```

Приложение откроется на http://localhost:8501.

## Деплой в Streamlit Cloud

1. Запушьте репозиторий на GitHub.
2. Откройте https://share.streamlit.io → New app.
3. Выберите репозиторий, ветку `main` и файл `app.py`.
4. Нажмите Deploy.