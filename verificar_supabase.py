import os
import pandas as pd
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
url = (os.getenv("SUPABASE_URL") or "").strip().strip('"').strip("'")
key = (
    os.getenv("SUPABASE_KEY")
    or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or os.getenv("SUPABASE_PUBLISHABLE_KEY")
    or ""
).strip().strip('"').strip("'")
supabase = create_client(url, key)

res_reg = supabase.table("registros_residuos").select("count", count="exact").execute()
print(f"Total registros en 'registros_residuos': {res_reg.count}")

res_arch = supabase.table("archivos_subidos").select("*").execute()
print("\nArchivos registrados en 'archivos_subidos':")
for a in res_arch.data:
    print(f"  • {a['nombre']} [{a['tipo_operacion']}]: {a['total_registros']} registros (hash: {a['hash_sha256'][:12]}...)")

# Muestra de registros insertados
sample = supabase.table("registros_residuos").select("fecha, sitio, tipo_residuo, kg, pz, tarimas").limit(10).execute()
print("\nMuestra de primeros 10 registros:")
df_sample = pd.DataFrame(sample.data)
print(df_sample.to_string(index=False))

# Agrupación por tipo de residuo
all_rows = supabase.table("registros_residuos").select("tipo_residuo, kg, pz, tarimas").execute()
df_all = pd.DataFrame(all_rows.data)
print("\nTotales agrupados por Tipo de Residuo en Supabase:")
print(df_all.groupby("tipo_residuo")[["kg", "pz", "tarimas"]].sum())
