import smtplib
import random
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os

SMTP_HOST = "smtp.mail.ru"
SMTP_PORT = 465
SMTP_EMAIL = os.environ.get("SMTP_EMAIL", "diogram@list.ru").strip()
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "").strip()


def generate_code():
    """Генерирует 9-значный код: от 100000000 до 999999999"""
    return random.randint(100000000, 999999999)


def send_verification_code(to_email: str, code: int) -> bool:
    """Отправляет код подтверждения на email"""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Diogram - Код подтверждения"
        msg["From"] = f"Diogram <{SMTP_EMAIL}>"
        msg["To"] = to_email

        text = f"""Привет!

Твой код подтверждения для Diogram:

{code}

Код действует 15 минут.

Если ты не регистрировался в Diogram - просто проигнорируй это письмо.

--
Diogram
"""

        html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: Arial, sans-serif; background:#f5f5f5; padding:20px;">
  <div style="max-width:500px; margin:0 auto; background:#fff; border-radius:12px; padding:30px;">
    <h2 style="color:#5b8bff; margin:0 0 20px;">Diogram</h2>
    <p style="color:#333; font-size:16px;">Привет!</p>
    <p style="color:#333; font-size:16px;">Твой код подтверждения:</p>
    <div style="background:#f0f4ff; border-radius:8px; padding:20px; text-align:center; margin:20px 0;">
      <span style="font-size:32px; font-weight:bold; color:#5b8bff; letter-spacing:4px;">{code}</span>
    </div>
    <p style="color:#888; font-size:13px;">Код действует 15 минут.</p>
    <p style="color:#888; font-size:13px;">Если ты не регистрировался в Diogram — проигнорируй это письмо.</p>
    <hr style="border:none; border-top:1px solid #eee; margin:20px 0;">
    <p style="color:#aaa; font-size:12px; text-align:center;">Diogram Messenger</p>
  </div>
</body>
</html>"""

        msg.attach(MIMEText(text, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))

        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.sendmail(SMTP_EMAIL, to_email, msg.as_string())

        print(f"[SMTP] Код {code} отправлен на {to_email}")
        return True

    except Exception as e:
        print(f"[SMTP ERROR] {e}")
        return False
