FROM python:3.13-slim
LABEL org.opencontainers.image.source="https://github.com/enlinxu/agentic-robot-shop"
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py *.txt sre.html ./
ENV STATE_DIR=/state
CMD ["uvicorn", "agent:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
