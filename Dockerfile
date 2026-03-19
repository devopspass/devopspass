# FROM --platform=linux/amd64 python:3.11-slim
FROM python:3.11-slim

WORKDIR /workspace

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    curl \
    git \
    openssh-client \
    nodejs \
    npm \
    procps \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://gh.io/copilot-install | bash

COPY api/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /tmp/requirements.txt

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV FASTAPI_HOST=0.0.0.0
ENV FASTAPI_PORT=10818
ENV FASTAPI_RELOAD=true

EXPOSE 10818

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["api"]
