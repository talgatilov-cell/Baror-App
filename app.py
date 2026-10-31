import streamlit as st
import json
import os

# הגדרת הקובץ בו יישמרו פרטי המשתמשים
USERS_FILE = 'users.json'

# פונקציה שטוענת את המשתמשים מהקובץ (אם הוא קיים)
def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

# פונקציה ששומרת משתמש חדש לקובץ
def save_users(users):
    with open(USERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(users, f, ensure_ascii=False, indent=4)

# יצירת "זיכרון" לאפליקציה כדי שתזכור אם אנחנו מחוברים
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.current_user = None
    st.session_state.gender = None

users = load_users()

# --- מסך התחברות והרשמה (מוצג רק אם לא מחוברים) ---
if not st.session_state.logged_in:
    st.title("דשבורד בראור - פלוגת חץ 🎯")
    
    # יצירת שתי לשוניות נפרדות
    tab1, tab2 = st.tabs(["התחברות", "הרשמה לפלוגה"])

    # מה קורה בלשונית ההתחברות:
    with tab1:
        st.subheader("כניסה למערכת")
        login_email = st.text_input("אימייל", key="login_email")
        login_password = st.text_input("סיסמה", type="password", key="login_pass")
        
        if st.button("היכנס"):
            if login_email in users and users[login_email]['password'] == login_password:
                # אם הפרטים נכונים, מעדכנים את הזיכרון של האפליקציה
                st.session_state.logged_in = True
                st.session_state.current_user = users[login_email]['name']
                st.session_state.gender = users[login_email]['gender']
                st.rerun() # מרענן את המסך כדי להכניס אותנו פנימה
            else:
                st.error("אימייל או סיסמה שגויים, נסה שוב.")

    # מה קורה בלשונית ההרשמה:
    with tab2:
        st.subheader("משתמש חדש? הרשם כאן")
        reg_name = st.text_input("שם מלא", key="reg_name")
        reg_email = st.text_input("אימייל", key="reg_email")
        reg_gender = st.selectbox("מגדר (קריטי לחישוב הציון)", ["בחר/י", "זכר", "נקבה"], key="reg_gender")
        reg_password = st.text_input("בחר סיסמה", type="password", key="reg_pass")

        if st.button("הרשם"):
            if reg_email in users:
                st.error("האימייל הזה כבר קיים במערכת.")
            elif reg_gender == "בחר/י" or not reg_name or not reg_password:
                st.warning("נא למלא את כל השדות ולבחור מגדר.")
            else:
                # שמירת הנתונים במילון ושליחה לפונקציית השמירה
                users[reg_email] = {
                    "name": reg_name,
                    "gender": reg_gender,
                    "password": reg_password
                }
                save_users(users)
                st.success("נרשמת בהצלחה! עבור/י ללשונית 'התחברות' כדי להיכנס.")

# --- האזור האישי (מוצג רק אחרי שמתחברים) ---
else:
    st.title(f"אהלן {st.session_state.current_user} 👋")
    st.write(f"המדדים שלך מחושבים לפי רף: **{st.session_state.gender}**")
    
    st.info("כאן נכניס בהמשך את כפתורי הזנת התוצאות והגרפים!")
    
    if st.button("התנתק"):
        st.session_state.logged_in = False
        st.rerun()