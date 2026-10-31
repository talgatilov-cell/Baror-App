import streamlit as st
import json
import os
import pandas as pd
from datetime import datetime
import base64
import plotly.express as px

# --- 1. הגדרות בסיס ---
st.set_page_config(page_title="דשבורד בראור קורס קצינים", page_icon="logo.png", layout="centered")

# --- 2. פונקציית תמונת רקע ---
def get_base64_of_bin_file(bin_file):
    try:
        with open(bin_file, 'rb') as f:
            data = f.read()
        return base64.b64encode(data).decode()
    except:
        return None

bg_base64 = get_base64_of_bin_file('image_bea22f.jpg')

# --- 3. ניהול נתונים ומשתמשים ---
USERS_FILE = os.environ.get('USERS_FILE', 'users.json')

def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_users(users):
    with open(USERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(users, f, ensure_ascii=False, indent=4)

if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.current_user = None
    st.session_state.email = None
    st.session_state.gender = None
    st.session_state.is_km = False

users = load_users()

for u in users:
    if "history" not in users[u]:
        users[u]["history"] = []
    if "is_km" not in users[u]:
        users[u]["is_km"] = False

# --- 4. הזרקת העיצוב (CSS) ---
if not st.session_state.logged_in:
    # עיצוב מסך התחברות - קופסה כהה חצי שקופה עם טקסט לבן
    st.markdown(f"""
        <style>
        .stApp {{
            direction: ltr !important; 
            background-image: url("data:image/jpg;base64,{bg_base64}");
            background-size: cover;
            background-position: center;
            background-attachment: fixed;
        }}
        .main .block-container, [data-testid="stMainBlockContainer"] {{
            background-color: rgba(0, 0, 0, 0.75) !important;
            border-radius: 15px !important;
            padding: 2rem !important; 
            margin-top: 1rem;
            box-shadow: 0 4px 15px rgba(0,0,0,0.5) !important;
            direction: rtl !important;
        }}
        p, h1, h2, h3, h4, h5, h6, span, label, li, ul, ol, .stMarkdown, .stText {{
            direction: rtl !important;
            text-align: right !important;
            font-family: 'Segoe UI', sans-serif;
            color: white !important;
        }}
        input {{
            direction: rtl !important;
            text-align: right !important;
            color: black !important;
            background-color: rgba(255,255,255,0.9) !important;
        }}
        .stButton>button {{ background-color: #4a5c40; color: white !important; border-radius: 8px; width: 100%; border: none; font-weight: bold; }}
        .stButton>button:hover {{ background-color: #3b4a33; }}
        .stDeployButton {{ display: none !important; }}
        </style>
    """, unsafe_allow_html=True)
else:
    # עיצוב מערכת פנימית - קופסה לבנה חצי-שקופה לקריאות מקסימלית של הטקסט השחור
    st.markdown(f"""
        <style>
        .stApp {{
            direction: ltr !important; 
            background-image: url("data:image/jpg;base64,{bg_base64}");
            background-size: cover;
            background-position: center;
            background-attachment: fixed;
        }}
        .main .block-container, [data-testid="stMainBlockContainer"] {{
            background-color: rgba(255, 255, 255, 0.90) !important;
            border-radius: 15px !important;
            padding: 2rem !important; 
            margin-top: 1rem;
            box-shadow: 0 4px 20px rgba(0,0,0,0.3) !important;
            direction: rtl !important;
        }}
        p, h1, h2, h3, h4, h5, h6, span, label, li, ul, ol, .stMarkdown, .stText, input, .stAlert div, .stRadio div {{
            direction: rtl !important;
            text-align: right !important;
            font-family: 'Segoe UI', sans-serif;
        }}
        [data-testid="stSidebar"] {{ background-color: rgba(240, 242, 246, 1) !important; direction: ltr !important; }}
        [data-testid="stSidebarUserContent"] {{ direction: rtl !important; }}
        .stButton>button {{ background-color: #4a5c40; color: white !important; border-radius: 8px; width: 100%; border: none; font-weight: bold; }}
        .stButton>button:hover {{ background-color: #3b4a33; }}
        .stDeployButton {{ display: none !important; }}
        @media (max-width: 768px) {{
            .main .block-container {{ padding: 1rem !important; width: 100% !important; }}
            [data-testid="stSidebar"] {{ min-width: 280px !important; }}
        }}
        </style>
    """, unsafe_allow_html=True)

# --- 5. מסך התחברות (לפני כניסה) ---
if not st.session_state.logged_in:
    st.title("דשבורד כשירות 🎖️")
    st.write("מערכת מעקב ושיפור ביצועי בר-אור לצוערים.")

    tab1, tab2 = st.tabs(["התחברות", "הרשמה"])

    with tab1:
        st.subheader("כניסה למערכת")
        login_email = st.text_input("אימייל", key="login_email")
        login_password = st.text_input("סיסמה", type="password", key="login_pass")

        if st.button("היכנס"):
            if login_email in users and users[login_email]['password'] == login_password:
                st.session_state.logged_in = True
                st.session_state.current_user = users[login_email]['name']
                st.session_state.email = login_email
                st.session_state.gender = users[login_email]['gender']
                st.session_state.is_km = users[login_email].get('is_km', False)
                st.rerun()
            else:
                st.error("אימייל או סיסמה שגויים.")

    with tab2:
        st.subheader("הרשמת צוער חדש")
        reg_name = st.text_input("שם מלא", key="reg_name")
        reg_email = st.text_input("אימייל", key="reg_email")
        reg_gender = st.selectbox("מגדר (קובע רף)", ["בחר/י", "זכר", "נקבה"], key="reg_gender")
        reg_km = st.checkbox("יש לי פטור ריצה (כ.מ) - יעד הליכה 6 ק״מ", key="reg_km")
        reg_password = st.text_input("בחר סיסמה", type="password", key="reg_pass")

        if st.button("הרשם"):
            if reg_email in users:
                st.error("האימייל כבר קיים.")
            elif reg_gender == "בחר/י" or not reg_name or not reg_password:
                st.warning("נא למלא את כל השדות.")
            else:
                users[reg_email] = {"name": reg_name, "gender": reg_gender, "password": reg_password, "is_km": reg_km, "history": []}
                save_users(users)
                st.success("נרשמת בהצלחה! עבור/י ללשונית התחברות.")

# --- 6. המערכת (אחרי התחברות) - ניווט ועמודים ---
else:
    with st.sidebar:
        st.image("logo.png", use_container_width=True)
        st.header("📌 ניווט פלוגתי")

        page = st.radio(
            "לאן תרצה/י לעבור?",
            ["האזור האישי 🎯", "טיפים לריצה 🏃", "טיפים לשכיבות סמיכה 💪", "סרטוני הדרכה 🎥"],
            label_visibility="collapsed"
        )

        st.divider()
        st.write(f"מחובר/ת כ: **{st.session_state.current_user}**")

        if st.button("התנתק מהמערכת"):
            st.session_state.logged_in = False
            st.rerun()

    if page == "האזור האישי 🎯":
        st.title(f"אהלן {st.session_state.current_user} 👋")
        
        if st.session_state.is_km:
            st.write(f"המדדים שלך מחושבים לפי רף: **{st.session_state.gender} (כ.מ - הליכה 6 ק״מ)**")
        else:
            st.write(f"המדדים שלך מחושבים לפי רף: **{st.session_state.gender}**")

        user_history = users[st.session_state.email]["history"]
        if user_history:
            st.subheader("📈 מגמת השיפור שלך")
            df = pd.DataFrame(user_history)
            df['זמן_בדקות'] = df['run_seconds'] / 60

            col_chart1, col_chart2 = st.columns(2)
            with col_chart1:
                chart_title = "זמן הליכה בדקות (נמוך = טוב)" if st.session_state.is_km else "זמן ריצה בדקות (נמוך = טוב)"
                st.markdown(f"<p style='text-align: center; font-weight: bold; color: #4a5c40;'>{chart_title}</p>", unsafe_allow_html=True)
                fig1 = px.line(df, x='date', y='זמן_בדקות', markers=True)
                fig1.update_traces(line_color='#4a5c40', marker=dict(size=10, color='#4a5c40'))
                # הוספת רקע לבן ואטום יותר ספציפית לגרפים
                fig1.update_layout(xaxis_title="תאריך", yaxis_title="דקות", plot_bgcolor='rgba(255,255,255,0.8)', paper_bgcolor='rgba(255,255,255,0.8)', margin=dict(l=0, r=0, t=10, b=0))
                st.plotly_chart(fig1, use_container_width=True)

            with col_chart2:
                st.markdown("<p style='text-align: center; font-weight: bold; color: #4a5c40;'>שכיבות סמיכה (גבוה = טוב)</p>", unsafe_allow_html=True)
                fig2 = px.line(df, x='date', y='pushups', markers=True)
                fig2.update_traces(line_color='#4a5c40', marker=dict(size=10, color='#4a5c40'))
                # הוספת רקע לבן ואטום יותר ספציפית לגרפים
                fig2.update_layout(xaxis_title="תאריך", yaxis_title="חזרות", plot_bgcolor='rgba(255,255,255,0.8)', paper_bgcolor='rgba(255,255,255,0.8)', margin=dict(l=0, r=0, t=10, b=0))
                st.plotly_chart(fig2, use_container_width=True)

        st.divider()
        st.subheader("הזנת תוצאות בוחן חדש")
        with st.form("baror_form"):
            test_date = st.date_input("תאריך האימון", value=datetime.today())
            col1, col2 = st.columns(2)
            with col1:
                if st.session_state.is_km:
                    run_time = st.text_input("זמן הליכה 6 ק״מ (יעד: עד 66:00)")
                else:
                    run_time = st.text_input("זמן ריצה 3 ק״מ (למשל 15:40)")
            with col2:
                pushups = st.number_input("שכיבות סמיכה (כמות)", min_value=0, max_value=100, step=1)

            submitted = st.form_submit_button("שמור תוצאות 💾")

        if submitted:
            if not run_time or ":" not in run_time:
                st.error("נא להזין את הזמן בפורמט תקין (למשל 14:30 או 64:00).")
            else:
                try:
                    minutes, seconds = map(int, run_time.split(':'))
                    total_seconds = minutes * 60 + seconds
                    run_status = ""
                    pushups_status = ""

                    if st.session_state.is_km:
                        if total_seconds <= 3960:
                            run_status = "עובר ✅ (כ.מ)"
                        else:
                            run_status = "נכשל ❌ (כ.מ)"
                    else:
                        if st.session_state.gender == "זכר":
                            if total_seconds <= 750: run_status = "מצטיין 🏆"
                            elif total_seconds <= 940: run_status = "עובר ✅"
                            else: run_status = "נכשל ❌"
                        else:
                            if total_seconds <= 880: run_status = "מצטיינת 🏆"
                            elif total_seconds <= 1180: run_status = "עובר ✅"
                            else: run_status = "נכשל ❌"

                    if st.session_state.gender == "זכר":
                        if pushups >= 36: pushups_status = "מצטיין 🏆"
                        elif pushups >= 22: pushups_status = "עובר ✅"
                        else: pushups_status = "נכשל ❌"
                    else:
                        if pushups >= 30: pushups_status = "מצטיינת 🏆"
                        elif pushups >= 11: pushups_status = "עובר ✅"
                        else: pushups_status = "נכשל ❌"

                    new_record = {"date": test_date.strftime("%Y-%m-%d"), "run_seconds": total_seconds, "pushups": pushups}
                    users[st.session_state.email]["history"].append(new_record)
                    save_users(users)

                    st.success("הנתונים נשמרו בהצלחה!")
                    st.write(f"👟 **סטטוס אירובי:** {run_status}")
                    st.write(f"💪 **סטטוס שכיבות סמיכה:** {pushups_status}")
                    st.button("טען גרפים מחדש 🔄", on_click=st.rerun)

                except ValueError:
                    st.error("שגיאה בקריאת הזמן. ודא/י שהקלדת רק מספרים ונקודתיים.")

    elif page == "טיפים לריצה 🏃":
        st.title("איך לשפר את זמני הריצה שלך? 🏃")
        st.write("ריצת 3 קילומטרים דורשת שילוב של מהירות, סיבולת לב-ריאה וחוסן מנטלי. הנה שיטות עבודה מוכחות:")
        st.subheader("1. אימוני אינטרוולים (הפוגות)")
        st.info("מומלץ לשלב פעם בשבוע: רוצים לשפר מהירות? תרוצו מהר. רוצו 400 מטר בקצב גבוה (סביב 85% יכולת), ואז 200 מטר של הליכה קלה להתאוששות. חזרו על זה 6-8 פעמים.")
        st.subheader("2. ריצת נפח (Zone 2)")
        st.info("אימון אחד בשבוע צריך להיות מוקדש לריצה ארוכה ואיטית. המטרה היא לרוץ 5-7 קילומטרים בקצב שמאפשר לכם לנהל שיחה קלה. זה בונה את התשתית האירובית ומלמד את הגוף לייעל את צריכת החמצן.")
        st.subheader("3. חימום לפני הבוחן")
        st.info("אל תזנקו מהמקום. 10 דקות לפני הזינוק בצעו ריצה קלה מאוד, ולאחריה מתיחות דינמיות כמו הנפות רגליים, מכרעים (לאנג'ים) וסיבובי אגן. זה מונע את תחושת ה'רגליים הכבדות' בקילומטר הראשון.")

    elif page == "טיפים לשכיבות סמיכה 💪":
        st.title("תוכנית פעולה לשכיבות סמיכה 💪")
        st.write("הסוד בשכיבות סמיכה הוא עקביות וטכניקה נכונה. עבודה נכונה תמנע פציעות ותגדיל את כמות החזרות.")
        st.subheader("1. עבודה על השלב האקצנטרי (הירידה)")
        st.success("השריר נבנה לא רק בדחיפה אלא גם בבלימה. נסו לרדת לקרקע בספירה איטית של 3 שניות, ולדחוף למעלה בשנייה אחת. אם קשה לכם לדחוף, רדו לאט לקרקע, ודחפו עם הברכיים.")
        st.subheader("2. שיטת ה'Grease the Groove'")
        st.success("במקום לעשות אימון אחד קורע פעם ביומיים, עשו סטים קטנים וקלים לאורך כל היום. למשל: כל פעם שנכנסים למגורים, עושים 5-10 שכיבות סמיכה. זה בונה חיבור עצבי חזק לשריר בלי לעייף אותו לחלוטין.")
        st.subheader("3. מיקום ידיים ומרפקים")
        st.success("שמרו את המרפקים קרובים לגוף (בזווית של כ-45 מעלות). אל תפתחו אותם החוצה ל-90 מעלות – זה שם עומס מסוכן על הכתפיים. כיווץ ישבן ובטן ישמור על גב ישר כמו פלס וימנע בזבוז אנרגיה.")

    elif page == "סרטוני הדרכה 🎥":
        st.title("סרטוני אימון וטכניקה 🎥")
        st.write("ריכזנו עבורכם סרטונים מומלצים שיעזרו לדייק את הטכניקה ולשפר תוצאות לקראת הבוחן:")
        st.subheader("טכניקה תקנית לשכיבות סמיכה")
        st.video("https://www.youtube.com/watch?v=IODxDxX7oi4") 
        st.divider()
        st.subheader("דגשים לריצת 3 ק\"מ ושיפור טכניקת ריצה")
        st.video("https://www.youtube.com/watch?v=9L2b2khySLE")
