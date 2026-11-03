# Baror App

Hebrew (RTL) Streamlit app for officer-course cadets.

## How to test

    python -m unittest discover -s tests -t .

Tests use only `unittest` and `streamlit.testing.v1.AppTest`. Install the pinned dependencies with
`pip install --require-hashes --only-binary=:all: --no-deps -r requirements.lock`.
