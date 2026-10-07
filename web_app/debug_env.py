import os
import sys
import requests
import json
import base64
import warnings
warnings.filterwarnings('ignore')

SECRET_MANAGER_MAP = {
    'GEMINI_API_KEY': 'gemini-api-key',
    'AZURE_CLIENT_SECRET': 'azure-client-secret',
    'AZURE_CLIENT_ID': 'azure-client-id',
    'AZURE_TENANT_ID': 'azure-tenant-id',
    'AZURE_SUBSCRIPTION_ID': 'azure-subscription-id',
    'POWERBI_CLIENT_SECRET': 'powerbi-client-secret',
    'POWERBI_CLIENT_ID': 'powerbi-client-id',
    'POWERBI_TENANT_ID': 'powerbi-tenant-id',
    'POWERBI_WORKSPACE_ID': 'powerbi-workspace-id',
    'GMAIL_ADDRESS': 'gmail-address',
    'GMAIL_APP_PASSWORD': 'gmail-app-password',
    'AWS_ACCESS_KEY_ID': 'aws-access-key-id',
    'AWS_SECRET_ACCESS_KEY': 'aws-secret-access-key',
    'AWS_DEFAULT_REGION': 'aws-default-region',
}

def _fetch_from_secret_manager(secret_name: str, project_id: str = 'project-5f47ed36-9aec-4f46-a30') -> tuple[str | None, str | None]:
    errors = []
    # 1. SDK
    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        name = f'projects/{project_id}/secrets/{secret_name}/versions/latest'
        response = client.access_secret_version(name=name)
        return response.payload.data.decode('UTF-8').strip(), None
    except Exception as e:
        errors.append(f'SDK: {e}')

    # 2. REST API
    try:
        import google.auth
        import google.auth.transport.requests
        credentials, gcp_project = google.auth.default(scopes=['https://www.googleapis.com/auth/cloud-platform'])
        auth_req = google.auth.transport.requests.Request()
        credentials.refresh(auth_req)
        
        target_project = gcp_project or project_id
        url = f'https://secretmanager.googleapis.com/v1/projects/{target_project}/secrets/{secret_name}/versions/latest:access'
        headers = {
            'Authorization': f'Bearer {credentials.token}',
            'x-goog-user-project': target_project
        }
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            payload_data = resp.json().get('payload', {}).get('data', '')
            return base64.b64decode(payload_data).decode('UTF-8').strip(), None
        else:
            errors.append(f'REST HTTP {resp.status_code}: {resp.text[:100]}')
    except Exception as e:
        errors.append(f'REST: {e}')

    return None, ' | '.join(errors)

def get_setting(name: str) -> tuple[str | None, str | None]:
    val = os.getenv(name)
    if val and val.strip():
        return val.strip(), 'Obtenido desde variable de entorno local'
    sm_name = SECRET_MANAGER_MAP.get(name)
    if sm_name:
        return _fetch_from_secret_manager(sm_name)
    return None, 'No mapeado en SECRET_MANAGER_MAP'

if __name__ == '__main__':
    print('=== Comprobando obtención de secretos desde Secret Manager ===')
    vars_to_check = [
        'GEMINI_API_KEY',
        'AZURE_TENANT_ID', 
        'AZURE_CLIENT_ID',
        'AZURE_CLIENT_SECRET',
        'POWERBI_TENANT_ID',
        'POWERBI_CLIENT_ID',
        'POWERBI_CLIENT_SECRET',
        'GMAIL_ADDRESS',
        'GMAIL_APP_PASSWORD'
    ]

    for var in vars_to_check:
        val, err = get_setting(var)
        if val:
            print(f'✅ {var}: Obtenido correctamente ({len(val)} caracteres)')
        else:
            print(f'❌ {var}: Falló -> {err}')
