param(
  [string]$TenantId = "68485601-fbbc-47c6-b156-3e1a7e0a4434",
  [string]$WorkspaceId = "7ba4e329-d51b-4b50-84c2-fb602fe0fa39"
)

$ErrorActionPreference = "Stop"

Write-Host "1) Verificando sesion Azure..." -ForegroundColor Cyan
$null = az account show 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host "Sesion no encontrada. Iniciando login..." -ForegroundColor Yellow
  az login --tenant $TenantId | Out-Null
}

Write-Host "2) Obteniendo token Power BI..." -ForegroundColor Cyan
$env:POWERBI_ACCESS_TOKEN = az account get-access-token `
  --tenant $TenantId `
  --resource https://analysis.windows.net/powerbi/api `
  --query accessToken -o tsv

if ([string]::IsNullOrWhiteSpace($env:POWERBI_ACCESS_TOKEN)) {
  throw "No se obtuvo POWERBI_ACCESS_TOKEN"
}

$env:POWERBI_TENANT_ID = $TenantId
$env:POWERBI_WORKSPACE_ID = $WorkspaceId

Write-Host "3) Iniciando Streamlit..." -ForegroundColor Green
& ".\.venv\Scripts\python.exe" -m streamlit run ".\web_app\app.py"
