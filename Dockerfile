# Playwright's image already has Chromium + all OS deps. Keep the tag in step with requirements.txt.
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./

# ./auth (logged-in profile) and ./runs (logs + screenshots) are mounted at run time.
EXPOSE 8100
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8100"]
