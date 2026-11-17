#!/usr/bin/env python3
"""
Script de Ingesta de Residuos a Supabase
========================================
Proyecto: PLASTIC TRADE • Smart Packaging | Portal Ambiental
Módulo: Ingesta y Homologación de Residuos Valorizables

Dependencias: python-dotenv, openpyxl, pandas, supabase
Tablas destino:
  - archivos_subidos (hash_sha256, nombre, modulo, tipo_operacion, fecha, total_registros)
  - registros_residuos (clave_duplicado, tipo_operacion, fecha, sitio, tipo_residuo, kg, pz, tarimas, archivo_hash, creado_en)
"""

import os
import sys
import re
import hashlib
import unicodedata
import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

import pandas as pd
import openpyxl
from dotenv import load_dotenv
from supabase import create_client, Client, ClientOptions

# ==========================================
# CONFIGURACIÓN Y CATÁLOGO OFICIAL (6 CATEGORÍAS)
# ==========================================
CATALOGO_RESIDUOS = [
    "Playo",
    "Cono",
    "Cartón",
    "Chatarra",
    "Tarima",
    "Madera"
]

COLUMNAS_CONCENTRADO = [
    "Fecha",
    "Sitio",
    "Tipo de Residuo",
    "Kg",
    "Pz",
    "Tarimas"
]

# Factores oficiales de emisión evitada por kilogramo y conversiones físicas
FACTORES_CO2E_KG = {
    "PLAYO": 3.5,        # Playo neto: 3.5 tCO2e / Ton
    "CARTON": 0.852,     # Cartón circular oficial: 0.852 tCO2e / Ton
    "CONO": 1.198,       # Cono oficial: 1.198 tCO2e / Ton (1.198 kg CO2e / kg)
    "MADERA": 0.2,       # Madera / Tarima
    "TARIMA": 0.2,       # Madera / Tarima
    "CHATARRA": 2.66     # Chatarra
}

def calcular_tco2e(kg_playo: float = 0.0, kg_cono: float = 0.0) -> dict:
    """
    Fórmula oficial de tCO2e:
    - Playo: (Kg_playo / 1000.0) * 3.5
    - Cono: (Kg_cono * 1.198) / 1000.0
    - Total tCO2e Evitado: tco2e_playo + tco2e_cono
    """
    tco2e_playo = (float(kg_playo or 0.0) / 1000.0) * 3.5
    tco2e_cono = (float(kg_cono or 0.0) * 1.198) / 1000.0
    total_tco2e = tco2e_playo + tco2e_cono
    return {
        "tco2e_playo": round(tco2e_playo, 2),
        "tco2e_cono": round(tco2e_cono, 2),
        "total_tco2e": round(total_tco2e, 2)
    }

def calcular_tco2e_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcula las columnas de tCO2e oficial en un DataFrame de Pandas:
    df['tco2e_playo'] = (df['kg_playo'] / 1000.0) * 3.5
    df['tco2e_cono'] = (df['kg_cono'] * 1.198) / 1000.0
    df['total_tco2e'] = df['tco2e_playo'] + df['tco2e_cono']
    """
    if 'kg_playo' in df.columns:
        df['tco2e_playo'] = (df['kg_playo'].fillna(0.0) / 1000.0) * 3.5
    else:
        df['tco2e_playo'] = 0.0
    if 'kg_cono' in df.columns:
        df['tco2e_cono'] = (df['kg_cono'].fillna(0.0) * 1.198) / 1000.0
    else:
        df['tco2e_cono'] = 0.0
    df['total_tco2e'] = df['tco2e_playo'] + df['tco2e_cono']
    return df

PESO_TARIMA_KG = 25.0  # 1 Pz de Tarima = 25.0 Kg de madera
FACTOR_CONO_KG = 0.3   # 1 Pz de Cono = 0.3 Kg


# ==========================================
# NORMALIZACIÓN Y REGLAS DE CLASIFICACIÓN
# ==========================================
def normalize_text(text: Any) -> str:
    """Elimina acentos, espacios extras y convierte a mayúsculas para comparaciones consistentes."""
    if text is None or pd.isna(text):
        return ""
    s = str(text).strip()
    return unicodedata.normalize('NFKD', s).encode('ASCII', 'ignore').decode('ASCII').upper()


def classify_material(text: Any) -> Optional[str]:
    """
    Clasifica una descripción de material contra el catálogo oficial de 6 categorías:
    Playo, Cono, Cartón, Chatarra, Tarima y Madera.

    Reglas de Negocio:
      - Tarima: Descripciones con 'TARIMA', 'PALLET', 'PALLETS', 'PARIHUELA'.
      - Madera: Descripciones con 'MADERA', 'HUACAL', 'POLIN', 'BARROTE' que NO contengan 'TARIMA' ni 'PALLET'.
      - Insumos Operativos: Descarte estricto de 'REGEN', 'REGEN 50', químicos, herramientas.
    """
    if not text or pd.isna(text):
        return None

    s = normalize_text(text)
    if not s:
        return None

    # 1. Regla de exclusión estricta: insumos como REGEN / REGEN 50
    if "REGEN" in s:
        return None

    # 2. Insumos químicos o equipos no valorizables
    if any(k in s for k in ["ANTICONGELANTE", "PATIN", "NAS-46", "QUIMICO", "LUBRICANTE", "TAPAS PLASTICAS"]):
        return None

    # 3. Cono (prioridad antes de Cartón)
    if any(k in s for k in ["CONO", "CONILLO", "TUBO DE CARTON", "TUBOS DE CARTON", "TUBIN"]):
        return "Cono"

    # 4. Tarima (prioridad sobre Madera)
    if any(k in s for k in ["TARIMA", "PALLET", "PALLETS", "PARIHUELA"]):
        return "Tarima"

    # 5. Madera (leñosos sin contener TARIMA ni PALLET)
    if any(k in s for k in ["MADERA", "HUACAL", "POLIN", "BARROTE", "LEÑA", "LENA", "TABLA", "TABLON"]) and "TARIMA" not in s and "PALLET" not in s:
        return "Madera"

    # 6. Playo (Playo, Polietileno, Stretch, Emplaye, Film, Plástico)
    if any(k in s for k in ["PLAYO", "EMPLAYE", "STRETCH", "POLIETILENO", "FILM", "PELICULA", "PLASTICO", "PLASTICA"]):
        return "Playo"

    # 7. Cartón (Cartón, Corrugado, OCC, Cajas, Kraft)
    if any(k in s for k in ["CARTON", "CORRUGADO", "OCC", "CAJA", "CAJAS", "KRAFT", "CARTONCILLO", "PACA DE CARTON", "PACAS DE CARTON"]):
        return "Cartón"

    # 8. Chatarra (Chatarra, Fierro, Metal, Acero, Aluminio, Cobre, Scrap, Lámina)
    if any(k in s for k in ["CHATARRA", "FIERRO", "METAL", "ACERO", "ALUMINIO", "COBRE", "BRONCE", "LAMINA", "TAMBOR METALICO", "SCRAP"]):
        return "Chatarra"

    return None


def clean_num(val: Any) -> float:
    """Convierte de forma segura cualquier celda a número flotante no negativo."""
    if val is None or pd.isna(val):
        return 0.0
    if isinstance(val, (int, float)):
        return float(val) if not pd.isna(val) else 0.0
    s = str(val).strip().replace('$', '').replace(' ', '').replace(',', '')
    try:
        n = float(s)
        return n if n >= 0 else 0.0
    except Exception:
        return 0.0


def format_date(val: Any) -> Optional[str]:
    """Convierte fechas (datetime, timestamp, serial de Excel o texto) a formato YYYY-MM-DD."""
    if val is None or pd.isna(val):
        return None
    s = str(val).strip()
    if not s:
        return None

    s_norm = normalize_text(s)
    if s_norm in ["FECHA", "DIA", "FECHA DE ENTREGA", "FECHA ENTREGA", "DATE", "FECHA RECOLECCION", "FECHA DE SALIDA"]:
        return None

    if isinstance(val, (datetime.datetime, datetime.date, pd.Timestamp)):
        return val.strftime("%Y-%m-%d")

    if isinstance(val, (int, float)):
        try:
            dt = pd.to_datetime(val, unit='D', origin='1899-12-30')
            return dt.strftime("%Y-%m-%d")
        except Exception:
            pass

    try:
        dt = pd.to_datetime(s, format="%Y-%m-%d", errors='coerce')
        if not pd.isna(dt):
            return dt.strftime("%Y-%m-%d")
    except Exception:
        pass

    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            dt = pd.to_datetime(s, dayfirst=True, errors='coerce')
            if not pd.isna(dt):
                return dt.strftime("%Y-%m-%d")
    except Exception:
        pass

    return None


def calculate_file_hash(file_path: str) -> str:
    """Calcula el hash SHA-256 de un archivo físico."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def apply_cono_conversion(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Regla Maestra de Conversión de Conos (Pz a Kg):
    - Factor oficial: Cada pieza de cono equivale a 0.3 kg (FACTOR_CONO_KG = 0.3).
    - Para cualquier fuente donde tipo_residuo == 'Cono':
      * Si pz > 0 y kg == 0 (o nulo): kg = round(pz * FACTOR_CONO_KG, 2)
      * Si kg > 0 y pz == 0 (o nulo): pz = round(kg / FACTOR_CONO_KG, 0)
    """
    for r in records:
        if r.get("tipo_residuo") == "Cono":
            kg = float(r.get("kg") or 0.0)
            pz = float(r.get("pz") or 0.0)
            if pz > 0 and (kg == 0.0 or kg is None):
                r["kg"] = round(pz * FACTOR_CONO_KG, 2)
            elif kg > 0 and (pz == 0.0 or pz is None):
                r["pz"] = round(kg / FACTOR_CONO_KG, 0)
    return records


def apply_tarima_conversion(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Regla Maestra de Conversión de Tarimas (Pz a Kg):
    - Factor oficial: 1 Pz de Tarima = 25.0 Kg de madera (PESO_TARIMA_KG = 25.0).
    - Para cualquier fuente donde tipo_residuo == 'Tarima':
      * Si (tarimas > 0 o pz > 0) y kg == 0 (o nulo): kg = round((tarimas or pz) * PESO_TARIMA_KG, 2)
      * Si kg > 0 y tarimas == 0: tarimas = round(kg / PESO_TARIMA_KG, 1); pz = tarimas
    """
    for r in records:
        if r.get("tipo_residuo") == "Tarima":
            kg = float(r.get("kg") or 0.0)
            tarimas = float(r.get("tarimas") or 0.0)
            pz = float(r.get("pz") or 0.0)
            cant = tarimas if tarimas > 0 else pz
            if cant > 0 and (kg == 0.0 or kg is None):
                r["kg"] = round(cant * PESO_TARIMA_KG, 2)
                r["tarimas"] = cant
                r["pz"] = cant
            elif kg > 0 and cant == 0.0:
                calc_p = round(kg / PESO_TARIMA_KG, 1)
                r["tarimas"] = calc_p
                r["pz"] = calc_p
    return records


