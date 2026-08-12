FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y build-essential curl && rm -rf /var/lib/apt/lists/*

COPY web_app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY web_app/ .
COPY consultas-tags/ /app/consultas-tags/

EXPOSE 8080

ENV STREAMLIT_SERVER_PORT=8080
ENV STREAMLIT_SERVER_ADDRESS=0.0.0.0
ENV STREAMLIT_SERVER_HEADLESS=true

HEALTHCHECK CMD curl --fail http://localhost:8080/_stcore/health

CMD ["streamlit", "run", "app.py", "--server.port=8080", "--server.address=0.0.0.0"]
