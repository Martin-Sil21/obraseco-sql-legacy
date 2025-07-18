from flask import Flask, request, jsonify, abort
from decimal import Decimal
import pyodbc
import os
import requests
import re
import schedule
import time
from threading import Thread
from datetime import datetime

app = Flask(__name__)

# Configuración SQL Server
sql_host = os.environ.get('SQLSERVER_HOST')
sql_db   = os.environ.get('SQLSERVER_DB')
sql_user = os.environ.get('SQLSERVER_USER')
sql_pass = os.environ.get('SQLSERVER_PASS')

# Configuración Supabase
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')

if not all([sql_host, sql_db, sql_user, sql_pass]):
    raise RuntimeError("Faltan variables de entorno necesarias para la conexión SQL Server.")

conn_str = (
    f"DRIVER={{ODBC Driver 17 for SQL Server}};"
    f"SERVER={sql_host};"
    f"DATABASE={sql_db};"
    f"UID={sql_user};"
    f"PWD={sql_pass};"
    "Encrypt=no;TrustServerCertificate=yes;"
)

def normalize_text(text):
    """Normaliza texto para mejorar búsquedas"""
    if not text:
        return ""
    text = text.lower()
    replacements = {
        'á': 'a', 'é': 'e', 'í': 'i', 'ó': 'o', 'ú': 'u',
        'ñ': 'n', 'ü': 'u'
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def extract_keywords(description):
    """Extrae palabras clave de la descripción"""
    if not description:
        return []
    
    stop_words = {'de', 'la', 'el', 'en', 'con', 'para', 'por', 'un', 'una', 'y', 'o', 'del', 'las', 'los'}
    words = re.findall(r'\b\w+\b', normalize_text(description))
    keywords = [w for w in words if len(w) > 2 and w not in stop_words]
    
    extended_keywords = set(keywords)
    for word in keywords:
        if word.endswith('s') and len(word) > 3:
            extended_keywords.add(word[:-1])
        else:
            extended_keywords.add(word + 's')
    
    return list(extended_keywords)

def sync_catalog_to_supabase():
    """Sincroniza el catálogo completo desde SQL Server a Supabase"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Supabase no configurado, saltando sincronización")
        return False
    
    try:
        print(f"Iniciando sincronizacion del catalogo - {datetime.now()}")
        
        # Obtener todos los productos de SQL Server
        with pyodbc.connect(conn_str, timeout=30) as conn:
            cursor = conn.cursor()
            
            # Query usando solo las columnas que existen
            query = """
                SELECT Codigo, Descri, PrecioFinal
                FROM dbo.ConsStock
                WHERE PrecioFinal > 0
                ORDER BY Codigo
            """
            cursor.execute(query)
            
            products = []
            for row in cursor.fetchall():
                codigo, descri, precio = row
                
                keywords = extract_keywords(descri)
                normalized_desc = normalize_text(descri)
                
                product = {
                    'codigo': codigo,
                    'descripcion': descri,
                    'descripcion_normalizada': normalized_desc,
                    'precio_final': float(precio) if precio else 0,                    
                    'keywords': keywords,
                    'updated_at': datetime.now().isoformat()
                }
                products.append(product)
        
        # Limpiar e insertar en Supabase
        headers = {
            'apikey': SUPABASE_KEY,
            'Authorization': f'Bearer {SUPABASE_KEY}',
            'Content-Type': 'application/json'
        }
        
        # Eliminar registros existentes
        delete_url = f"{SUPABASE_URL}/rest/v1/productos_catalogo"
        requests.delete(delete_url, headers=headers, params={'codigo': 'neq.'})
        
        # Insertar por lotes de 1000
        batch_size = 1000
        total_inserted = 0
        
        for i in range(0, len(products), batch_size):
            batch = products[i:i+batch_size]
            insert_url = f"{SUPABASE_URL}/rest/v1/productos_catalogo"
            response = requests.post(insert_url, headers=headers, json=batch)
            
            if response.status_code in [200, 201]:
                total_inserted += len(batch)
                print(f"Lote {i//batch_size + 1}: {len(batch)} productos insertados")
            else:
                print(f"Error en lote {i//batch_size + 1}: {response.status_code}")
                return False
        
        print(f"Sincronizacion completada - {total_inserted} productos actualizados")
        return True
        
    except Exception as e:
        print("Error en sincronizacion:", str(e).replace('ó', 'o').replace('í', 'i').replace('á', 'a'))
        return False

@app.route("/search-multi", methods=["GET"])
def search_multi():
    """Endpoint de búsqueda multi-términos"""
    token = request.args.get("token")
    if token != os.environ.get("API_TOKEN"):
        return abort(403, "Unauthorized")

    query_param = request.args.get("query", "").strip()
    if not query_param:
        return jsonify({"error": "Query parameter is required."}), 400

    terms = [t.strip() for t in query_param.split(",") if t.strip()]
    if not terms:
        return jsonify({"error": "No valid terms provided."}), 400

    try:
        with pyodbc.connect(conn_str, timeout=5) as conn:
            cursor = conn.cursor()
            like_clauses = " OR ".join(["Descri LIKE ?" for _ in terms])
            params = [f"%{t}%" for t in terms]

            # Query simplificado - solo usar columnas que sabemos que existen
            query = f"""
                SELECT TOP 200 Codigo, Descri, PrecioFinal
                FROM dbo.ConsStock
                WHERE {like_clauses} AND PrecioFinal > 0
                ORDER BY PrecioFinal ASC
            """
            cursor.execute(query, params)
            columns = [c[0] for c in cursor.description]
            results = []
            for row in cursor.fetchall():
                record = dict(zip(columns, row))
                for key, value in record.items():
                    if isinstance(value, Decimal):
                        record[key] = float(value)
                results.append(record)

            return jsonify({
                "total": len(results),
                "results": results
            })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

def run_scheduler():
    """Ejecuta el scheduler para sincronizacion automatica cada 8 horas"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Scheduler deshabilitado - Supabase no configurado")
        return
    
    schedule.every(8).hours.do(sync_catalog_to_supabase)
    
    while True:
        schedule.run_pending()
        time.sleep(60)

print("Iniciando aplicacion...")

if SUPABASE_URL and SUPABASE_KEY:
    print("Iniciando sincronizacion inicial...")
    Thread(target=sync_catalog_to_supabase).start()

    print("Iniciando scheduler de sincronizacion cada 8 horas...")
    Thread(target=run_scheduler, daemon=True).start()
else:
    print("Supabase no configurado - Solo búsqueda disponible")

# Esto sí queda dentro del bloque MAIN si querés usarlo local:
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