# ==========================================
# EXTRACTORES POR FUENTE
# ==========================================
def extract_from_concentrado(file_bytes_or_path, tipo_operacion: str = "ENTRADAS", target_sheet: Optional[str] = None, **kwargs) -> List[Dict[str, Any]]:
    """Extrae registros de un archivo ya consolidado con las 6 columnas oficiales."""
    records = []
    wb = openpyxl.load_workbook(file_bytes_or_path, data_only=True)
    if target_sheet and target_sheet in wb.sheetnames:
        sheet_name = target_sheet
    elif "Concentrado" in wb.sheetnames:
        sheet_name = "Concentrado"
    else:
        sheet_name = wb.sheetnames[0]
    ws = wb[sheet_name]

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return records

    # Identificar encabezado
    header_idx = -1
    for i, r in enumerate(rows[:5]):
        if r and any("Fecha" in str(c) for c in r if c):
            header_idx = i
            break

    if header_idx == -1:
        header_idx = 0

    data_rows = rows[header_idx + 1:]

    # Verificación de delegación si el archivo es Histórico Monositio
    first_row = data_rows[0] if data_rows else []
    if len(first_row) >= 4:
        val_ano = str(first_row[0]).strip()
        es_ano_puro = val_ano.isdigit() and len(val_ano) == 4 and val_ano.startswith(("202", "203"))
        tiene_mes = (parse_mes_historico(first_row[1]) is not None) or (parse_mes_historico(first_row[3]) is not None)
        if es_ano_puro and tiene_mes and any(k in normalize_text(first_row[2]) for k in ["PLAYO", "CARTON", "TARIMA", "CONO", "CHATARRA"]):
            return extract_historico_monositio(file_bytes_or_path, target_sheet=sheet_name)
    for idx, r in enumerate(data_rows, start=1):
        if not r or len(r) < 3:
            continue
        fecha = format_date(r[0])
        if not fecha:
            continue
        sitio = str(r[1]).strip() if r[1] is not None and pd.notna(r[1]) else "General"
        mat = r[2]
        res = classify_material(mat)
        if not res:
            s_mat = normalize_text(mat)
            if "TARIMA" in s_mat or "PALLET" in s_mat:
                res = "Tarima"
            elif "MADERA" in s_mat:
                res = "Madera"
            else:
                continue

        kg = clean_num(r[3]) if len(r) > 3 else 0.0
        pz = clean_num(r[4]) if len(r) > 4 else 0.0
        tarimas = clean_num(r[5]) if len(r) > 5 else 0.0

        if res == "Tarima":
            if tarimas == 0.0 and pz > 0:
                tarimas = pz
            elif pz == 0.0 and tarimas > 0:
                pz = tarimas

        if kg == 0.0 and pz == 0.0 and tarimas == 0.0:
            continue

        records.append({
            "tipo_operacion": tipo_operacion,
            "fecha": fecha,
            "sitio": sitio,
            "tipo_residuo": res,
            "kg": kg,
            "pz": pz,
            "tarimas": tarimas,
            "origen_fila": idx
        })
    records = apply_cono_conversion(records)
    records = apply_tarima_conversion(records)
    return records

def classify_residuo_historico(val: Any) -> Optional[str]:
    """Clasifica los residuos del formato histórico monositio normalizando a mayúsculas."""
    if not val or pd.isna(val):
        return None
    s = normalize_text(val).strip().upper()
    if not s or any(k in s for k in ["TOTAL", "SUBTOTAL", "SUMA", "PROMEDIO"]):
        return None
    if any(k in s for k in ["PLAYO", "STRETCH", "POLIETILENO", "FILM"]):
        return "Playo"
    elif any(k in s for k in ["CONO", "TUBIN", "CONILLO"]):
        return "Cono"
    elif any(k in s for k in ["CARTON", "OCC", "CORRUGADO"]):
        return "Cartón"
    elif any(k in s for k in ["TARIMA", "PALLET"]):
        return "Tarima"
    elif any(k in s for k in ["MADERA", "HUACAL", "POLIN"]):
        return "Madera"
    elif any(k in s for k in ["CHATARRA", "FIERRO", "METAL", "ACERO"]):
        return "Chatarra"
    elif "TOTEM" in s:
        return "Totem"
    return None


