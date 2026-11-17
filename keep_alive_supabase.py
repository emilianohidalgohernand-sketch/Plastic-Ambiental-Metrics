#!/usr/bin/env python3
"""
Script Keep-Alive para Supabase - Plastic Trade
==============================================
Evita que el proyecto de Supabase entre en pausa por inactividad.
Ejecuta un ciclo completo de:
1. Conexión segura usando SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY (o SUPABASE_KEY).
2. Inserción de un registro temporal con clave identificable 'KEEP_ALIVE_PING'.
3. Eliminación inmediata del registro por su ID/clave.
4. Verificación y confirmación en consola.
"""

import os
import sys
import time
import datetime
from dotenv import load_dotenv
from supabase import create_client, ClientOptions


def main():
    load_dotenv()

    # 1. Obtener y sanitizar credenciales
    supabase_url = (os.getenv("SUPABASE_URL") or "").strip().strip('"').strip("'")
    supabase_key = (
        os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SUPABASE_KEY")
        or os.getenv("SUPABASE_PUBLISHABLE_KEY")
        or ""
    ).strip().strip('"').strip("'")

    if not supabase_url or not supabase_key:
        print("[ERROR] Credenciales de Supabase no definidas en variables de entorno.")
        print("Asegúrate de configurar SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY (o SUPABASE_KEY).")
        sys.exit(1)

    print(f"[{datetime.datetime.now().isoformat()}] Conectando a Supabase...")

    try:
        supabase = create_client(
            supabase_url,
            supabase_key,
            options=ClientOptions(postgrest_client_timeout=15)
        )
    except Exception as e:
        print(f"[ERROR] Falló la creación del cliente Supabase: {e}")
        sys.exit(1)

    timestamp_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    ping_key = f"KEEP_ALIVE_PING_{timestamp_str}"
    ping_hash = f"keep_alive_{timestamp_str}"
    today = datetime.date.today().isoformat()

    archivo_record = {
        "hash_sha256": ping_hash,
        "nombre": "KEEP_ALIVE_PING.xlsx",
        "modulo": "Keep-Alive Automation",
        "tipo_operacion": "SISTEMA",
        "fecha": today,
        "total_registros": 1
    }

    ping_record = {
        "clave_duplicado": ping_key,
        "tipo_operacion": "SISTEMA",
        "fecha": today,
        "sitio": "KEEP_ALIVE_PING",
        "tipo_residuo": "Playo",
        "kg": 0.0,
        "pz": 0.0,
        "tarimas": 0.0,
        "archivo_hash": ping_hash
    }

    try:
        # 2. Inserción temporal
        print(f"[ESCRITURA] Insertando entrada en archivos_subidos: {ping_hash}...")
        supabase.table("archivos_subidos").insert(archivo_record).execute()

        print(f"[ESCRITURA] Insertando registro ping en registros_residuos: {ping_key}...")
        insert_res = supabase.table("registros_residuos").insert(ping_record).execute()
        print(f"[OK] Inserción exitosa. Registros insertados: {len(insert_res.data) if insert_res.data else 1}")

        # Pequeña pausa
        time.sleep(0.5)

        # 3. Eliminación inmediata (en orden inverso de FK)
        print(f"[LIMPIEZA] Eliminando registro ping de registros_residuos...")
        del_reg = supabase.table("registros_residuos").delete().eq("clave_duplicado", ping_key).execute()
        print(f"[OK] Registro de residuos eliminado.")

        print(f"[LIMPIEZA] Eliminando entrada de archivos_subidos...")
        del_arch = supabase.table("archivos_subidos").delete().eq("hash_sha256", ping_hash).execute()
        print(f"[OK] Entrada de archivo eliminada.")

        print(f"[{datetime.datetime.now().isoformat()}] [ÉXITO] Ciclo de keep-alive (escritura y borrado) completado exitosamente en Supabase.")
        sys.exit(0)

    except Exception as e:
        print(f"[ERROR] Error durante el ciclo de keep-alive en Supabase: {e}")
        # Intentar limpieza de emergencia en caso de fallo intermedio
        try:
            supabase.table("registros_residuos").delete().eq("clave_duplicado", ping_key).execute()
            supabase.table("archivos_subidos").delete().eq("hash_sha256", ping_hash).execute()
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
