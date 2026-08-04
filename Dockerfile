FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["sh", "-c", "python webhook_v2.py & streamlit run app_v2.py --server.port=8080 --server.address=0.0.0.0"]
