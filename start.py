from app import app, SUPABASE_KEY, SUPABASE_URL, sync_catalog_to_supabase, run_scheduler
from threading import Thread

def run_sync_in_thread():
    try:
        print("Ejecutando sincronización inicial...")
        sync_catalog_to_supabase()
    except Exception as e:
        print("Error ejecutando sincronización inicial:", str(e))

def run_scheduler_in_thread():
    try:
        run_scheduler()
    except Exception as e:
        print("Error en scheduler:", str(e))

print("Iniciando aplicación...")

if SUPABASE_URL and SUPABASE_KEY:
    Thread(target=run_sync_in_thread).start()
    Thread(target=run_scheduler_in_thread, daemon=True).start()
else:
    print("Supabase no configurado - Solo búsqueda disponible.")

app.run(host="0.0.0.0", port=5000)
