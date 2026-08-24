import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

REPORT_FILE = Path("reports/latest.md")
EMAIL_USERNAME = os.environ["EMAIL_USERNAME"]
EMAIL_APP_PASSWORD = os.environ["EMAIL_APP_PASSWORD"]
EMAIL_TO = os.environ["EMAIL_TO"]

if not REPORT_FILE.exists():
    raise RuntimeError("reports/latest.md not found")

report = REPORT_FILE.read_text(encoding="utf-8")
message = EmailMessage()
message["Subject"] = "GT7 Career Tracker — Weekly Summary"
message["From"] = EMAIL_USERNAME
message["To"] = EMAIL_TO
message.set_content(report)

with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
    smtp.login(EMAIL_USERNAME, EMAIL_APP_PASSWORD)
    smtp.send_message(message)

print("Weekly GT7 Career Tracker email sent successfully.")
