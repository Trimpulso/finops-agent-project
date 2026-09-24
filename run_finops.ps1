param(
  [string]$Repo = "D:\Github\finops-agent-project"
)

Set-Location $Repo

$py = @"
from pathlib import Path

p = Path("web_app/app.py")
t = p.read_text(encoding="utf-8")
lines = t.splitlines()

# Elimina import/tools previos para reconstruirlos bien
cleaned = []
for ln in lines:
    s = ln.strip()
    if s.startswith("from agent_core import "):
        continue
    if s.startswith("TOOLS = ["):
        continue
    cleaned.append(ln)

# Inserta encabezado correcto
cleaned.insert(0, "TOOLS = [get_cost_summary_by_service, get_daily_trend_for_service]")
cleaned.insert(0, "from agent_core import get_cost_summary_by_service, get_daily_trend_for_service")

p.write_text("\n".join(cleaned) + "\n", encoding="utf-8")
print("OK: app.py reparado")
"@

$tmp = Join-Path $env:TEMP "fix_finops_tools.py"
Set-Content -Path $tmp -Value $py -Encoding UTF8
python $tmp
if ($LASTEXITCODE -ne 0) {
  Write-Host "ERROR: fallo reparando app.py"
  exit 1
}
Remove-Item $tmp -Force -ErrorAction SilentlyContinue

python -m py_compile .\web_app\app.py
if ($LASTEXITCODE -ne 0) {
  Write-Host "ERROR: app.py no compila"
  exit 1
}

streamlit run .\web_app\app.py