MESES_CANONICOS_DICT = {
    "ENE": ("Ene", "01"), "ENERO": ("Ene", "01"), "JAN": ("Ene", "01"),
    "FEB": ("Feb", "02"), "FEBRERO": ("Feb", "02"),
    "MAR": ("Mar", "03"), "MARZO": ("Mar", "03"),
    "ABR": ("Abr", "04"), "ABRIL": ("Abr", "04"), "APR": ("Abr", "04"),
    "MAY": ("May", "05"), "MAYO": ("May", "05"),
    "JUN": ("Jun", "06"), "JUNIO": ("Jun", "06"),
    "JUL": ("Jul", "07"), "JULIO": ("Jul", "07"),
    "AGO": ("Ago", "08"), "AGOSTO": ("Ago", "08"), "AUG": ("Ago", "08"),
    "SEP": ("Sep", "09"), "SEPTIEMBRE": ("Sep", "09"), "SET": ("Sep", "09"),
    "OCT": ("Oct", "10"), "OCTUBRE": ("Oct", "10"),
    "NOV": ("Nov", "11"), "NOVIEMBRE": ("Nov", "11"),
    "DIC": ("Dic", "12"), "DICIEMBRE": ("Dic", "12"), "DEC": ("Dic", "12")
}


def parse_mes_historico(val: Any) -> Optional[Tuple[str, str]]:
    """Parsea el mes a tupla ('Ene', '01') de forma tolerante."""
    if not val or pd.isna(val):
        return None
    s = normalize_text(val).strip().upper()
    for k, v in MESES_CANONICOS_DICT.items():
        if s == k or s.startswith(k) or k in s:
            return v
    return None


def extract_historico_monositio(file_bytes_or_path: Any, target_sheet: Optional[str] = None, **kwargs) -> List[Dict[str, Any]]:
    """
    Extracción especializada para archivos de histórico monositio (ej. MXCD05 HISTÓRICO.xlsx):
    - Columna A: Año (ej. 2026)
    - Columna B o D: Mes (Ene, Feb, Mar, Abr, May, Jun, Jul, Ago, Sep, Oct, Nov, Dic)
    - Columna C: Tipo de Residuo (Playo, Cartón, Tarima, Chatarra, Cono, totem)
    - Columna E o N: Cantidad (Kg / Pz)
    - Columna F: Sitio (ej. MXCD05)
    """
    records = []
    wb = openpyxl.load_workbook(file_bytes_or_path, data_only=True)
    if target_sheet and target_sheet in wb.sheetnames:
        sheet_name = target_sheet
    else:
        cand = [s for s in wb.sheetnames if any(k in normalize_text(s) for k in ["HIST", "MXCD", "RESUMEN"])]
        sheet_name = cand[0] if cand else wb.sheetnames[0]

    ws = wb[sheet_name]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return records

    # 1. Identificar columnas dinámicas
    col_ano = None
    col_mes = None
    col_res = None
    col_cant = None
    col_sitio = None
    header_idx = -1

    for r_idx in range(min(10, len(rows))):
        row = rows[r_idx]
        if not row:
            continue
        row_norm = [normalize_text(c) if c is not None else "" for c in row]
        if any(k in c for c in row_norm for k in ["RESIDUO", "MATERIAL", "TIPO"]) and any(k in c for c in row_norm for k in ["AÑO", "ANO", "MES", "FECHA"]):
            header_idx = r_idx
            for c_idx, c in enumerate(row_norm):
                if any(k in c for k in ["AÑO", "ANO", "YEAR"]) and col_ano is None:
                    col_ano = c_idx
                elif any(k in c for k in ["MES", "MONTH"]) and col_mes is None:
                    col_mes = c_idx
                elif any(k in c for k in ["RESIDUO", "MATERIAL", "TIPO"]) and col_res is None:
                    col_res = c_idx
                elif any(k in c for k in ["CANTIDAD", "KG", "KILOS", "PESO", "TOTAL"]) and col_cant is None:
                    col_cant = c_idx
                elif any(k in c for k in ["SITIO", "PLANTA", "CLIENTE", "ORIGEN", "UBICACION", "SUCURSAL"]) and col_sitio is None:
                    col_sitio = c_idx
            break

    # Fallback predeterminado según especificación del formato MXCD05
    if col_ano is None:
        col_ano = 0  # Col A
    if col_res is None:
        col_res = 2  # Col C
    if col_sitio is None:
        col_sitio = 5  # Col F

    start_idx = (header_idx + 1) if header_idx >= 0 else 0

    file_label = "MXCD05"
    if isinstance(file_bytes_or_path, (str, Path)):
        base_f = os.path.basename(file_bytes_or_path).upper()
        if "MXCD" in base_f:
            m = re.search(r'(MXCD\w*)', base_f)
            if m:
                file_label = m.group(1)

    for idx in range(start_idx, len(rows)):
        r = rows[idx]
        if not r or all(c is None for c in r):
            continue

        # 1. Año
        val_ano = r[col_ano] if len(r) > col_ano and r[col_ano] is not None else None
        ano_str = str(val_ano).strip() if val_ano is not None else ""
        if any(k in normalize_text(ano_str) for k in ["TOTAL", "SUBTOTAL", "SUMA", "PROMEDIO"]):
            continue
        m_ano = re.search(r'(20\d\d)', ano_str)
        ano_int = int(m_ano.group(1)) if m_ano else 2026

        # 2. Mes: Buscar en col_mes, Col B (1) o Col D (3)
        mes_info = None
        cands_mes = [col_mes, 1, 3] if col_mes is not None else [1, 3]
        for c_m in cands_mes:
            if c_m is not None and len(r) > c_m and r[c_m] is not None:
                p = parse_mes_historico(r[c_m])
                if p:
                    mes_info = p
                    break
        if not mes_info:
            continue
        mes_abrev, mes_num_str = mes_info

        # 3. Tipo de Residuo
        val_res = r[col_res] if len(r) > col_res and r[col_res] is not None else None
        cat_res = classify_residuo_historico(val_res)
        if not cat_res:
            for alt_c in [1, 2, 3]:
                if len(r) > alt_c and r[alt_c] is not None:
                    cat_res = classify_residuo_historico(r[alt_c])
                    if cat_res:
                        break
        if not cat_res:
            continue

        # 4. Cantidad (Kg / Pz): Probar en col_cant, Col E (4) o Col N (13)
        cantidad = 0.0
        cands_cant = [col_cant, 4, 13] if col_cant is not None else [4, 13]
        for c_c in cands_cant:
            if c_c is not None and len(r) > c_c and r[c_c] is not None:
                num = clean_num(r[c_c])
                if num > 0:
                    cantidad = num
                    break

        if cantidad <= 0.0:
            for c_val in r[3:]:
                num = clean_num(c_val)
                if num > 0:
                    cantidad = num
                    break

        if cantidad <= 0.0:
            continue

        # 5. Sitio
        sitio_val = str(r[col_sitio]).strip() if len(r) > col_sitio and r[col_sitio] is not None and pd.notna(r[col_sitio]) else file_label
        if not sitio_val or sitio_val.isdigit() or len(sitio_val) < 2:
            sitio_val = file_label

        # 6. Reglas de Negocio para Kg, Pz, Tarimas
        kg = 0.0
        pz = 0.0
        tarimas = 0.0

        if cat_res == "Playo":
            kg = cantidad
        elif cat_res == "Cartón":
            kg = cantidad
        elif cat_res == "Chatarra":
            kg = cantidad
        elif cat_res == "Madera":
            kg = cantidad
        elif cat_res == "Cono":
            # Si cantidad < 500 y es entera, asumir piezas y convertir a 0.3 kg
            if cantidad < 500 and cantidad.is_integer():
                pz = cantidad
                kg = round(pz * FACTOR_CONO_KG, 2)
            else:
                kg = cantidad
                pz = round(kg / FACTOR_CONO_KG, 0)
        elif cat_res == "Tarima":
            # Regla oficial: Si son piezas, Pz * 25.0 Kg para Madera/Tarima
            if cantidad < 1000:
                pz = cantidad
                tarimas = cantidad
                kg = round(cantidad * PESO_TARIMA_KG, 2)
            else:
                kg = cantidad
                tarimas = round(kg / PESO_TARIMA_KG, 1)
                pz = tarimas
        else:
            kg = cantidad

        fecha_str = f"{ano_int}-{mes_num_str}-01"

        records.append({
            "tipo_operacion": "ENTRADAS",
            "fecha": fecha_str,
            "ano": ano_int,
            "mes": mes_abrev,
            "sitio": sitio_val,
            "tipo_residuo": cat_res,
            "kg": kg,
            "pz": pz,
            "tarimas": tarimas,
            "origen_fila": idx + 1
        })

    return records



