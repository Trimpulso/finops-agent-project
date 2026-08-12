import requests
import json
import csv
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime

# ============ CONFIGURACIÓN ORGANIZACIONAL ============
REQUIRED_TAGS_SCHEMA = {
    'businessOwner': 'Email del propietario del recurso',
    'businessUnit': 'Unidad de negocio (IT TRANSFORMATION, IT OPERATIONS, etc.)',
    'env': 'Ambiente (dev, test, prod)',
    'ac': 'Account Code (formato: ES0009/ES999999/G990/U184)'
}

# ============ AZURE FUNCTIONS ============

def azure_get_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    """Obtiene token Bearer para Azure."""
    url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    data = {
        'grant_type': 'client_credentials',
        'client_id': client_id,
        'client_secret': client_secret,
        'scope': 'https://management.azure.com/.default'
    }
    response = requests.post(url, data=data, timeout=10)
    if response.status_code != 200:
        raise Exception(f"Azure auth failed: {response.text}")
    return response.json()['access_token']

def azure_assign_tags(
    resource_id: str,
    tags: Dict[str, str],
    token: str
) -> Dict:
    """Asigna tags a un recurso Azure."""
    if not tags:
        return {
            'success': False,
            'resource_id': resource_id,
            'message': 'No tags provided'
        }
    
    url = f"https://management.azure.com{resource_id}?api-version=2023-07-01"
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json'
    }
    
    try:
        response = requests.patch(url, json={'tags': tags}, headers=headers, timeout=10)
        
        if response.status_code in [200, 201]:
            return {
                'success': True,
                'cloud': 'azure',
                'resource_id': resource_id,
                'tags_assigned': tags,
                'timestamp': datetime.now().isoformat()
            }
        else:
            return {
                'success': False,
                'cloud': 'azure',
                'resource_id': resource_id,
                'error': f"HTTP {response.status_code}: {response.text}"
            }
    except Exception as e:
        return {
            'success': False,
            'cloud': 'azure',
            'resource_id': resource_id,
            'error': str(e)
        }

def azure_assign_batch(
    resource_tags_list: List[Dict],
    token: str,
    skip_validation: bool = False
) -> List[Dict]:
    """Asigna tags a múltiples recursos Azure."""
    results = []
    for item in resource_tags_list:
        result = azure_assign_tags(item['resource_id'], item['tags'], token)
        results.append(result)
    return results

# ============ GCP FUNCTIONS ============

def gcp_get_credentials(project_id: str, service_account_file: Optional[str] = None):
    """Obtiene credenciales de GCP."""
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request
        
        if service_account_file:
            creds = service_account.Credentials.from_service_account_file(
                service_account_file,
                scopes=['https://www.googleapis.com/auth/cloud-platform']
            )
        else:
            from google.auth import default
            creds, _ = default(scopes=['https://www.googleapis.com/auth/cloud-platform'])
        
        if creds.expired:
            creds.refresh(Request())
        
        return creds
    except Exception as e:
        raise Exception(f"GCP auth failed: {e}")

def gcp_assign_labels(
    resource_name: str,
    labels: Dict[str, str],
    service_account_file: Optional[str] = None
) -> Dict:
    """Asigna labels a un recurso GCP."""
    try:
        import googleapiclient.discovery as discovery
        
        creds = gcp_get_credentials(service_account_file)
        
        if '/instances/' in resource_name:
            return _gcp_assign_compute_labels(resource_name, labels, creds)
        elif '/buckets/' in resource_name:
            return _gcp_assign_bucket_labels(resource_name, labels, creds)
        elif '/clusters/' in resource_name:
            return _gcp_assign_gke_labels(resource_name, labels, creds)
        else:
            return {
                'success': False,
                'cloud': 'gcp',
                'resource_name': resource_name,
                'error': 'Unsupported resource type'
            }
    except Exception as e:
        return {
            'success': False,
            'cloud': 'gcp',
            'resource_name': resource_name,
            'error': str(e)
        }

def _gcp_assign_compute_labels(resource_name: str, labels: Dict[str, str], creds) -> Dict:
    """Asigna labels a Compute Engine instances."""
    try:
        import googleapiclient.discovery as discovery
        
        compute = discovery.build('compute', 'v1', credentials=creds)
        
        parts = resource_name.split('/')
        project = parts[1]
        zone = parts[3]
        instance = parts[5]
        
        get_req = compute.instances().get(project=project, zone=zone, resource=instance)
        result = get_req.execute()
        
        current_labels = result.get('labels', {})
        current_labels.update(labels)
        
        body = {
            'labels': current_labels,
            'labelFingerprint': result.get('labelFingerprint', '')
        }
        
        set_req = compute.instances().setLabels(
            project=project,
            resource=instance,
            zone=zone,
            body=body
        )
        set_req.execute()
        
        return {
            'success': True,
            'cloud': 'gcp',
            'resource_name': resource_name,
            'resource_type': 'compute.instances',
            'labels_assigned': labels,
            'timestamp': datetime.now().isoformat()
        }
    except Exception as e:
        return {
            'success': False,
            'cloud': 'gcp',
            'resource_name': resource_name,
            'error': str(e)
        }

