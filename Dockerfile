FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml README.md ./
COPY content_desk ./content_desk
COPY static ./static

RUN uv pip install --system .

ENV CONTENT_DESK_HOST=0.0.0.0
ENV CONTENT_DESK_PORT=5199
EXPOSE 5199

CMD ["content-desk"]
