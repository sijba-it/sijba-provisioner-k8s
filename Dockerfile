FROM docker.io/library/python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY catalog.py kube.py connectors.py main.py ./

ENV LISTEN_HOST=0.0.0.0
ENV PORT=8082
EXPOSE 8082
USER 65534
CMD ["python", "main.py"]
