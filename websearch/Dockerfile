# The web search service. The only Nexus component that talks to the
# internet (through SearXNG beside it), so it runs in its own container.
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY nexus_websearch ./nexus_websearch
RUN pip install --no-cache-dir .
ENV PYTHONUNBUFFERED=1
EXPOSE 8004
CMD ["uvicorn", "nexus_websearch.main:app", "--host", "0.0.0.0", "--port", "8004"]