def _gcp_assign_bucket_labels(resource_name: str, labels: Dict[str, str], creds) -> Dict:
    """Asigna labels a Cloud Storage buckets."""
    try:
        from google.cloud import storage
        
        bucket_name = resource_name.split('/')[-1]
        
        client = storage.Client(credentials=creds)
        bucket = client.bucket(bucket_name)
        
        bucket.labels = labels
        bucket.patch()
        
        return {
            'success': True,
            'cloud': 'gcp',
            'resource_name': resource_name,
            'resource_type': 'storage.buckets',
            'labels_assigned': labels,
            'timestamp': datetime.now().isoformat()
        }
    except Exception as e:
        return {
            'success': False,
            'cloud': 'gcp',
            'resource_name': resource_name,
            'error': str(e)
        }

def _gcp_assign_gke_labels(resource_name: str, labels: Dict[str, str], creds) -> Dict:
    """Asigna labels a GKE clusters."""
    try:
        import googleapiclient.discovery as discovery
        
        container = discovery.build('container', 'v1', credentials=creds)
        
        parts = resource_name.split('/')
        project = parts[1]
        zone = parts[3]
        cluster = parts[5]
        
        request = container.projects().zones().clusters().get(
            projectId=project,
            zone=zone,
            clusterId=cluster
        )
        cluster_obj = request.execute()
        
        current_labels = cluster_obj.get('resourceLabels', {})
        current_labels.update(labels)
        
        update_request = container.projects().zones().clusters().update(
            projectId=project,
            zone=zone,
            clusterId=cluster,
            body={
                'update': {
                    'desiredResourceLabels': current_labels
                }
            }
        )
        update_request.execute()
        
        return {
            'success': True,
            'cloud': 'gcp',
            'resource_name': resource_name,
            'resource_type': 'container.clusters',
            'labels_assigned': labels,
            'timestamp': datetime.now().isoformat()
        }
    except Exception as e:
        return {
            'success': False,
            'cloud': 'gcp',
            'resource_name': resource_name,
            'error': str(e)
        }

def gcp_assign_batch(
    resource_labels_list: List[Dict],
    service_account_file: Optional[str] = None
) -> List[Dict]:
    """Asigna labels a múltiples recursos GCP."""
    results = []
    for item in resource_labels_list:
        result = gcp_assign_labels(
            item['resource_name'],
            item['labels'],
            service_account_file
        )
        results.append(result)
    return results

# ============ VALIDACIÓN ============

def validate_tags(tags: Dict[str, str]) -> Tuple[bool, List[str]]:
    """Valida que los tags cumplan con el esquema."""
    errors = []
    
    if not tags:
        errors.append("No tags provided")
        return False, errors
    
    if 'businessOwner' not in tags or not tags['businessOwner']:
        errors.append("Missing required tag: businessOwner")
    
    if 'env' not in tags or not tags['env']:
        errors.append("Missing required tag: env")
    
    valid_envs = ['dev', 'test', 'staging', 'prod', 'development', 'production']
    if 'env' in tags and tags['env'].lower() not in valid_envs:
        errors.append(f"Invalid env value: {tags['env']} (must be one of: {', '.join(valid_envs)})")
    
    return len(errors) == 0, errors

# ============ REPORTES ============

def generate_compliance_report(
    results: List[Dict],
    output_file: str = 'tag-assignment-report.csv'
) -> str:
    """Genera reporte CSV de asignaciones de tags."""
    if not results:
        return None
    
    keys = ['cloud', 'resource_id', 'resource_name', 'success', 'tags_assigned', 'error', 'timestamp']
    
    with open(output_file, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=keys, restval='')
        writer.writeheader()
        
        for r in results:
            row = {
                'cloud': r.get('cloud', ''),
                'resource_id': r.get('resource_id', r.get('resource_name', '')),
                'resource_name': r.get('resource_name', ''),
                'success': r.get('success', False),
                'tags_assigned': json.dumps(r.get('tags_assigned', r.get('labels_assigned', {}))),
                'error': r.get('error', ''),
                'timestamp': r.get('timestamp', '')
            }
            writer.writerow(row)
    
    return output_file

# ============ MAIN ============

def main():
    """Ejemplo de uso."""
    print("🏷️  Tag Assignment Tool - Azure & GCP")
    print("=" * 60)
    
    azure_tenant = os.getenv('AZURE_TENANT_ID')
    azure_client_id = os.getenv('AZURE_CLIENT_ID')
    azure_client_secret = os.getenv('AZURE_CLIENT_SECRET')
    azure_sub = os.getenv('AZURE_SUBSCRIPTION_ID')
    
    if all([azure_tenant, azure_client_id, azure_client_secret]):
        print("\n✓ Azure credentials found")
        
        try:
            token = azure_get_token(azure_tenant, azure_client_id, azure_client_secret)
            print("✓ Azure authenticated")
            
            example_resource = "/subscriptions/6d5311ca-8b9f-46cb-a171-58516b9bacda/resourceGroups/pga-nonprod-host/providers/Microsoft.Network/networkSecurityGroups/nsg-subnet-pga-htr-flex-dev"
            
            example_tags = {
                'businessOwner': 'admin@example.com',
                'businessUnit': 'IT OPERATIONS',
                'env': 'prod',
                'ac': 'ES0009/ES999999/G990/U184'
            }
            
            is_valid, errors = validate_tags(example_tags)
            if is_valid:
                print(f"\n✓ Tags validated")
                print(f"  Would assign: {example_tags}")
                print(f"  To resource: {example_resource}")
            else:
                print(f"✗ Validation errors: {errors}")
        
        except Exception as e:
            print(f"✗ Error: {e}")
    
    print("\n" + "=" * 60)
    print("Ready for integration with web_app/app.py")

if __name__ == '__main__':
    main()
