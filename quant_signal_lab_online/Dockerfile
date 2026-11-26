FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 STREAMLIT_THEME_BASE=dark
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py market_data.py indicators.py signals.py charts.py backtest.py paper.py legacy.py ui.py live_market.py comparison.py quote_ui.py verify_market.py perpetual_market.py perpetual_ui.py swap_data.py realtime_engine.py realtime_ui.py presets.py strategy_modules.py settings_ui.py measure_realtime.py macro_fib.py preset_backtest.py ./
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health', timeout=3)" || exit 1
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
