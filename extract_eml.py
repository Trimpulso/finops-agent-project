import email
import os
import html
import re
from pathlib import Path

eml_dir = r'D:\Github\finops-agent-project\notebookLM'

for eml_file in Path(eml_dir).glob('*.eml'):
    print(f'\n{"="*60}')
    print(f'ARCHIVO: {eml_file.name}')
    print("="*60)
    
    with open(eml_file, 'rb') as f:
        msg = email.message_from_bytes(f.read())
    
    print(f'ASUNTO: {msg.get("Subject", "N/A")}')
    print(f'FECHA: {msg.get("Date", "N/A")}')
    print()
    
    # Extract text body
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                break
            elif ct == "text/html" and not body:
                raw = part.get_payload(decode=True).decode("utf-8", errors="replace")
                body = re.sub(r'<[^>]+>', ' ', raw)
                body = re.sub(r'\s+', ' ', body).strip()
    else:
        body = msg.get_payload(decode=True).decode("utf-8", errors="replace")
    
    print(body[:2000])
