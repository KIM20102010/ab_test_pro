FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir streamlit pandas numpy scipy matplotlib Pillow Flask requests supabase python-dotenv

COPY . .

CMD ["streamlit", "run", "app_v2.py", "--server.port=8080", "--server.address=0.0.0.0"]
