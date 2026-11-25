FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 STREAMLIT_THEME_BASE=dark
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py market_data.py indicators.py signals.py charts.py backtest.py paper.py legacy.py ui.py live_market.py comparison.py quote_ui.py verify_market.py perpetual_market.py perpetual_ui.py freshness.py evidence.py presets.py mean_reversion.py research_http.py research_store.py hyperliquid_public.py okx_public.py bitget_structure.py github_public.py platform_status.py preset_ui.py platform_ui.py ./
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health', timeout=3)" || exit 1
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
