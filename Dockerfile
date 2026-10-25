# Connector order forms ship inside each connector image as /connector.json;
# collect them here. Add a connector = add a FROM + COPY pair.
FROM ghcr.io/sijba-it/sijba-out-pushover:latest AS pushover
FROM ghcr.io/sijba-it/sijba-telegram:latest AS telegram

FROM docker.io/library/python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY catalog.py kube.py connectors.py main.py ./
COPY --from=pushover /connector.json catalog.d/pushover.json
COPY --from=telegram /connector.json catalog.d/telegram.json
RUN python -c "from catalog import build_catalog; print(build_catalog('catalog.d', {}))"

ENV LISTEN_HOST=0.0.0.0
ENV PORT=8082
EXPOSE 8082
USER 65534
CMD ["python", "main.py"]
