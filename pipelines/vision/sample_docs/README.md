# sample_docs

Drop a real PDF here to run the vision pipeline against it manually via the
CLI. This is separate from `../tests/fixtures.py`, which generates its own
synthetic PDFs on the fly for the automated test suite - nothing here is
read by `pytest`.

```bash
cd pipelines/vision
uv run python -m nexus_vision sample_docs/your-file.pdf > output.json
```
