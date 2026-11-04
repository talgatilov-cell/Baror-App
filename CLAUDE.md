# CLAUDE.md — Baror App

Hebrew (RTL), mobile-first Streamlit app for officer-course cadets: log 3 km run and push-up results, see progress.
Full plan: `IMPLEMENTATION_BRIEF.md`. Work task by task.

## Hard rules
- **Never add a dependency.** Runtime: `streamlit`, `pandas`, `plotly` only. Tests: `unittest` + `streamlit.testing.v1.AppTest`. If something needs another package, stop and ask the owner.
- One task = one commit on a feature branch. Run `python -m unittest discover -s tests -t .` before each commit.
- Never commit secrets, user data, `.db`, `users.json`. Never log passwords, tokens or raw emails.
- Do not change threshold numbers; they live in `baror/domain/thresholds.json`, recorded as "confirmed by owner, 2026-11-02" (no official document cited). Grade each test separately: no overall pass/fail verdict until the owner explains "כ.מ" and the overall rule.
- All Hebrew UI text in `baror/messages_he.py`. Gender-neutral, impersonal phrasing (noun/infinitive labels, no slash forms).
- `baror/domain` and `baror/storage` never import `streamlit`.
- Every query touching results is scoped by `user_id`. No `st.cache_*` on user data. Identity comes only from server-side session state.
- Registration is open: cadets register themselves and pick a platoon; every new account is a cadet. Staff roles (instructor, chief) are granted only by an admin or a chief to an already registered account, never self-claimed or pre-listed; admins are created only by the operator script. Refusals stay one generic message. Bot and rate protection (honeypot, signed token, minimum fill time, per-IP and app-wide limits) and the hourly sign-up cap stay mandatory. Password minimum is 8 characters.
- Never put user input into `st.html`, `unsafe_allow_html` or JavaScript.
- Custom CSS: use `key=` containers (`.st-key-<key>`), logical properties, no hashed `st-emotion-cache-*` selectors, no `!important` without a comment.
- Design for 390 px width first; touch targets ≥ 48 px; contrast ≥ 4.5:1 text, 3:1 UI.

## Commands
```
python -m unittest discover -s tests -t . -v
streamlit run app.py
```

## Definition of done
Tests added and passing, whole suite green, checked at 390×844 and 1366×850, no new dependency, findings listed in the commit message.
