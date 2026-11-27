# Optional image for a NEW host. Do not point the existing Streamlit app at this file.
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY workbench/requirements.txt workbench/requirements.txt
RUN pip install --no-cache-dir -r workbench/requirements.txt
COPY quant_signal_lab_online quant_signal_lab_online
COPY workbench workbench
EXPOSE 8787
# Host platforms inject PORT. The process binds 0.0.0.0 and falls back to 8787.
CMD ["python", "-m", "workbench"]
