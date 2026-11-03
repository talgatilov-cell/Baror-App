# Baseline (T0.1)

Run on 2026-11-03 against the untouched `app.py` of `main` (commit `fddcff9`), Streamlit 1.66.1, Python 3.12, uid 1000.

    BAROR_APP_DIR=$(pwd) python -m unittest discover -s tests/regression

Result: 54 tests, **9 pass, 45 fail, 0 error, 0 skip**.

The report quotes 9 / 43 / 1 / 1. The differences are explained: the same-email race test is timing dependent (fail here, error there), and the read-only-directory test ran here because the user is not root (it skips as root).

`tests/regression/` assumes the JSON storage of the old app and is not part of `python -m unittest discover -s tests -t .` (the folder has no `__init__.py` on purpose). It stays as the opt-in acceptance suite until its fixtures are adapted to the SQLite path.
