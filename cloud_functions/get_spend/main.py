import functions_framework
import json

@functions_framework.http
def get_daily_spend(request):
    return json.dumps({"status": "success", "message": "Agente FinOps conectado correctamente"}), 200, {'Content-Type': 'application/json'}
