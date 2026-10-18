FROM python:3.12-slim

RUN useradd --create-home --uid 10001 agent
WORKDIR /app

COPY . .
RUN pip install --no-cache-dir . \
    && mkdir -p /data \
    && chown agent:agent /data

USER agent

# Public mode is switched on by AGENT_TEAM_PUBLIC_HOST, which the deploy script sets. The port comes from $PORT.
ENV PYTHONUNBUFFERED=1 \
    PORT=8080
EXPOSE 8080
CMD ["agent-team", "serve", "--data-dir", "/data"]