def extract_almacen(file_path: str, tipo_operacion: str = "ENTRADAS", target_sheet: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Extracción de Almacén:
    - Para ENTRADAS: procesa hoja 'ENTRADAS MP' (o detectada estructuralmente).
    - Para SALIDAS: procesa hoja 'SALIDAS PT' o 'SALIDAS'.
    Detecta dinámicamente las columnas desde la fila de encabezados:
      Fecha, Sitio/Cliente/Proveedor, Material/Especificación, Pz/Rollos, Tarimas, Kg/Peso Neto.
    """
    records = []
    xls = pd.ExcelFile(file_path)

    if not target_sheet or target_sheet not in xls.sheet_names:
        target_sheet = None
        if tipo_operacion == "ENTRADAS":
            for s in xls.sheet_names:
                s_clean = s.strip().upper()
                if s_clean == "ENTRADAS MP" or ("ENTRADAS" in s_clean and "MP" in s_clean and "SALIDAS" not in s_clean):
                    target_sheet = s
                    break
            if not target_sheet:
                for s in xls.sheet_names:
                    if "ENTRADAS" in s.strip().upper() and "SALIDAS" not in s.strip().upper():
                        target_sheet = s
                        break
        elif tipo_operacion == "SALIDAS":
            for s in xls.sheet_names:
                s_clean = s.strip().upper()
                if s_clean == "SALIDAS PT":
                    target_sheet = s
                    break
            if not target_sheet:
                for s in xls.sheet_names:
                    s_clean = s.strip().upper()
                    if s_clean == "SALIDAS" or ("SALIDAS" in s_clean and "MP" not in s_clean):
                        target_sheet = s
                        break

    if not target_sheet and xls.sheet_names:
        target_sheet = xls.sheet_names[0]

    if not target_sheet:
        return records

    file_name = os.path.basename(file_path) if isinstance(file_path, (str, Path)) else getattr(file_path, 'name', 'memoria.xlsx')
    print(f"[INGESTA] Archivo recibido: {file_name}")
    print(f"[INGESTA] Hoja seleccionada: {target_sheet}")

    df = pd.read_excel(xls, sheet_name=target_sheet, header=None)

    # Detección dinámica de encabezados y columnas
    col_fecha = None
    col_sitio = None
    col_mat = None
    col_pz = None
    col_tarimas = None
    col_kg = None
    header_idx = -1

    for r_idx in range(min(10, len(df))):
        row = [normalize_text(c) if pd.notna(c) else "" for c in df.iloc[r_idx]]
        if any("FECHA" in c for c in row) and (any("PROVEEDOR" in c for c in row) or any("CLIENTE" in c for c in row)):
            header_idx = r_idx

            # 1. Fecha
            for c_idx, c in enumerate(row):
                if "FECHA" in c and col_fecha is None:
                    col_fecha = c_idx
                    break

            # 2. Sitio: preferir NOMBRE DEL PROVEEDOR o NOMBRE DEL CLIENTE sobre CÓDIGO
            for c_idx, c in enumerate(row):
                if ("NOMBRE" in c and ("PROVEEDOR" in c or "CLIENTE" in c)) or (("CLIENTE" in c or "PROVEEDOR" in c) and "CODIGO" not in c and "ID" not in c):
                    col_sitio = c_idx
                    break
            if col_sitio is None:
                for c_idx, c in enumerate(row):
                    if "PROVEEDOR" in c or "CLIENTE" in c:
                        col_sitio = c_idx
                        break

            # 3. Material: preferir ESPECIFICACION o DESCRIPCION
            for c_idx, c in enumerate(row):
                if "ESPECIFICACION" in c or "DESCRIPCION" in c or "FORMULA/MATERIAL" in c:
                    col_mat = c_idx
                    break
            if col_mat is None:
                for c_idx, c in enumerate(row):
                    if "MATERIAL" in c or "FORMULA" in c:
                        col_mat = c_idx
                        break

            # 4. Pz / Rollos
            for c_idx, c in enumerate(row):
                if ("NO. DE ROLLOS" in c or "ROLLO" in c or "PIEZA" in c or "PZ" in c) and "TARIMA" not in c:
                    col_pz = c_idx
                    break

            # 5. Tarimas
            for c_idx, c in enumerate(row):
                if ("TARIMA" in c or "PALLET" in c) and "BRUTO" not in c:
                    col_tarimas = c_idx
                    break

            # 6. Kg
            for c_idx, c in enumerate(row):
                if ("PESO NETO" in c or "KILOGRAMO" in c or "KILO" in c or "KG" in c) and "BRUTO" not in c and "CARTON" not in c:
                    col_kg = c_idx
                    break
            break

    # Si no se detectó por encabezado, valores predeterminados de respaldo
    if header_idx == -1 or col_fecha is None or col_mat is None:
        if tipo_operacion == "ENTRADAS":
            header_idx = 1
            col_fecha, col_sitio, col_mat, col_pz, col_tarimas, col_kg = 1, 5, 10, 12, 13, 14
        else:
            if "PT" in target_sheet.upper():
                header_idx = 1
                col_fecha, col_sitio, col_mat, col_tarimas, col_pz, col_kg = 1, 8, 14, 19, 20, 21
            else:
                header_idx = 0
                col_fecha, col_sitio, col_mat, col_pz, col_tarimas, col_kg = 0, 7, 10, 12, 14, 17

    for idx in range(header_idx + 1, len(df)):
        row = df.iloc[idx]
        if col_mat is None or len(row) <= col_mat:
            continue

        raw_mat = row[col_mat]
        res = classify_material(raw_mat)
        if not res:
            continue

        raw_fecha = row[col_fecha] if col_fecha is not None and len(row) > col_fecha else None
        fecha = format_date(raw_fecha)
        if not fecha:
            continue

        sitio = str(row[col_sitio]).strip() if col_sitio is not None and len(row) > col_sitio and pd.notna(row[col_sitio]) else ("Almacén" if tipo_operacion == "ENTRADAS" else "Cliente")
        pz = clean_num(row[col_pz]) if col_pz is not None and len(row) > col_pz else 0.0
        tarimas = clean_num(row[col_tarimas]) if col_tarimas is not None and len(row) > col_tarimas else 0.0
        kg = clean_num(row[col_kg]) if col_kg is not None and len(row) > col_kg else 0.0

        if res == "Tarima":
            if tarimas == 0.0 and pz > 0:
                tarimas = pz
            elif pz == 0.0 and tarimas > 0:
                pz = tarimas

        if kg == 0.0 and pz == 0.0 and tarimas == 0.0:
            continue

        records.append({
            "tipo_operacion": tipo_operacion,
            "fecha": fecha,
            "sitio": sitio,
            "tipo_residuo": res,
            "kg": kg,
            "pz": pz,
            "tarimas": tarimas,
            "origen_fila": int(idx) + 1
        })

    return apply_cono_conversion(records)


def extract_uziel(file_path: str, tipo_operacion: str = "ENTRADAS", target_sheet: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Extracción de Uziel:
    - Hoja: contiene 'entradas' o 'salidas' (o seleccionada por inspección)
    - Filtro Col D: Tipo de movimiento debe contener 'entrada' (para ENTRADAS) o 'salida' (para SALIDAS).
    - Fecha: Col C (2), Material: Col E (4), Origen/Sitio: Col G (6), Unidad: Col I (8), Cantidad: Col J (9).
    - Regla especial: Si la unidad de medida contiene 'TARIMA' o 'PALLET', se categoriza como 'Tarima'.
    """
    records = []
    xls = pd.ExcelFile(file_path)
    if not target_sheet or target_sheet not in xls.sheet_names:
        target_sheet = None
        for s in xls.sheet_names:
            s_lower = s.lower()
            if "entrada" in s_lower or "salida" in s_lower:
                target_sheet = s
                break

    if not target_sheet and xls.sheet_names:
        target_sheet = xls.sheet_names[0]

    if not target_sheet:
        return records

    file_name = os.path.basename(file_path) if isinstance(file_path, (str, Path)) else getattr(file_path, 'name', 'memoria.xlsx')
    print(f"[INGESTA] Archivo recibido: {file_name}")
    print(f"[INGESTA] Hoja seleccionada: {target_sheet}")

    filtro_mov = "ENTRADA" if tipo_operacion == "ENTRADAS" else "SALIDA"
    df = pd.read_excel(xls, sheet_name=target_sheet, header=None)

    for idx, row in df.iterrows():
        if len(row) <= 4:
            continue

        mov = normalize_text(row[3]) if len(row) > 3 else ""
        if filtro_mov not in mov:
            continue

        raw_mat = row[4] if len(row) > 4 else None
        res = classify_material(raw_mat)
        unidad = normalize_text(row[8]) if len(row) > 8 else ""

        # Regla: si en Uziel la unidad de medida es TARIMA/PALLET, clasifica como 'Tarima'
        if any(k in unidad for k in ["TARIMA", "TAR", "PALLET"]):
            res = "Tarima"

        if not res:
            continue

        raw_fecha = row[2] if len(row) > 2 else None
        fecha = format_date(raw_fecha)
        if not fecha:
            continue

        sitio = str(row[6]).strip() if len(row) > 6 and pd.notna(row[6]) else "Uziel"
        cantidad = clean_num(row[9]) if len(row) > 9 else 0.0

        if cantidad == 0.0:
            continue

        kg = 0.0
        pz = 0.0
        tarimas = 0.0

        if any(k in unidad for k in ["KG", "KILO", "KGS"]):
            kg = cantidad
        elif any(k in unidad for k in ["PZ", "PZA", "PIEZA"]):
            pz = cantidad
            if res == "Tarima":
                tarimas = cantidad
        elif any(k in unidad for k in ["TARIMA", "TAR", "PALLET"]):
            res = "Tarima"
            tarimas = cantidad
            pz = cantidad
        else:
            if res == "Tarima":
                tarimas = cantidad
                pz = cantidad
            else:
                pz = cantidad

        records.append({
            "tipo_operacion": tipo_operacion,
            "fecha": fecha,
            "sitio": sitio,
            "tipo_residuo": res,
            "kg": kg,
            "pz": pz,
            "tarimas": tarimas,
            "origen_fila": int(idx) + 1
        })

    return apply_cono_conversion(records)


def extract_sostenibilidad(file_path: str, tipo_operacion: str = "ENTRADAS", target_sheet: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Extracción de Sostenibilidad / Pernod:
    - Hoja: 'Pernod' (o detectada estructuralmente)
    - Sitio fijo: 'Pernod Ricard'
    - Fecha: Col B (1)
    - Conos de cartón: Col C (2) -> Si > 0: Residuo="Cono", Pz=val, Kg=val*0.3
    - Polietileno: Col D (3) -> Si > 0: Residuo="Playo", Kg=val
    """
    records = []
    xls = pd.ExcelFile(file_path)
    if not target_sheet or target_sheet not in xls.sheet_names:
        target_sheet = None
        for s in xls.sheet_names:
            if "pernod" in s.lower() or "sostenibilidad" in s.lower():
                target_sheet = s
                break

    if not target_sheet and xls.sheet_names:
        target_sheet = xls.sheet_names[0]

    if not target_sheet:
        return records

    file_name = os.path.basename(file_path) if isinstance(file_path, (str, Path)) else getattr(file_path, 'name', 'memoria.xlsx')
    print(f"[INGESTA] Archivo recibido: {file_name}")
    print(f"[INGESTA] Hoja seleccionada: {target_sheet}")

    df = pd.read_excel(xls, sheet_name=target_sheet, header=None)
    for idx, row in df.iterrows():
        if len(row) <= 2:
            continue

        raw_fecha = row[1] if len(row) > 1 else None
        fecha = format_date(raw_fecha)
        if not fecha:
            continue

        val_conos = clean_num(row[2]) if len(row) > 2 else 0.0
        val_playo = clean_num(row[3]) if len(row) > 3 else 0.0

        if val_conos > 0:
            records.append({
                "tipo_operacion": tipo_operacion,
                "fecha": fecha,
                "sitio": "Pernod Ricard",
                "tipo_residuo": "Cono",
                "kg": round(val_conos * 0.3, 2),
                "pz": val_conos,
                "tarimas": 0.0,
                "origen_fila": int(idx) + 1
            })

        if val_playo > 0:
            records.append({
                "tipo_operacion": tipo_operacion,
                "fecha": fecha,
                "sitio": "Pernod Ricard",
                "tipo_residuo": "Playo",
                "kg": val_playo,
                "pz": 0.0,
                "tarimas": 0.0,
                "origen_fila": int(idx) + 1
            })

    return apply_cono_conversion(records)


def extract_unit_weight(spec_str: Any) -> float:
    """
    Extrae el peso unitario en kg de la descripción del producto:
    - Extrae con Regex el último número decimal (ej. 1.2, 1.5, .4) o el último número < 20 presente.
    - Si no se detecta, retorna 0.0.
    """
    if spec_str is None or pd.isna(spec_str):
        return 0.0
    s = str(spec_str).strip()
    if not s:
        return 0.0

    # 1. Prioridad: Buscar número que precede a KG/KILO/KGS
    m_kg = re.search(r'((?:\d*\.\d+|\d+))\s*(?:kg|kilo|kgs)\b', s, re.IGNORECASE)
    if m_kg:
        try:
            val = float(m_kg.group(1))
            if 0.0 < val < 20.0:
                return val
        except Exception:
            pass

    # 2. Buscar todos los números (decimales con punto o enteros)
    matches = re.findall(r'(?:\d*\.\d+|\d+)', s)
    if not matches:
        return 0.0

    # Prioridad: el último número decimal (< 20)
    for m in reversed(matches):
        if '.' in m:
            try:
                val = float(m)
                if 0.0 < val < 20.0:
                    return val
            except Exception:
                pass

    # Fallback: el último número < 20
    for m in reversed(matches):
        try:
            val = float(m)
            if 0.0 < val < 20.0:
                return val
        except Exception:
            pass

    return 0.0


def identificar_tipo_documento(file_path: str, tipo_operacion: Optional[str] = None) -> Tuple[str, str, Dict[str, Any]]:
    """
    Algoritmo de detección inteligente de hoja y fuente:
    1. Lee todas las hojas disponibles en el libro (openpyxl).
    2. Recorre las primeras 15 filas de cada hoja para inspeccionar encabezados y valores clave:
       - Facturación / Salidas si:
         * Encuentra en alguna columna valores como 'Entregado', 'Cancelado' o 'Pendiente'.
         * O contiene encabezados como 'Programa', 'Especificación', 'Entregas' o la estructura de columnas D, G, L, O.
       - Almacén (Entradas) si:
         * Contiene columnas o celdas con 'ENTRADAS MP', 'Proveedor', 'Material' o 'KILOS'.
       - Sostenibilidad (Pernod) si:
         * Encuentra texto como 'PERNOD', 'CONOS' o desglose mensual de reciclaje.
       - Uziel si:
         * Contiene la columna 'Tipo de movimiento' con valores 'ENTRADA' / 'SALIDA'.
    3. Si ninguna hoja coincide por nombre estricto, procesa automáticamente la hoja que contenga
       la mayor coincidencia estructural o, en su defecto, la primera hoja (sheet_name=0).
    """
    wb = openpyxl.load_workbook(file_path, data_only=True, read_only=True)
    sheet_scores = []

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        s_norm = normalize_text(sheet_name)

        rows_sample = []
        for r_idx, row in enumerate(ws.iter_rows(values_only=True)):
            if r_idx >= 15:
                break
            rows_sample.append([str(c) for c in row if c is not None])

        all_text_norm = normalize_text(" ".join(" ".join(r) for r in rows_sample))

        score_facturacion = 0
        score_almacen = 0
        score_sostenibilidad = 0
        score_uziel = 0
        score_concentrado = 0

        # 1. Facturación / Salidas
        if any(term in all_text_norm for term in ["ENTREGAD", "CANCELAD", "PENDIENT"]):
            score_facturacion += 10
        if any(term in all_text_norm for term in ["PROGRAMA", "ENTREGAS", "ESPECIFICAC"]):
            score_facturacion += 6
        if "PROGRAMA" in s_norm or "ENTREGA" in s_norm or "FACTURA" in s_norm:
            score_facturacion += 5

        # 2. Almacén (Entradas)
        if any(term in all_text_norm for term in ["ENTRADAS MP", "DATOS DE ENTRADA", "NOMBRE DEL PROVEEDOR", "BARCINAS"]):
            score_almacen += 12
        if any(term in all_text_norm for term in ["PROVEEDOR", "MATERIAL", "KILOGRAMOS", "KILOS", "PESO NETO"]):
            score_almacen += 6
        if "ENTRADAS MP" in s_norm:
            score_almacen += 10
        elif "INVENTARIO" in s_norm or ("ENTRADAS" in s_norm and "SALIDAS" not in s_norm):
            score_almacen += 4

        # 3. Sostenibilidad / Pernod
        if any(term in all_text_norm for term in ["CONOS DE CARTON", "POLIETILENO KG", "LOGISTICA DE RECOLECCIONES", "PERNOD"]):
            score_sostenibilidad += 14
        if "PERNOD" in s_norm or "SOSTENIBILIDAD" in s_norm:
            score_sostenibilidad += 8

        # 4. Uziel
        if any(term in all_text_norm for term in ["TIPO DE MOVIMIENTO", "CONTROL DE ENTRADAS Y SALIDAS", "EMPRESA DE RECICLAJE"]):
            score_uziel += 14
        if "ENTRADAS Y SALIDAS" in s_norm or "UZIEL" in s_norm:
            score_uziel += 8

        # 5. Concentrado
        if any(term in all_text_norm for term in ["TIPO DE RESIDUO", "TARIMAS"]):
            score_concentrado += 8
        if "CONCENTRADO" in s_norm:
            score_concentrado += 6

        # 6. Histórico / Monositio (ej. MXCD05)
        score_historico = 0
        if "MXCD" in all_text_norm or "MXCD" in s_norm:
            score_historico += 20
        if any(k in all_text_norm for k in ["HISTORICO", "HISTORICA", "HIST"]) or "HIST" in s_norm:
            score_historico += 15
        if ("ANO" in all_text_norm or "AÑO" in all_text_norm) and "MES" in all_text_norm:
            score_historico += 12
        if any(k in all_text_norm for k in ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"]):
            score_historico += 8

        sheet_scores.append({
            "sheet": sheet_name,
            "Facturación": score_facturacion,
            "Almacén": score_almacen,
            "Sostenibilidad": score_sostenibilidad,
            "Uziel": score_uziel,
            "Concentrado": score_concentrado,
            "Histórico": score_historico,
            "max_score": max(score_facturacion, score_almacen, score_sostenibilidad, score_uziel, score_concentrado, score_historico)
        })

    wb.close()

    best_sheet = None
    best_fuente = "Desconocido"
    max_val = -1

    for s_info in sheet_scores:
        for f in ["Facturación", "Almacén", "Sostenibilidad", "Uziel", "Concentrado", "Histórico"]:
            score = s_info[f]
            if tipo_operacion == "SALIDAS" and f == "Facturación":
                score += 5
            elif tipo_operacion == "ENTRADAS" and f == "Almacén" and "ENTRADAS MP" in normalize_text(s_info["sheet"]):
                score += 5

            if score > max_val:
                max_val = score
                best_sheet = s_info["sheet"]
                best_fuente = f

    if not best_sheet or max_val <= 0:
        best_sheet = sheet_scores[0]["sheet"] if sheet_scores else None
        best_fuente = "Facturación" if tipo_operacion == "SALIDAS" else "Almacén"

    return best_fuente, best_sheet, {"scores": sheet_scores, "max_score": max_val}


def extract_facturacion_salidas(file_path: str, target_sheet: Optional[str] = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Extracción robusta de Facturación para SALIDAS desacoplada de nombres de archivo y hojas:
    1. Detección dinámica de filas de datos y encabezados (sin salto rígido de filas 1 a 9).
    2. Normalización de Status (Columna D o detectada): acepta si contiene 'ENTREGAD'.
    3. Mapeo resiliente de columnas por nombre o por posición:
       - Col D (Índice 3 / 1-based col 4): Status.
       - Col G (Índice 6 / 1-based col 7): Sitio / Cliente.
       - Col L (Índice 11 / 1-based col 12): Especificación.
       - Col O (Índice 14 / 1-based col 15): Piezas.
       - Col E (Índice 4 / 1-based col 5): Fecha.
    4. Tratamiento seguro de Piezas (pd.to_numeric con errors='coerce' y validación pz > 0).
    5. Diagnóstico en tiempo de ejecución con conteos de filas leídas, status y válidas.
    """
    db_records = []
    salidas_detalle = []
    file_name = os.path.basename(file_path) if isinstance(file_path, (str, Path)) else getattr(file_path, 'name', 'memoria.xlsx')

    wb = openpyxl.load_workbook(file_path, data_only=True)
    if not target_sheet or target_sheet not in wb.sheetnames:
        _, best_s, _ = identificar_tipo_documento(file_path, "SALIDAS")
        target_sheet = best_s if best_s and best_s in wb.sheetnames else wb.sheetnames[0]

    ws = wb[target_sheet]

    # Detección dinámica de encabezados y posiciones de columnas
    col_status = 4  # Col D
    col_fecha = 5   # Col E
    col_sitio = 7   # Col G
    col_spec = 12   # Col L
    col_pz = 15     # Col O
    start_row = 1

    for r in range(1, min(20, ws.max_row + 1)):
        row_vals = [normalize_text(ws.cell(r, c).value) for c in range(1, min(25, ws.max_column + 1))]
        if any("STATUS" in v for v in row_vals) or (any("ENTREGA" in v for v in row_vals) and any("PZ" in v or "PIEZA" in v for v in row_vals)):
            start_row = r + 1
            for c_idx, v in enumerate(row_vals, 1):
                if "STATUS" in v:
                    col_status = c_idx
                elif "ESPECIFICAC" in v or "DESCRIPCION" in v:
                    col_spec = c_idx
                elif ("PIEZA" in v or "PZ" in v) and "TARIMA" not in v:
                    col_pz = c_idx
                elif ("CLIENTE" in v or "SITIO" in v) and "COD" not in v and "ID" not in v:
                    col_sitio = c_idx
                elif "FECHA" in v and "CIERRE" not in v and col_fecha == 5:
                    col_fecha = c_idx
            break

    total_filas = 0
    conteo_status = 0
    conteo_validas = 0
    primeras_3_filas = []

    for r in range(start_row, ws.max_row + 1):
        total_filas += 1
        if len(primeras_3_filas) < 3:
            primeras_3_filas.append([ws.cell(r, c).value for c in range(1, min(16, ws.max_column + 1))])

        val_status = ws.cell(r, col_status).value
        status_val = str(val_status or "").strip().upper()
        if "ENTREGAD" not in status_val:
            continue

        conteo_status += 1

        pz_raw = ws.cell(r, col_pz).value
        piezas = pd.to_numeric(pz_raw, errors='coerce')
        if pd.isna(piezas) or piezas <= 0:
            continue

        conteo_validas += 1

        raw_fecha = ws.cell(r, col_fecha).value
        fecha = format_date(raw_fecha)
        if not fecha:
            for alt_c in [5, 2, 3, 4, 6, 7]:
                if alt_c != col_fecha:
                    fecha = format_date(ws.cell(r, alt_c).value)
                    if fecha:
                        break
        if not fecha:
            continue

        raw_sitio = ws.cell(r, col_sitio).value
        sitio = str(raw_sitio or "").strip()
        if not sitio or sitio in ["#REF!", "#N/A", "#VALUE!", "None"]:
            code_alt = str(ws.cell(r, 6).value or "").strip()
            if code_alt and code_alt not in ["#REF!", "#N/A", "#VALUE!", "None"]:
                sitio = code_alt
            else:
                sitio = "Cliente"

        raw_spec = ws.cell(r, col_spec).value
        spec = str(raw_spec or "").strip()
        unit_wt = extract_unit_weight(spec)

        peso_producto = round(float(piezas) * unit_wt, 2)
        peso_cono = round(float(piezas) * 0.3, 2)
        peso_playo = round(max(0.0, peso_producto - peso_cono), 2)

        # Fila detalle para el concentrado de salidas y vista previa (9 columnas)
        salidas_detalle.append({
            "Fecha Entrega": fecha,
            "Status": "Entregado",
            "Sitio": sitio,
            "Especificación": spec,
            "Piezas": float(piezas),
            "Peso Unitario (Kg)": unit_wt,
            "Peso Producto (Kg)": peso_producto,
            "Peso Cono (Kg)": peso_cono,
            "Peso Playo (Kg)": peso_playo
        })

        # Registros homologados para Supabase (Playo y Cono)
        db_records.append({
            "tipo_operacion": "SALIDAS",
            "fecha": fecha,
            "sitio": sitio,
            "tipo_residuo": "Playo",
            "kg": peso_playo,
            "pz": float(piezas),
            "tarimas": 0.0,
            "origen_fila": r
        })

        db_records.append({
            "tipo_operacion": "SALIDAS",
            "fecha": fecha,
            "sitio": sitio,
            "tipo_residuo": "Cono",
            "kg": peso_cono,
            "pz": float(piezas),
            "tarimas": 0.0,
            "origen_fila": r
        })

    wb.close()

    # Diagnóstico en tiempo de ejecución
    print(f"[INGESTA] Archivo recibido: {file_name}")
    print(f"[INGESTA] Hoja seleccionada: {target_sheet}")
    print(f"[INGESTA] Filas leídas: {total_filas}, Filas con status 'Entregado': {conteo_status}, Filas válidas con Pz > 0: {conteo_validas}")
    if conteo_validas == 0:
        print(f"[INGESTA] AVISO: conteo_validas == 0. Mostrando las primeras 3 filas inspeccionadas para diagnóstico:")
        for idx_diag, fila_d in enumerate(primeras_3_filas, 1):
            print(f"  Fila {idx_diag}: {fila_d}")

    return db_records, salidas_detalle


def extract_facturacion(file_path: str, tipo_operacion: str = "SALIDAS", target_sheet: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Extracción de Facturación:
    - Si es SALIDAS: utiliza la extracción robusta extract_facturacion_salidas.
    - Si es ENTRADAS: extracción genérica de entregas.
    """
    if tipo_operacion == "SALIDAS":
        records, _ = extract_facturacion_salidas(file_path, target_sheet=target_sheet)
        return records

    records = []
    xls = pd.ExcelFile(file_path)
    if not target_sheet or target_sheet not in xls.sheet_names:
        target_sheet = None
        for s in xls.sheet_names:
            s_lower = s.lower()
            if "entreg" in s_lower or "program" in s_lower:
                target_sheet = s
                break
    if not target_sheet and xls.sheet_names:
        target_sheet = xls.sheet_names[0]

    if not target_sheet:
        return records

    file_name = os.path.basename(file_path) if isinstance(file_path, (str, Path)) else getattr(file_path, 'name', 'memoria.xlsx')
    print(f"[INGESTA] Archivo recibido: {file_name}")
    print(f"[INGESTA] Hoja seleccionada: {target_sheet}")

    df = pd.read_excel(xls, sheet_name=target_sheet, header=None)
    for idx, row in df.iterrows():
        if len(row) <= 11:
            continue

        raw_mat = row[11] if len(row) > 11 else None
        res = classify_material(raw_mat)
        if not res:
            continue

        raw_fecha = row[4] if len(row) > 4 else None
        fecha = format_date(raw_fecha)
        if not fecha:
            continue

        sitio = str(row[6]).strip() if len(row) > 6 and pd.notna(row[6]) else "Facturación"
        pz = clean_num(row[14]) if len(row) > 14 else 0.0
        if pz == 0.0:
            continue

        tarimas = pz if res == "Tarima" else 0.0

        records.append({
            "tipo_operacion": tipo_operacion,
            "fecha": fecha,
            "sitio": sitio,
            "tipo_residuo": res,
            "kg": 0.0,
            "pz": pz,
            "tarimas": tarimas,
            "origen_fila": int(idx) + 1
        })

    return apply_cono_conversion(records)


def detect_and_extract(file_path: str, tipo_operacion: str = "ENTRADAS") -> Tuple[str, List[Dict[str, Any]]]:
    """Detecta inteligentemente la fuente y hoja según el contenido estructural y ejecuta la extracción."""
    fuente_detectada, hoja_seleccionada, _ = identificar_tipo_documento(file_path, tipo_operacion)

    if fuente_detectada == "Facturación" or tipo_operacion == "SALIDAS":
        records, _ = extract_facturacion_salidas(file_path, target_sheet=hoja_seleccionada)
        return "Facturación", records
    elif fuente_detectada == "Almacén":
        records = extract_almacen(file_path, tipo_operacion, target_sheet=hoja_seleccionada)
        return "Almacén", records
    elif fuente_detectada == "Sostenibilidad":
        records = extract_sostenibilidad(file_path, tipo_operacion, target_sheet=hoja_seleccionada)
        return "Sostenibilidad", records
    elif fuente_detectada == "Uziel":
        records = extract_uziel(file_path, tipo_operacion, target_sheet=hoja_seleccionada)
        return "Uziel", records
    elif fuente_detectada == "Concentrado":
        records = extract_from_concentrado(file_path, tipo_operacion)
        return "Concentrado", records
    else:
        records = extract_from_concentrado(file_path, tipo_operacion)
        return fuente_detectada, records


# ==========================================
# GESTOR DE INGESTA SUPABASE
# ==========================================
class SupabaseIngestor:
    def __init__(self, url: Optional[str] = None, key: Optional[str] = None):
        load_dotenv()
        self.url = url or os.getenv("SUPABASE_URL")
        self.key = (
            key
            or os.getenv("SUPABASE_KEY")
            or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
            or os.getenv("SUPABASE_PUBLISHABLE_KEY")
        )

        if not self.url or not self.key:
            raise ValueError(
                "Credenciales de Supabase no encontradas. "
                "Asegúrate de definir SUPABASE_URL y SUPABASE_KEY (o SUPABASE_SERVICE_ROLE_KEY / SUPABASE_PUBLISHABLE_KEY)."
            )

        self.client: Client = create_client(self.url, self.key, options=ClientOptions(postgrest_client_timeout=5))
        print(f"[CONEXIÓN] Supabase conectado a: {self.url}")

    def clean_tables(self):
        """Limpia todos los registros previos en 'registros_residuos' y 'archivos_subidos'."""
        print("[LIMPIEZA] Vaciando tablas 'registros_residuos' y 'archivos_subidos'...")
        self.client.table("registros_residuos").delete().neq("clave_duplicado", "___non_existent___").execute()
        self.client.table("archivos_subidos").delete().neq("hash_sha256", "___non_existent___").execute()
        print("[OK] Tablas limpiadas exitosamente en Supabase.")

    def ingest_file(self, file_path: str, tipo_operacion: str = "ENTRADAS", modulo: str = "Ingesta Residuos") -> Dict[str, Any]:
        """Procesa un archivo Excel y guarda sus registros en Supabase garantizando idempotencia."""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Archivo no encontrado: {file_path}")

        file_name = os.path.basename(file_path) if isinstance(file_path, (str, Path)) else getattr(file_path, 'name', 'memoria.xlsx')
        file_hash = calculate_file_hash(file_path)

        print(f"\n==================================================")
        print(f"Procesando: {file_name}")
        print(f"Tipo Operación: {tipo_operacion}")
        print(f"SHA-256: {file_hash[:16]}...")
        print(f"==================================================")

        tipo_fuente, records = detect_and_extract(file_path, tipo_operacion)
        print(f"[EXTRACCIÓN] Fuente detectada: '{tipo_fuente}' -> {len(records)} registros válidos.")

        if not records:
            print("[AVISO] No se obtuvieron registros válidos aplicando las reglas de exclusión y categorización.")
            return {"file": file_name, "tipo_fuente": tipo_fuente, "total_records": 0, "inserted": 0}

        # Aplicar regla maestra de conversión de Conos (Pz a Kg)
        records = apply_cono_conversion(records)

        # 1. Registrar / Actualizar archivo en 'archivos_subidos'
        archivo_entry = {
            "hash_sha256": file_hash,
            "nombre": file_name,
            "modulo": modulo,
            "tipo_operacion": tipo_operacion,
            "total_registros": len(records)
        }
        self.client.table("archivos_subidos").upsert(archivo_entry).execute()
        print(f"[OK] Archivo registrado en 'archivos_subidos' (total_registros: {len(records)})")

        # 2. Formatear y preparar registros para 'registros_residuos'
        db_records = []
        for r in records:
            idx = r.get("origen_fila", 0)
            fecha = r["fecha"]
            sitio = r["sitio"]
            tipo_residuo = r["tipo_residuo"]
            kg = float(r["kg"])
            pz = float(r["pz"])
            tarimas = float(r["tarimas"])

            # Si el residuo es Tarima, preservar el valor de piezas tanto en pz como en tarimas
            if tipo_residuo == "Tarima":
                if tarimas == 0.0 and pz > 0:
                    tarimas = pz
                elif pz == 0.0 and tarimas > 0:
                    pz = tarimas

            # Clave única determinista
            clave_duplicado = f"{tipo_operacion}_{fecha}_{sitio}_{tipo_residuo}_{kg}_{pz}_{tarimas}"

            db_records.append({
                "clave_duplicado": clave_duplicado,
                "tipo_operacion": tipo_operacion,
                "fecha": fecha,
                "sitio": sitio,
                "tipo_residuo": tipo_residuo,
                "kg": kg,
                "pz": pz,
                "tarimas": tarimas,
                "archivo_hash": file_hash
            })

        # 3. Deduplicar en memoria por clave_duplicado para evitar conflicto en el mismo lote de Postgres
        dict_records = {}
        for r in db_records:
            dict_records[r["clave_duplicado"]] = r
        unique_records = list(dict_records.values())

        # 4. Inserción por lotes (upsert) en 'registros_residuos'
        batch_size = 100
        total_inserted = 0
        for i in range(0, len(unique_records), batch_size):
            batch = unique_records[i:i + batch_size]
            res = self.client.table("registros_residuos").upsert(batch).execute()
            count = len(res.data) if res.data else len(batch)
            total_inserted += count
            print(f"  -> Lote {i // batch_size + 1}: {count} registros subidos.")

        print(f"[ÉXITO] {total_inserted} registros guardados en 'registros_residuos'.")
        return {
            "file": file_name,
            "tipo_fuente": tipo_fuente,
            "total_records": len(records),
            "inserted": total_inserted
        }


# ==========================================
# RUTINA DE EJECUCIÓN PRINCIPAL
# ==========================================
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Ingesta de Residuos a Supabase")
    parser.add_argument("--file", type=str, help="Ruta de archivo Excel individual a procesar")
    parser.add_argument("--tipo", type=str, default="ENTRADAS", choices=["ENTRADAS", "SALIDAS"], help="Tipo de operación")
    parser.add_argument("--dir", type=str, help="Directorio con archivos Excel a procesar")
    parser.add_argument("--clean", action="store_true", help="Limpia registros previos antes de procesar")
    args = parser.parse_args()

    ingestor = SupabaseIngestor()

    if args.clean:
        ingestor.clean_tables()

    archivos_a_procesar = []

    if args.file:
        archivos_a_procesar.append(args.file)
    elif args.dir and os.path.exists(args.dir):
        for f in os.listdir(args.dir):
            if f.endswith(('.xlsx', '.xls')) and not f.startswith('~$'):
                archivos_a_procesar.append(os.path.join(args.dir, f))
    else:
        # Detección automática de los Excels de prueba oficiales en 'Documentos de prueba'
        base_doc_prueba = r"C:\Users\user\Desktop\Proyecto Yadi\Documentos de prueba"
        if args.tipo == "ENTRADAS":
            candidatos_oficiales = [
                os.path.join(base_doc_prueba, r"Almacén\INVENTARIO OCTUBRE 2026.xlsx"),
                os.path.join(base_doc_prueba, r"Sostenibilidad\RESUMEN SEPTIEMBRE 2026 PERNOD SMO.xlsx"),
                os.path.join(base_doc_prueba, r"Uziel\Formato_Almacen_Reciclaje_Entradas_Salidas.xlsx"),
            ]
        else:
            candidatos_oficiales = [
                os.path.join(base_doc_prueba, r"Facturación\PROGRAMA_ENTREGAS PLASTIC TRADE.xlsx"),
            ]

        existentes = [p for p in candidatos_oficiales if os.path.exists(p)]
        if existentes:
            archivos_a_procesar = existentes
            print(f"[AUTO-DETECCIÓN] Se encontraron {len(existentes)} archivos de prueba en 'Documentos de prueba'.")
        else:
            # Fallback a Desktop/2026 si existiera
            base_2026 = r"C:\Users\user\Desktop\2026"
            if args.tipo == "ENTRADAS":
                fb_oficiales = [
                    os.path.join(base_2026, r"Almacén\INVENTARIO OCTUBRE 2026.xlsx"),
                    os.path.join(base_2026, r"Uziel\Formato_Almacen_Reciclaje_Entradas_Salidas.xlsx"),
                    os.path.join(base_2026, r"Sostenibilidad\RESUMEN SEPTIEMBRE 2026 PERNOD SMO.xlsx"),
                ]
            else:
                fb_oficiales = [
                    os.path.join(base_2026, r"Almacén\SALIDAS 2026.xlsx"),
                ]
            existentes_fb = [p for p in fb_oficiales if os.path.exists(p)]
            if existentes_fb:
                archivos_a_procesar = existentes_fb
                print(f"[AUTO-DETECCIÓN] Se encontraron {len(existentes_fb)} archivos de prueba en 'Desktop/2026'.")
            else:
                fallback_conc = r"C:\Users\user\Desktop\Concentrado_Residuos_20261022_155126.xlsx"
                if os.path.exists(fallback_conc):
                    archivos_a_procesar.append(fallback_conc)
                    print(f"[AUTO-DETECCIÓN] Usando concentrado consolidado: {fallback_conc}")

    if not archivos_a_procesar:
        print("[ERROR] No se encontraron archivos Excel para procesar.")
        sys.exit(1)

    resumen = []
    total_general = 0
    for f in archivos_a_procesar:
        try:
            res = ingestor.ingest_file(f, tipo_operacion=args.tipo)
            resumen.append(res)
            total_general += res["inserted"]
        except Exception as e:
            print(f"[ERROR] Falló procesamiento de {f}: {e}")

    print("\n" + "="*60)
    print("RESUMEN FINAL DE INGESTA A SUPABASE")
    print("="*60)
    for r in resumen:
        print(f" • {r['file']} [{r['tipo_fuente']}]: {r['inserted']} registros insertados.")
    print(f"\nTOTAL GENERAL DE REGISTROS INSERTADOS EXITOSAMENTE: {total_general}")
    print("="*60)


if __name__ == "__main__":
    main()
