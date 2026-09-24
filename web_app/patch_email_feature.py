import shutil

PATH = "app.py"
BACKUP = "app.py.bak"

with open(PATH, "r", encoding="utf-8") as f:
    content = f.read()

shutil.copy(PATH, BACKUP)
print(f"Respaldo creado en {BACKUP}")

replacements = []

# 1. Imports nuevos (smtplib + email)
old1 = '''import re
from datetime import UTC, datetime, timedelta'''
new1 = '''import re
import smtplib
from datetime import UTC, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText'''
replacements.append(("imports", old1, new1))

# 2. Nuevas funciones send_email_smtp / render_email_button
old2 = '''        raise KeyError(f"Falta la configuracion requerida: {name}")
    return None'''
new2 = old2 + '''


def send_email_smtp(to_address: str, subject: str, body: str) -> tuple[bool, str]:
    try:
        to_address = (to_address or "").strip()
        if not to_address:
            return False, "Debes indicar un correo destino."

        gmail_address = _get_setting("GMAIL_ADDRESS", required=False)
        gmail_password = _get_setting("GMAIL_APP_PASSWORD", required=False)
        if not gmail_address or not gmail_password:
            return False, "Falta configurar GMAIL_ADDRESS / GMAIL_APP_PASSWORD en el servicio."

        msg = MIMEMultipart()
        msg["From"] = gmail_address
        msg["To"] = to_address
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
            server.starttls()
            server.login(gmail_address, gmail_password)
            server.sendmail(gmail_address, [to_address], msg.as_string())

        return True, f"Correo enviado a {to_address}."
    except Exception as e:
        return False, f"No se pudo enviar el correo: {e}"


def render_email_button(message_index: int, content: str) -> None:
    with st.expander("Enviar por correo"):
        to_address = st.text_input(
            "Correo destino",
            key=f"email_to_{message_index}",
            placeholder="destinatario@ejemplo.com",
        )
        if st.button("Enviar por correo", key=f"email_send_{message_index}"):
            ok, info = send_email_smtp(
                to_address,
                subject="Respuesta FinOps Chat Agent",
                body=content,
            )
            if ok:
                st.success(info)
            else:
                st.error(info)'''
replacements.append(("funciones de email", old2, new2))

# 3. Boton en el historial de mensajes
old3 = '''for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])'''
new3 = '''for idx, message in enumerate(st.session_state.messages):
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message["role"] == "assistant":
            render_email_button(idx, message["content"])'''
replacements.append(("boton en historial", old3, new3))

# 4. Boton en la respuesta nueva
old4 = '''                final_response, used_model = ask_with_fallback(prompt)
                st.markdown(final_response)
                st.caption(f"Respondido con: {used_model}")
                st.session_state.messages.append({"role": "assistant", "content": final_response})
                log_conversation(prompt, final_response, used_model)'''
new4 = old4 + '''
                render_email_button(len(st.session_state.messages) - 1, final_response)'''
replacements.append(("boton en respuesta nueva", old4, new4))

for label, old, new in replacements:
    count = content.count(old)
    if count != 1:
        print(f"ERROR: '{label}' se encontro {count} veces (se esperaba 1). No se aplico ningun cambio.")
        raise SystemExit(1)
    content = content.replace(old, new, 1)
    print(f"OK: aplicado '{label}'")

with open(PATH, "w", encoding="utf-8") as f:
    f.write(content)

print("Listo. app.py actualizado. Revisa con: git diff app.py")