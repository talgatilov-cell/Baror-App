"""No barrier / no patching: N sessions press 'register' at the same instant (natural timing)."""
import threading, sys
from helpers import *
N = int(sys.argv[1]) if len(sys.argv) > 1 else 8
for trial in range(int(sys.argv[2]) if len(sys.argv) > 2 else 3):
    p = make_users_file({f"seed{i}@x.y": user(f"s{i}", history=[{"date":"2026-01-01","run_seconds":900,"pushups":20}]*50) for i in range(300)})
    ats = []
    for i in range(N):
        at = new_app(); at.run()
        at.text_input(key="reg_name").set_value(f"n{i}"); at.text_input(key="reg_email").set_value(f"new{i}@x.y")
        at.selectbox(key="reg_gender").select("זכר"); at.text_input(key="reg_pass").set_value("pw")
        ats.append(at)
    go = threading.Barrier(N); res = [None]*N
    def work(i):
        go.wait(); btn(ats[i], "הרשם").click()
        try: ats[i].run(); res[i] = (len(ats[i].success), len(exc(ats[i])))
        except Exception as e: res[i] = repr(e)
    ts = [threading.Thread(target=work, args=(i,)) for i in range(N)]
    [t.start() for t in ts]; [t.join() for t in ts]
    try:
        stored = [k for k in read(p) if k.startswith("new")]
        print(f"trial {trial}: told-success={sum(1 for r in res if isinstance(r,tuple) and r[0]==1)}/{N}, "
              f"exceptions={sum(r[1] for r in res if isinstance(r,tuple))}, actually stored={len(stored)}")
    except Exception as e:
        print(f"trial {trial}: users.json unreadable afterwards: {e!r}")
