FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
WORKDIR /app
COPY requirements.txt .
RUN python -m pip install --no-cache-dir --require-hashes --disable-pip-version-check \
    --root-user-action=ignore \
    --no-deps --platform any --implementation py --python-version 3.12 --abi none \
    --target /opt/python -r requirements.txt
ENV PYTHONPATH=/opt/python
ENV PYTHONDONTWRITEBYTECODE=1
COPY bridge.py .
USER 65532:65532
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/healthz', timeout=2).read()"
ENTRYPOINT ["python", "-u", "/app/bridge.py"]
