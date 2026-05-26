FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8010
ENV ANNOTATION_SPEED=2
ENV LABEL_W_AGREE=70
ENV LABEL_W_DISAGREE=20
ENV LABEL_W_PARTIAL=10

EXPOSE 8010

CMD ["python", "main.py"]
