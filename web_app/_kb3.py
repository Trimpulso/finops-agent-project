with open("app.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if line.strip().startswith("st.session_state.messages.append({\"role\": \"assistant\", \"content\": final_response})"):
        target = i
        break

log_line = "                log_conversation(prompt, final_response, used_model)\n"
lines.insert(target + 1, log_line)

with open("app.py", "w", encoding="utf-8") as f:
    f.writelines(lines)

print("OK: log_conversation llamado en el flujo del chat")
