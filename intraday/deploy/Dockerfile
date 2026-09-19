# Intraday recorder: read-only market-data recorder. No orders, no broker mutation.
# Build from the stock-intel repo root:  docker build -f intraday/deploy/Dockerfile -t stock-intel/intraday-recorder:0.1.0 .
FROM python:3.13-slim
WORKDIR /app
COPY intraday/requirements.txt intraday/requirements.txt
RUN pip install --no-cache-dir -r intraday/requirements.txt
COPY intraday/ intraday/
ENV PYTHONUNBUFFERED=1
# Continuous poll loop (no --once). Secrets + config come from env (k8s Secret/ConfigMap).
CMD ["python", "-m", "intraday"]
