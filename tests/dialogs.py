"""Test helper: drive the body of an ``st.dialog`` under AppTest.

AppTest cannot rerun a dialog fragment on its own: a click reruns the whole script, where the button that opened the dialog
is False and the dialog is never drawn again, so a button INSIDE the dialog would be lost. This helper stands in for the
browser (the same idea as tests/test_accommodation_add_flow.py): the dialog's own function (``__wrapped__``, without the
dialog container) is drawn inline in the run that opens it and, at the top of every following run (through the patched
``accom.take_notice``, which every staff page calls first), until the dialog ends with ``st.rerun()`` (save or cancel),
exactly what a fragment rerun does. A refusal that only shows an error leaves the dialog open, as in the browser."""
from __future__ import annotations

from unittest import mock

import streamlit as st

from baror.ui import accom

FLAG = "_test_inline_dialogs"


def install(case, *targets) -> None:
    """``targets`` are (module, attribute name) pairs of ``st.dialog`` functions; undone at the end of the test."""
    real_rerun, real_take = st.rerun, accom.take_notice

    def rerun(scope="app"):                      # AppTest has no fragment reruns; ending the dialog closes it
        st.session_state.pop(FLAG, None)
        real_rerun()

    def take_notice():
        for body, args in list(st.session_state.get(FLAG, {}).values()):
            body(*args)
        real_take()

    patches = [mock.patch.object(st, "rerun", rerun), mock.patch.object(accom, "take_notice", take_notice)]
    for module, name in targets:
        body = getattr(module, name).__wrapped__

        def opener(*args, _body=body, _key=(module.__name__, name)):
            st.session_state.setdefault(FLAG, {})[_key] = (_body, args)
            _body(*args)

        patches.append(mock.patch.object(module, name, opener))
    for p in patches:
        p.start()
        case.addCleanup(p.stop)
