with open("app.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

# Add debug line inside ask_with_fallback right after tag_mode assignment
for i, line in enumerate(lines):
    if line.strip() == "tag_mode = _is_tag_query(prompt)":
        lines.insert(i + 1, '    st.write(f"DEBUG tag_mode={tag_mode!r} prompt={prompt[:50]!r}")\n')
        print(f"Debug inserted at line {i+1}")
        break

with open("app.py", "w", encoding="utf-8") as f:
    f.writelines(lines)

print("OK")
