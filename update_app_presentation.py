import sys

app_path = "app.py"
with open(app_path, "r", encoding="utf-8") as f:
    code = f.read()

# 1. Target around line 548 before @app.get("/api/metricas")
target_metricas = '''# ==========================================
# ENDPOINT DE MÉTRICAS Y DASHBOARD (MÓDULO 02)
# ==========================================
@app.get("/api/metricas")
async def get_metricas(
    sitio: Optional[str] = None,
    fecha_inicio: Optional[str] = None,
    fecha_fin: Optional[str] = None,
    tipo_operacion: Optional[str] = "ENTRADAS"
):
    """
    Calcula indicadores ecológicos oficiales, tops de generación, distribución
    de Playo por cliente y series temporales con proyección por regresión lineal.
    """
    global ingestor
    if ingestor is None:
        try:
            ingestor = SupabaseIngestor()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error conectando a Supabase: {str(e)}")

    try:
        # 1. Obtener todos los registros base
        query = ingestor.client.table("registros_residuos").select("*")
        
        # Filtro de tipo de operación
        if tipo_operacion and tipo_operacion.upper() in ["ENTRADAS", "SALIDAS"]:
            query = query.eq("tipo_operacion", tipo_operacion.upper())
            
        if fecha_inicio:
            query = query.gte("fecha", fecha_inicio)
        if fecha_fin:
            query = query.lte("fecha", fecha_fin)

        query = query.order("fecha", desc=False)
        all_rows = []
        page = 0
        step = 1000
        while True:
            res = query.range(page * step, (page + 1) * step - 1).execute()
            data = res.data or []
            all_rows.extend(data)
            if len(data) < step:
                break
            page += 1

        # Lista de sitios únicos disponibles para el selector dinámico del frontend
        sitios_unicos = sorted(list(set(r.get("sitio", "").strip() for r in all_rows if r.get("sitio"))))

        # 2. Filtrar por sitio si se especificó
        rows = all_rows
        if sitio and sitio.strip() and sitio.lower() not in ["todos", "todos los sitios", "todas"]:
            filtro_s = normalize_text(sitio)
            rows = [r for r in rows if filtro_s == normalize_text(r.get("sitio", ""))]

        # 3. Sumas de Kg por Tipo de Residuo
        kg_playo = sum(float(r.get("kg") or 0.0) for r in rows if r.get("tipo_residuo") == "Playo")
        # Regla Maestra: kg_cono consolidado con factor oficial 0.3 kg por pieza si kg <= 0
        kg_cono = sum(
            float(r.get("kg") or 0.0) if float(r.get("kg") or 0.0) > 0 else (float(r.get("pz") or 0.0) * 0.3)
            for r in rows if r.get("tipo_residuo") == "Cono"
        )
        kg_carton = sum(float(r.get("kg") or 0.0) for r in rows if r.get("tipo_residuo") == "Cartón")
        kg_chatarra = sum(float(r.get("kg") or 0.0) for r in rows if r.get("tipo_residuo") == "Chatarra")
        kg_tarima = sum(float(r.get("kg") or 0.0) for r in rows if r.get("tipo_residuo") == "Tarima")
        kg_madera = sum(float(r.get("kg") or 0.0) for r in rows if r.get("tipo_residuo") == "Madera")
        
        total_kg = sum(float(r.get("kg") or 0.0) for r in rows)
        total_pz = sum(float(r.get("pz") or 0.0) for r in rows)
        total_tarimas = sum(float(r.get("tarimas") or 0.0) for r in rows)
        total_registros = len(rows)

        # 4. Cálculo estricto de los 5 Indicadores Ecológicos
        # Indicador 1: tCO2e Sin emitir
        tco2e_sin_ec_playo = (kg_playo / 1000.0) * 4.5
        tco2e_sin_ec_cono = (kg_cono / 1000.0) * 0.852
        total_tco2e_sin_ec = tco2e_sin_ec_playo + tco2e_sin_ec_cono

        tco2e_con_ec_playo = (kg_playo / 1000.0) * 1.0
        tco2e_con_ec_cono = (kg_cono / 1000.0) * 0.852
        total_tco2e_con_ec = tco2e_con_ec_playo + tco2e_con_ec_cono

        tco2e_sin_emitir = total_tco2e_sin_ec - total_tco2e_con_ec

        # Indicador 2: Árboles sin talar
        arboles_sin_talar = (kg_cono / 1000.0) * 17.0

        # Indicador 3: Energía no consumida (kW/hr)
        energia_kwh = (kg_cono / 1000.0) * 4200.0

        # Indicador 4: L de agua no consumida
        agua_litros = (kg_cono / 1000.0) * 3500.0

        # Indicador 5: L combustible fósil no consumido
        combustible_litros = (kg_cono / 1000.0) * 1650.0

        # 5. Top 3 Sitios que más generan y Top 3 que menos generan
        site_totals_dict = {}
        for r in rows:
            s_name = r.get("sitio", "General").strip()
            site_totals_dict[s_name] = site_totals_dict.get(s_name, 0.0) + float(r.get("kg") or 0.0)

        sorted_sites = sorted(site_totals_dict.items(), key=lambda x: x[1], reverse=True)
        
        top_mas = []
        for s_name, k_val in sorted_sites[:3]:
            top_mas.append({
                "sitio": s_name,
                "kg": round(k_val, 2),
                "porcentaje": round((k_val / total_kg * 100.0) if total_kg > 0 else 0.0, 1)
            })

        sites_with_kg = [item for item in sorted_sites if item[1] > 0]
        top_menos = []
        if len(sites_with_kg) <= 3:
            for s_name, k_val in reversed(sites_with_kg):
                top_menos.append({
                    "sitio": s_name,
                    "kg": round(k_val, 2),
                    "porcentaje": round((k_val / total_kg * 100.0) if total_kg > 0 else 0.0, 1)
                })
        else:
            for s_name, k_val in reversed(sites_with_kg[-3:]):
                top_menos.append({
                    "sitio": s_name,
                    "kg": round(k_val, 2),
                    "porcentaje": round((k_val / total_kg * 100.0) if total_kg > 0 else 0.0, 1)
                })

        # 6. Distribución de Playo por Cliente (Dona)
        playo_by_site = {}
        for r in rows:
            if r.get("tipo_residuo") == "Playo":
                s_name = r.get("sitio", "General").strip()
                playo_by_site[s_name] = playo_by_site.get(s_name, 0.0) + float(r.get("kg") or 0.0)

        sorted_playo = sorted(playo_by_site.items(), key=lambda x: x[1], reverse=True)
        playo_labels = []
        playo_data = []
        playo_porcentajes = []
        
        if len(sorted_playo) > 6:
            top_p = sorted_playo[:6]
            otros_kg = sum(x[1] for x in sorted_playo[6:])
            for s_name, k_val in top_p:
                playo_labels.append(s_name)
                playo_data.append(round(k_val, 2))
                playo_porcentajes.append(round((k_val / kg_playo * 100.0) if kg_playo > 0 else 0.0, 1))
            if otros_kg > 0:
                playo_labels.append("Otros Sitios")
                playo_data.append(round(otros_kg, 2))
                playo_porcentajes.append(round((otros_kg / kg_playo * 100.0) if kg_playo > 0 else 0.0, 1))
        else:
            for s_name, k_val in sorted_playo:
                playo_labels.append(s_name)
                playo_data.append(round(k_val, 2))
                playo_porcentajes.append(round((k_val / kg_playo * 100.0) if kg_playo > 0 else 0.0, 1))

        # 7. Tendencia temporal y Proyección por regresión lineal
        date_totals = {}
        for r in rows:
            f = r.get("fecha")
            if f:
                date_totals[f] = date_totals.get(f, 0.0) + float(r.get("kg") or 0.0)

        sorted_dates = sorted(date_totals.keys())
        y_hist = [round(date_totals[d], 2) for d in sorted_dates]
        n = len(sorted_dates)

        m = 0.0
        b = 0.0
        if n >= 2:
            x_vals = list(range(n))
            x_mean = sum(x_vals) / n
            y_mean = sum(y_hist) / n
            denom = sum((x - x_mean) ** 2 for x in x_vals)
            if denom > 0:
                numer = sum((x_vals[i] - x_mean) * (y_hist[i] - y_mean) for i in range(n))
                m = numer / denom
                b = y_mean - m * x_mean

        trend_labels = list(sorted_dates)
        trend_historical = list(y_hist)
        trend_fitted = [round(max(0.0, m * i + b), 2) for i in range(n)] if n >= 2 else list(y_hist)

        if n >= 1:
            proj_1 = round(max(0.0, m * n + b), 2)
            proj_2 = round(max(0.0, m * (n + 1) + b), 2)
            
            try:
                from datetime import datetime, timedelta
                last_dt = datetime.strptime(sorted_dates[-1], "%Y-%m-%d")
                d_p1 = (last_dt + timedelta(days=7)).strftime("%Y-%m-%d")
                d_p2 = (last_dt + timedelta(days=14)).strftime("%Y-%m-%d")
                trend_labels.extend([f"Proy. {d_p1}", f"Proy. {d_p2}"])
            except Exception:
                trend_labels.extend(["Proy. +1", "Proy. +2"])

            trend_historical.extend([None, None])
            trend_fitted.extend([proj_1, proj_2])
            projected_points = [None] * (n - 1) + [y_hist[-1], proj_1, proj_2]
        else:
            projected_points = []

        return JSONResponse({
            "success": True,
            "sitios_disponibles": sitios_unicos,'''

replacement_metricas = '''# ==========================================
# GESTOR DE SESIONES DEL MODO PRESENTACIÓN (SANDBOX AISLADO)
# ==========================================
SESIONES_PRESENTACION: Dict[str, Dict[str, Any]] = {}

def calcular_metricas_residuos(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Función unificada para calcular las 5 métricas ecológicas oficiales,
    los rankings de sitios, la distribución de Playo y la proyección temporal por regresión.
    Garantiza consistencia matemática exacta entre producción (Supabase) y el Modo Presentación (Sandbox).
    """
    # 1. Sumas de Kg por Tipo de Residuo
    kg_playo = sum(float(r.get("kg") or 0.0) for r in rows if r.get("tipo_residuo") == "Playo")
    # Regla Maestra: kg_cono consolidado con factor oficial 0.3 kg por pieza si kg <= 0
    kg_cono = sum(
        float(r.get("kg") or 0.0) if float(r.get("kg") or 0.0) > 0 else (float(r.get("pz") or 0.0) * 0.3)
        for r in rows if r.get("tipo_residuo") == "Cono"
    )
    kg_carton = sum(float(r.get("kg") or 0.0) for r in rows if r.get("tipo_residuo") == "Cartón")
    kg_chatarra = sum(float(r.get("kg") or 0.0) for r in rows if r.get("tipo_residuo") == "Chatarra")
    kg_tarima = sum(float(r.get("kg") or 0.0) for r in rows if r.get("tipo_residuo") == "Tarima")
    kg_madera = sum(float(r.get("kg") or 0.0) for r in rows if r.get("tipo_residuo") == "Madera")

    total_kg = sum(float(r.get("kg") or 0.0) for r in rows)
    total_pz = sum(float(r.get("pz") or 0.0) for r in rows)
    total_tarimas = sum(float(r.get("tarimas") or 0.0) for r in rows)
    total_registros = len(rows)

    # 2. Cálculo estricto de los 5 Indicadores Ecológicos
    # Indicador 1: tCO2e Sin emitir
    tco2e_sin_ec_playo = (kg_playo / 1000.0) * 4.5
    tco2e_sin_ec_cono = (kg_cono / 1000.0) * 0.852
    total_tco2e_sin_ec = tco2e_sin_ec_playo + tco2e_sin_ec_cono

    tco2e_con_ec_playo = (kg_playo / 1000.0) * 1.0
    tco2e_con_ec_cono = (kg_cono / 1000.0) * 0.852
    total_tco2e_con_ec = tco2e_con_ec_playo + tco2e_con_ec_cono

    tco2e_sin_emitir = total_tco2e_sin_ec - total_tco2e_con_ec

    # Indicador 2: Árboles sin talar
    arboles_sin_talar = (kg_cono / 1000.0) * 17.0

    # Indicador 3: Energía no consumida (kW/hr)
    energia_kwh = (kg_cono / 1000.0) * 4200.0

    # Indicador 4: L de agua no consumida
    agua_litros = (kg_cono / 1000.0) * 3500.0

    # Indicador 5: L combustible fósil no consumido
    combustible_litros = (kg_cono / 1000.0) * 1650.0

    # 3. Top 3 Sitios que más generan y Top 3 que menos generan
    site_totals_dict = {}
    for r in rows:
        s_name = (r.get("sitio") or "General").strip()
        site_totals_dict[s_name] = site_totals_dict.get(s_name, 0.0) + float(r.get("kg") or 0.0)

    sorted_sites = sorted(site_totals_dict.items(), key=lambda x: x[1], reverse=True)

    top_mas = []
    for s_name, k_val in sorted_sites[:3]:
        top_mas.append({
            "sitio": s_name,
            "kg": round(k_val, 2),
            "porcentaje": round((k_val / total_kg * 100.0) if total_kg > 0 else 0.0, 1)
        })

    sites_with_kg = [item for item in sorted_sites if item[1] > 0]
    top_menos = []
    if len(sites_with_kg) <= 3:
        for s_name, k_val in reversed(sites_with_kg):
            top_menos.append({
                "sitio": s_name,
                "kg": round(k_val, 2),
                "porcentaje": round((k_val / total_kg * 100.0) if total_kg > 0 else 0.0, 1)
            })
    else:
        for s_name, k_val in reversed(sites_with_kg[-3:]):
            top_menos.append({
                "sitio": s_name,
                "kg": round(k_val, 2),
                "porcentaje": round((k_val / total_kg * 100.0) if total_kg > 0 else 0.0, 1)
            })

    # 4. Distribución de Playo por Cliente (Dona)
    playo_by_site = {}
    for r in rows:
        if r.get("tipo_residuo") == "Playo":
            s_name = (r.get("sitio") or "General").strip()
            playo_by_site[s_name] = playo_by_site.get(s_name, 0.0) + float(r.get("kg") or 0.0)

    sorted_playo = sorted(playo_by_site.items(), key=lambda x: x[1], reverse=True)
    playo_labels = []
    playo_data = []
    playo_porcentajes = []

    if len(sorted_playo) > 6:
        top_p = sorted_playo[:6]
        otros_kg = sum(x[1] for x in sorted_playo[6:])
        for s_name, k_val in top_p:
            playo_labels.append(s_name)
            playo_data.append(round(k_val, 2))
            playo_porcentajes.append(round((k_val / kg_playo * 100.0) if kg_playo > 0 else 0.0, 1))
        if otros_kg > 0:
            playo_labels.append("Otros Sitios")
            playo_data.append(round(otros_kg, 2))
            playo_porcentajes.append(round((otros_kg / kg_playo * 100.0) if kg_playo > 0 else 0.0, 1))
    else:
        for s_name, k_val in sorted_playo:
            playo_labels.append(s_name)
            playo_data.append(round(k_val, 2))
            playo_porcentajes.append(round((k_val / kg_playo * 100.0) if kg_playo > 0 else 0.0, 1))

    # 5. Tendencia temporal y Proyección por regresión lineal
    date_totals = {}
    for r in rows:
        f = r.get("fecha")
        if f:
            date_totals[f] = date_totals.get(f, 0.0) + float(r.get("kg") or 0.0)

    sorted_dates = sorted(date_totals.keys())
    y_hist = [round(date_totals[d], 2) for d in sorted_dates]
    n = len(sorted_dates)

    m = 0.0
    b = 0.0
    if n >= 2:
        x_vals = list(range(n))
        x_mean = sum(x_vals) / n
        y_mean = sum(y_hist) / n
        denom = sum((x - x_mean) ** 2 for x in x_vals)
        if denom > 0:
            numer = sum((x_vals[i] - x_mean) * (y_hist[i] - y_mean) for i in range(n))
            m = numer / denom
            b = y_mean - m * x_mean

    trend_labels = list(sorted_dates)
    trend_historical = list(y_hist)
    trend_fitted = [round(max(0.0, m * i + b), 2) for i in range(n)] if n >= 2 else list(y_hist)

    if n >= 1:
        proj_1 = round(max(0.0, m * n + b), 2)
        proj_2 = round(max(0.0, m * (n + 1) + b), 2)
        try:
            from datetime import datetime, timedelta
            last_dt = datetime.strptime(sorted_dates[-1], "%Y-%m-%d")
            d_p1 = (last_dt + timedelta(days=7)).strftime("%Y-%m-%d")
            d_p2 = (last_dt + timedelta(days=14)).strftime("%Y-%m-%d")
            trend_labels.extend([f"Proy. {d_p1}", f"Proy. {d_p2}"])
        except Exception:
            trend_labels.extend(["Proy. +1", "Proy. +2"])

        trend_historical.extend([None, None])
        trend_fitted.extend([proj_1, proj_2])
        projected_points = [None] * (n - 1) + [y_hist[-1], proj_1, proj_2]
    else:
        projected_points = []

    return {
        "kpis_ecologicos": {
            "tco2e_sin_emitir": round(tco2e_sin_emitir, 2),
            "arboles_sin_talar": round(arboles_sin_talar, 1),
            "energia_kwh": round(energia_kwh, 1),
            "agua_litros": round(agua_litros, 1),
            "combustible_litros": round(combustible_litros, 1)
        },
        "totales": {
            "total_kg": round(total_kg, 2),
            "total_pz": round(total_pz, 2),
            "total_tarimas": round(total_tarimas, 2),
            "total_registros": total_registros,
            "kg_playo": round(kg_playo, 2),
            "kg_cono": round(kg_cono, 2),
            "kg_carton": round(kg_carton, 2),
            "kg_chatarra": round(kg_chatarra, 2),
            "kg_tarima": round(kg_tarima, 2),
            "kg_madera": round(kg_madera, 2)
        },
        "top_mas_generan": top_mas,
        "top_menos_generan": top_menos,
        "volumen_playo_cliente": {
            "labels": playo_labels,
            "data": playo_data,
            "porcentajes": playo_porcentajes
        },
        "tendencia_proyeccion": {
            "labels": trend_labels,
            "historico": trend_historical,
            "regresion": trend_fitted,
            "proyeccion": projected_points,
            "pendiente_m": round(m, 2),
            "intercepto_b": round(b, 2)
        }
    }


# ==========================================
# ENDPOINTS DEL MODO PRESENTACIÓN AISLADA (SANDBOX)
# ==========================================
@app.post("/api/presentacion/cargar")
async def presentacion_cargar(file: UploadFile = File(...)):
    """
    Carga un archivo Excel para iniciar el Modo Presentación Aislada.
    REGLA INVIOLABLE: Los datos NUNCA se guardan en Supabase ni en disco permanente.
    Se procesan y mantienen exclusivamente en la memoria de la sesión.
    """
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="Debes proporcionar un archivo Excel válido.")

    temp_dir = tempfile.mkdtemp()
    try:
        temp_path = os.path.join(temp_dir, os.path.basename(file.filename))
        with open(temp_path, "wb") as buf:
            shutil.copyfileobj(file.file, buf)

        # 1. Detección inteligente de fuente y hoja
        fuente_detectada, hoja_detectada, _ = identificar_tipo_documento(temp_path)

        # 2. Extracción desacoplada en memoria
        if fuente_detectada == "Facturación":
            records, _ = extract_facturacion_salidas(temp_path, target_sheet=hoja_detectada)
        elif fuente_detectada == "Almacén":
            records = extract_almacen(temp_path, "ENTRADAS", target_sheet=hoja_detectada)
        elif fuente_detectada == "Sostenibilidad":
            records = extract_sostenibilidad(temp_path, "ENTRADAS", target_sheet=hoja_detectada)
        elif fuente_detectada == "Uziel":
            records = extract_uziel(temp_path, "ENTRADAS", target_sheet=hoja_detectada)
        else:
            records = extract_from_concentrado(temp_path, "ENTRADAS", target_sheet=hoja_detectada)

        if not records:
            raise HTTPException(
                status_code=400,
                detail="No se encontraron registros valorizables válidos en el archivo proporcionado."
            )

        records = apply_cono_conversion(records)
        sitios_unicos = sorted(list(set((r.get("sitio") or "").strip() for r in records if r.get("sitio"))))

        # 3. Cálculo de métricas e indicadores en memoria
        metricas = calcular_metricas_residuos(records)

        session_id = str(uuid.uuid4())
        SESIONES_PRESENTACION[session_id] = {
            "session_id": session_id,
            "records": records,
            "nombre_archivo": file.filename,
            "fuente_detectada": fuente_detectada,
            "hoja_detectada": hoja_detectada,
            "timestamp": time.time()
        }

        return JSONResponse({
            "success": True,
            "modo_presentacion": True,
            "session_id": session_id,
            "nombre_archivo": file.filename,
            "fuente_detectada": fuente_detectada,
            "hoja_detectada": hoja_detectada,
            "sitios_disponibles": sitios_unicos,
            "kpis_ecologicos": metricas["kpis_ecologicos"],
            "totales": metricas["totales"],
            "top_mas_generan": metricas["top_mas_generan"],
            "top_menos_generan": metricas["top_menos_generan"],
            "volumen_playo_cliente": metricas["volumen_playo_cliente"],
            "tendencia_proyeccion": metricas["tendencia_proyeccion"],
            "records": records[:2000]
        })

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error procesando archivo para presentación: {str(e)}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@app.post("/api/presentacion/metricas")
async def presentacion_filtrar_metricas(request: Request):
    """
    Filtra los datos de la sesión de presentación en memoria y recalcula métricas
    sin interactuar con Supabase.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    session_id = body.get("session_id")
    if not session_id or session_id not in SESIONES_PRESENTACION:
        raise HTTPException(status_code=404, detail="Sesión de presentación no encontrada o expirada.")

    sesion = SESIONES_PRESENTACION[session_id]
    all_records = sesion["records"]

    sitio = body.get("sitio")
    desde = body.get("fecha_inicio")
    hasta = body.get("fecha_fin")
    operacion = body.get("tipo_operacion")

    filtered = all_records
    if operacion and str(operacion).upper() in ["ENTRADAS", "SALIDAS"]:
        filtered = [r for r in filtered if str(r.get("tipo_operacion") or "").upper() == str(operacion).upper()]
    if desde:
        filtered = [r for r in filtered if r.get("fecha") and r["fecha"] >= desde]
    if hasta:
        filtered = [r for r in filtered if r.get("fecha") and r["fecha"] <= hasta]
    if sitio and sitio.strip() and sitio.lower() not in ["todos", "todos los sitios", "todas"]:
        f_norm = normalize_text(sitio)
        filtered = [r for r in filtered if f_norm in normalize_text(r.get("sitio", ""))]

    sitios_unicos = sorted(list(set((r.get("sitio") or "").strip() for r in all_records if r.get("sitio"))))
    metricas = calcular_metricas_residuos(filtered)

    return JSONResponse({
        "success": True,
        "modo_presentacion": True,
        "session_id": session_id,
        "nombre_archivo": sesion["nombre_archivo"],
        "sitios_disponibles": sitios_unicos,
        "kpis_ecologicos": metricas["kpis_ecologicos"],
        "totales": metricas["totales"],
        "top_mas_generan": metricas["top_mas_generan"],
        "top_menos_generan": metricas["top_menos_generan"],
        "volumen_playo_cliente": metricas["volumen_playo_cliente"],
        "tendencia_proyeccion": metricas["tendencia_proyeccion"]
    })


# ==========================================
# ENDPOINT DE MÉTRICAS Y DASHBOARD (MÓDULO 02)
# ==========================================
@app.get("/api/metricas")
async def get_metricas(
    sitio: Optional[str] = None,
    fecha_inicio: Optional[str] = None,
    fecha_fin: Optional[str] = None,
    tipo_operacion: Optional[str] = "ENTRADAS"
):
    """
    Calcula indicadores ecológicos oficiales, tops de generación, distribución
    de Playo por cliente y series temporales con proyección por regresión lineal desde Supabase.
    """
    global ingestor
    if ingestor is None:
        try:
            ingestor = SupabaseIngestor()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error conectando a Supabase: {str(e)}")

    try:
        # 1. Obtener todos los registros base
        query = ingestor.client.table("registros_residuos").select("*")
        
        # Filtro de tipo de operación
        if tipo_operacion and tipo_operacion.upper() in ["ENTRADAS", "SALIDAS"]:
            query = query.eq("tipo_operacion", tipo_operacion.upper())
            
        if fecha_inicio:
            query = query.gte("fecha", fecha_inicio)
        if fecha_fin:
            query = query.lte("fecha", fecha_fin)

        query = query.order("fecha", desc=False)
        all_rows = []
        page = 0
        step = 1000
        while True:
            res = query.range(page * step, (page + 1) * step - 1).execute()
            data = res.data or []
            all_rows.extend(data)
            if len(data) < step:
                break
            page += 1

        sitios_unicos = sorted(list(set(r.get("sitio", "").strip() for r in all_rows if r.get("sitio"))))

        # 2. Filtrar por sitio si se especificó
        rows = all_rows
        if sitio and sitio.strip() and sitio.lower() not in ["todos", "todos los sitios", "todas"]:
            filtro_s = normalize_text(sitio)
            rows = [r for r in rows if filtro_s == normalize_text(r.get("sitio", ""))]

        metricas = calcular_metricas_residuos(rows)

        return JSONResponse({
            "success": True,
            "sitios_disponibles": sitios_unicos,
            "kpis_ecologicos": metricas["kpis_ecologicos"],
            "totales": metricas["totales"],
            "top_mas_generan": metricas["top_mas_generan"],
            "top_menos_generan": metricas["top_menos_generan"],
            "volumen_playo_cliente": metricas["volumen_playo_cliente"],
            "tendencia_proyeccion": metricas["tendencia_proyeccion"]
        })'''

# 2. Target for construir_contexto_desde_records and asistente_ia_chat
target_asistente = '''def obtener_contexto_supabase(ing_client: SupabaseIngestor, ttl_segundos: int = 40) -> str:
    """
    Extrae las métricas analíticas consolidadas y movimientos recientes
    desde Supabase para proveer grounding estricto al Asistente IA.
    """
    import time
    import json
    now = time.time()
    if _asistente_contexto_cache["texto"] and (now - _asistente_contexto_cache["timestamp"] < ttl_segundos):
        return _asistente_contexto_cache["texto"]

    all_rows = []
    page = 0
    step = 1000
    while True:
        res = ing_client.client.table("registros_residuos").select(
            "tipo_operacion,fecha,sitio,tipo_residuo,kg,pz,tarimas"
        ).range(page * step, (page + 1) * step - 1).execute()
        data = res.data or []
        all_rows.extend(data)
        if len(data) < step:
            break
        page += 1

    total_registros = len(all_rows)

    # 1. Totales por categoría oficial
    totales_por_residuo = {cat: {"kg": 0.0, "pz": 0.0, "tarimas": 0.0, "conteo": 0} for cat in CATALOGO_RESIDUOS}
    totales_por_operacion = {
        "ENTRADAS": {"kg": 0.0, "pz": 0.0, "tarimas": 0.0, "conteo": 0},
        "SALIDAS": {"kg": 0.0, "pz": 0.0, "tarimas": 0.0, "conteo": 0}
    }
    volumen_por_sitio = {}
    fechas = []

    for r in all_rows:
        op = (r.get("tipo_operacion") or "ENTRADAS").upper()
        cat = r.get("tipo_residuo") or "Playo"
        kg = float(r.get("kg") or 0.0)
        pz = float(r.get("pz") or 0.0)
        tarimas = float(r.get("tarimas") or 0.0)
        sitio = (r.get("sitio") or "Sin Sitio").strip()
        fecha = r.get("fecha")

        if fecha:
            fechas.append(fecha)

        if cat in totales_por_residuo:
            totales_por_residuo[cat]["kg"] += kg
            totales_por_residuo[cat]["pz"] += pz
            totales_por_residuo[cat]["tarimas"] += tarimas
            totales_por_residuo[cat]["conteo"] += 1

        if op in totales_por_operacion:
            totales_por_operacion[op]["kg"] += kg
            totales_por_operacion[op]["pz"] += pz
            totales_por_operacion[op]["tarimas"] += tarimas
            totales_por_operacion[op]["conteo"] += 1

        volumen_por_sitio[sitio] = volumen_por_sitio.get(sitio, 0.0) + kg

    sitios_ordenados = sorted(volumen_por_sitio.items(), key=lambda x: x[1], reverse=True)
    top_sitios_mayor = [{"sitio": s, "kg_total": round(k, 2)} for s, k in sitios_ordenados[:10]]
    top_sitios_menor = [{"sitio": s, "kg_total": round(k, 2)} for s, k in reversed(sitios_ordenados[-5:])] if len(sitios_ordenados) > 0 else []

    fechas_validas = sorted([f for f in fechas if f])
    fecha_min = fechas_validas[0] if fechas_validas else "N/A"
    fecha_max = fechas_validas[-1] if fechas_validas else "N/A"

    rows_con_fecha = [r for r in all_rows if r.get("fecha")]
    rows_con_fecha.sort(key=lambda x: x.get("fecha", ""), reverse=True)
    ultimos_20 = [
        {
            "fecha": r.get("fecha"),
            "operacion": r.get("tipo_operacion"),
            "sitio": r.get("sitio"),
            "residuo": r.get("tipo_residuo"),
            "kg": round(float(r.get("kg") or 0.0), 2),
            "pz": round(float(r.get("pz") or 0.0), 2),
            "tarimas": round(float(r.get("tarimas") or 0.0), 2)
        }
        for r in rows_con_fecha[:20]
    ]

    totales_residuos_fmt = {
        k: {
            "kg": round(v["kg"], 2),
            "pz": round(v["pz"], 2),
            "tarimas": round(v["tarimas"], 2),
            "movimientos": v["conteo"]
        }
        for k, v in totales_por_residuo.items()
    }
    totales_operacion_fmt = {
        k: {
            "kg": round(v["kg"], 2),
            "pz": round(v["pz"], 2),
            "tarimas": round(v["tarimas"], 2),
            "movimientos": v["conteo"]
        }
        for k, v in totales_por_operacion.items()
    }

    total_kg_global = sum(v["kg"] for v in totales_residuos_fmt.values())
    total_pz_global = sum(v["pz"] for v in totales_residuos_fmt.values())
    total_tarimas_global = sum(v["tarimas"] for v in totales_residuos_fmt.values())

    contexto = {
        "resumen_general": {
            "total_registros_almacenados": total_registros,
            "total_kg_acumulados": round(total_kg_global, 2),
            "total_pz_acumuladas": round(total_pz_global, 2),
            "total_tarimas_acumuladas": round(total_tarimas_global, 2),
            "rango_fechas": {"fecha_minima": fecha_min, "fecha_maxima": fecha_max},
            "cantidad_sitios_o_clientes": len(volumen_por_sitio)
        },
        "totales_por_tipo_residuo_oficial": totales_residuos_fmt,
        "totales_por_tipo_operacion": totales_operacion_fmt,
        "top_10_clientes_mayor_volumen_kg": top_sitios_mayor,
        "top_5_clientes_menor_volumen_kg": top_sitios_menor,
        "ultimos_20_movimientos_recientes": ultimos_20
    }

    texto_contexto = json.dumps(contexto, ensure_ascii=False, indent=2)
    _asistente_contexto_cache["timestamp"] = now
    _asistente_contexto_cache["texto"] = texto_contexto
    return texto_contexto


@app.post("/api/asistente-ia")
async def asistente_ia_chat(request: Request):
    """
    Endpoint del Asistente IA con Grounding estricto (cero alucinaciones).
    Consulta Supabase en tiempo real e interactúa con OpenAI gpt-4o-mini a temperature=0.0.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    pregunta = body.get("pregunta", "").strip() if isinstance(body, dict) else ""
    if not pregunta:
        raise HTTPException(
            status_code=400,
            detail="Debes proporcionar una pregunta válida en el campo 'pregunta'."
        )

    # Verificar o inicializar cliente OpenAI
    global openai_client
    if openai_client is None:
        key_env = os.getenv("OPENAI_API_KEY")
        if key_env:
            try:
                openai_client = openai.OpenAI(api_key=key_env)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Error inicializando OpenAI: {str(e)}")
        else:
            raise HTTPException(
                status_code=500,
                detail="La variable de entorno OPENAI_API_KEY no está configurada en el servidor."
            )

    # Verificar o inicializar cliente Supabase
    global ingestor
    if ingestor is None:
        try:
            ingestor = SupabaseIngestor()
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"No se pudo conectar a Supabase para obtener el contexto de datos: {str(e)}"
            )

    try:
        # 1. Extracción de contexto en tiempo real desde Supabase
        contexto_datos = obtener_contexto_supabase(ingestor)

        # 2. Llamada a OpenAI gpt-4o-mini con temperature=0.0
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.0,
            messages=[
                {"role": "system", "content": f"{PROMPT_SISTEMA_ASISTENTE}\n\nCONTEXTO DE DATOS:\n{contexto_datos}"},
                {"role": "user", "content": pregunta}
            ]
        )'''

replacement_asistente = '''def construir_contexto_desde_records(all_rows: List[Dict[str, Any]], es_modo_aislado: bool = False, nombre_archivo: str = "") -> str:
    """
    Construye el contexto JSON estricto para el Asistente IA a partir de una lista de registros.
    """
    total_registros = len(all_rows)

    totales_por_residuo = {cat: {"kg": 0.0, "pz": 0.0, "tarimas": 0.0, "conteo": 0} for cat in CATALOGO_RESIDUOS}
    totales_por_operacion = {
        "ENTRADAS": {"kg": 0.0, "pz": 0.0, "tarimas": 0.0, "conteo": 0},
        "SALIDAS": {"kg": 0.0, "pz": 0.0, "tarimas": 0.0, "conteo": 0}
    }
    volumen_por_sitio = {}
    fechas = []

    for r in all_rows:
        op = (r.get("tipo_operacion") or "ENTRADAS").upper()
        cat = r.get("tipo_residuo") or "Playo"
        kg = float(r.get("kg") or 0.0)
        pz = float(r.get("pz") or 0.0)
        tarimas = float(r.get("tarimas") or 0.0)
        sitio = (r.get("sitio") or "Sin Sitio").strip()
        fecha = r.get("fecha")

        if fecha:
            fechas.append(fecha)

        if cat in totales_por_residuo:
            totales_por_residuo[cat]["kg"] += kg
            totales_por_residuo[cat]["pz"] += pz
            totales_por_residuo[cat]["tarimas"] += tarimas
            totales_por_residuo[cat]["conteo"] += 1

        if op in totales_por_operacion:
            totales_por_operacion[op]["kg"] += kg
            totales_por_operacion[op]["pz"] += pz
            totales_por_operacion[op]["tarimas"] += tarimas
            totales_por_operacion[op]["conteo"] += 1

        volumen_por_sitio[sitio] = volumen_por_sitio.get(sitio, 0.0) + kg

    sitios_ordenados = sorted(volumen_por_sitio.items(), key=lambda x: x[1], reverse=True)
    top_sitios_mayor = [{"sitio": s, "kg_total": round(k, 2)} for s, k in sitios_ordenados[:10]]
    top_sitios_menor = [{"sitio": s, "kg_total": round(k, 2)} for s, k in reversed(sitios_ordenados[-5:])] if len(sitios_ordenados) > 0 else []

    fechas_validas = sorted([f for f in fechas if f])
    fecha_min = fechas_validas[0] if fechas_validas else "N/A"
    fecha_max = fechas_validas[-1] if fechas_validas else "N/A"

    rows_con_fecha = [r for r in all_rows if r.get("fecha")]
    rows_con_fecha.sort(key=lambda x: x.get("fecha", ""), reverse=True)
    ultimos_20 = [
        {
            "fecha": r.get("fecha"),
            "operacion": r.get("tipo_operacion"),
            "sitio": r.get("sitio"),
            "residuo": r.get("tipo_residuo"),
            "kg": round(float(r.get("kg") or 0.0), 2),
            "pz": round(float(r.get("pz") or 0.0), 2),
            "tarimas": round(float(r.get("tarimas") or 0.0), 2)
        }
        for r in rows_con_fecha[:20]
    ]

    totales_residuos_fmt = {
        k: {
            "kg": round(v["kg"], 2),
            "pz": round(v["pz"], 2),
            "tarimas": round(v["tarimas"], 2),
            "movimientos": v["conteo"]
        }
        for k, v in totales_por_residuo.items()
    }
    totales_operacion_fmt = {
        k: {
            "kg": round(v["kg"], 2),
            "pz": round(v["pz"], 2),
            "tarimas": round(v["tarimas"], 2),
            "movimientos": v["conteo"]
        }
        for k, v in totales_por_operacion.items()
    }

    total_kg_global = sum(v["kg"] for v in totales_residuos_fmt.values())
    total_pz_global = sum(v["pz"] for v in totales_residuos_fmt.values())
    total_tarimas_global = sum(v["tarimas"] for v in totales_residuos_fmt.values())

    contexto = {
        "entorno_operativo": "MODO_PRESENTACION_AISLADA_SANDBOX" if es_modo_aislado else "PRODUCCION_SUPABASE",
        "archivo_origen": nombre_archivo if es_modo_aislado else "Base de datos Supabase (registros_residuos)",
        "resumen_general": {
            "total_registros_almacenados": total_registros,
            "total_kg_acumulados": round(total_kg_global, 2),
            "total_pz_acumuladas": round(total_pz_global, 2),
            "total_tarimas_acumuladas": round(total_tarimas_global, 2),
            "rango_fechas": {"fecha_minima": fecha_min, "fecha_maxima": fecha_max},
            "cantidad_sitios_o_clientes": len(volumen_por_sitio)
        },
        "totales_por_tipo_residuo_oficial": totales_residuos_fmt,
        "totales_por_tipo_operacion": totales_operacion_fmt,
        "top_10_clientes_mayor_volumen_kg": top_sitios_mayor,
        "top_5_clientes_menor_volumen_kg": top_sitios_menor,
        "ultimos_20_movimientos_recientes": ultimos_20
    }

    return json.dumps(contexto, ensure_ascii=False, indent=2)


def obtener_contexto_supabase(ing_client: SupabaseIngestor, ttl_segundos: int = 40) -> str:
    """
    Extrae las métricas analíticas consolidadas y movimientos recientes
    desde Supabase para proveer grounding estricto al Asistente IA.
    """
    import time
    now = time.time()
    if _asistente_contexto_cache["texto"] and (now - _asistente_contexto_cache["timestamp"] < ttl_segundos):
        return _asistente_contexto_cache["texto"]

    all_rows = []
    page = 0
    step = 1000
    while True:
        res = ing_client.client.table("registros_residuos").select(
            "tipo_operacion,fecha,sitio,tipo_residuo,kg,pz,tarimas"
        ).range(page * step, (page + 1) * step - 1).execute()
        data = res.data or []
        all_rows.extend(data)
        if len(data) < step:
            break
        page += 1

    texto_contexto = construir_contexto_desde_records(all_rows, es_modo_aislado=False)
    _asistente_contexto_cache["timestamp"] = now
    _asistente_contexto_cache["texto"] = texto_contexto
    return texto_contexto


@app.post("/api/asistente-ia")
async def asistente_ia_chat(request: Request):
    """
    Endpoint del Asistente IA con Grounding estricto (cero alucinaciones).
    Soporta Modo Normal (Supabase en tiempo real) y Modo Presentación Aislada (Sandbox en memoria).
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    pregunta = body.get("pregunta", "").strip() if isinstance(body, dict) else ""
    if not pregunta:
        raise HTTPException(
            status_code=400,
            detail="Debes proporcionar una pregunta válida en el campo 'pregunta'."
        )

    modo_presentacion = bool(body.get("modo_presentacion"))
    session_id = body.get("session_id")
    datos_aislados = body.get("datos_aislados")

    # Verificar o inicializar cliente OpenAI
    global openai_client
    if openai_client is None:
        key_env = os.getenv("OPENAI_API_KEY")
        if key_env:
            try:
                openai_client = openai.OpenAI(api_key=key_env)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Error inicializando OpenAI: {str(e)}")
        else:
            raise HTTPException(
                status_code=500,
                detail="La variable de entorno OPENAI_API_KEY no está configurada en el servidor."
            )

    if modo_presentacion:
        # AISLAMIENTO ESTRICTO: Solo leer memoria, jamás Supabase
        registros_sesion = []
        nombre_arch = "archivo_presentacion.xlsx"
        if session_id and session_id in SESIONES_PRESENTACION:
            registros_sesion = SESIONES_PRESENTACION[session_id]["records"]
            nombre_arch = SESIONES_PRESENTACION[session_id].get("nombre_archivo", nombre_arch)
        elif isinstance(datos_aislados, list):
            registros_sesion = datos_aislados

        if not registros_sesion:
            raise HTTPException(
                status_code=400,
                detail="No se encontraron registros en la sesión aislada del Modo Presentación."
            )

        contexto_datos = construir_contexto_desde_records(registros_sesion, es_modo_aislado=True, nombre_archivo=nombre_arch)
        prompt_sistema = """Eres el Asistente Experto en Inteligencia Ambiental y Logística Inversa de PLASTIC TRADE • Smart Packaging.
Estás en MODO PRESENTACIÓN AISLADA. Responde y genera análisis basándote ÚNICA Y EXCLUSIVAMENTE en el archivo temporal cargado para esta sesión. No tienes acceso a Supabase.
Tu única fuente de verdad es estrictamente el CONTEXTO DE DATOS proporcionado a continuación.

REGLAS INVIOLABLES DE VERACIDAD Y AISLAMIENTO:
1. Responde basándote exclusivamente en las cifras del archivo de presentación cargado.
2. Si el usuario te pregunta por un dato, residuo o cliente que no figure en este archivo, responde honestamente que no se encuentra en el conjunto de datos de esta presentación.
3. Prohibido inventar datos o hacer referencia a registros fuera de este archivo."""
    else:
        # Flujo Normal: Conexión a Supabase
        global ingestor
        if ingestor is None:
            try:
                ingestor = SupabaseIngestor()
            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"No se pudo conectar a Supabase para obtener el contexto de datos: {str(e)}"
                )
        contexto_datos = obtener_contexto_supabase(ingestor)
        prompt_sistema = PROMPT_SISTEMA_ASISTENTE

    try:
        # Llamada a OpenAI gpt-4o-mini con temperature=0.0
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.0,
            messages=[
                {"role": "system", "content": f"{prompt_sistema}\n\nCONTEXTO DE DATOS:\n{contexto_datos}"},
                {"role": "user", "content": pregunta}
            ]
        )'''

# 3. Target for api_generar_grafico_ia
target_grafico = '''    # 1. Obtener datos de Supabase
    global ingestor
    if ingestor is None:
        try:
            ingestor = SupabaseIngestor()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error conectando a Supabase: {str(e)}")

    query = ingestor.client.table("registros_residuos").select("fecha,sitio,tipo_residuo,tipo_operacion,kg,pz,tarimas")
    if fecha_inicio:
        query = query.gte("fecha", fecha_inicio)
    if fecha_fin:
        query = query.lte("fecha", fecha_fin)
    if tipo_operacion and str(tipo_operacion).upper() in ["ENTRADAS", "SALIDAS"]:
        query = query.eq("tipo_operacion", str(tipo_operacion).upper())

    # Descargar datos paginados (hasta 3000 registros para velocidad de análisis)
    rows = []
    page = 0
    step = 1000
    while page < 3:
        res = query.range(page * step, (page + 1) * step - 1).execute()
        data = res.data or []
        rows.extend(data)
        if len(data) < step:
            break
        page += 1

    # Filtro de sitio en memoria
    if sitio_filtro and sitio_filtro.strip() and sitio_filtro.lower() not in ["todos", "todos los sitios", "todas"]:
        f_norm = normalize_text(sitio_filtro)
        rows = [r for r in rows if f_norm in normalize_text(r.get("sitio", ""))]

    df = pd.DataFrame(rows)'''

replacement_grafico = '''    modo_presentacion = bool(body.get("modo_presentacion"))
    session_id = body.get("session_id")
    datos_aislados = body.get("datos_aislados")

    if modo_presentacion:
        # AISLAMIENTO ESTRICTO: Operar exclusivamente con datos en memoria
        registros_sesion = []
        if session_id and session_id in SESIONES_PRESENTACION:
            registros_sesion = SESIONES_PRESENTACION[session_id]["records"]
        elif isinstance(datos_aislados, list):
            registros_sesion = datos_aislados

        if not registros_sesion:
            raise HTTPException(
                status_code=400,
                detail="No se encontraron datos en la sesión aislada del Modo Presentación."
            )

        df = pd.DataFrame(registros_sesion)
        if "fecha" in df.columns:
            if fecha_inicio:
                df = df[df["fecha"] >= fecha_inicio]
            if fecha_fin:
                df = df[df["fecha"] <= fecha_fin]
        if tipo_operacion and "tipo_operacion" in df.columns and str(tipo_operacion).upper() in ["ENTRADAS", "SALIDAS"]:
            df = df[df["tipo_operacion"].astype(str).str.upper() == str(tipo_operacion).upper()]
        if sitio_filtro and sitio_filtro.strip() and sitio_filtro.lower() not in ["todos", "todos los sitios", "todas"]:
            f_norm = normalize_text(sitio_filtro)
            if "sitio" in df.columns:
                df = df[df["sitio"].astype(str).apply(normalize_text).str.contains(f_norm, na=False)]
    else:
        # 1. Obtener datos de Supabase en Modo Normal
        global ingestor
        if ingestor is None:
            try:
                ingestor = SupabaseIngestor()
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Error conectando a Supabase: {str(e)}")

        query = ingestor.client.table("registros_residuos").select("fecha,sitio,tipo_residuo,tipo_operacion,kg,pz,tarimas")
        if fecha_inicio:
            query = query.gte("fecha", fecha_inicio)
        if fecha_fin:
            query = query.lte("fecha", fecha_fin)
        if tipo_operacion and str(tipo_operacion).upper() in ["ENTRADAS", "SALIDAS"]:
            query = query.eq("tipo_operacion", str(tipo_operacion).upper())

        # Descargar datos paginados (hasta 3000 registros para velocidad de análisis)
        rows = []
        page = 0
        step = 1000
        while page < 3:
            res = query.range(page * step, (page + 1) * step - 1).execute()
            data = res.data or []
            rows.extend(data)
            if len(data) < step:
                break
            page += 1

        # Filtro de sitio en memoria
        if sitio_filtro and sitio_filtro.strip() and sitio_filtro.lower() not in ["todos", "todos los sitios", "todas"]:
            f_norm = normalize_text(sitio_filtro)
            rows = [r for r in rows if f_norm in normalize_text(r.get("sitio", ""))]

        df = pd.DataFrame(rows)'''

# Ensure uuid is imported
if "import uuid" not in code:
    code = code.replace("import uvicorn", "import uvicorn\nimport uuid")

assert target_metricas in code, "target_metricas not found!"
assert target_asistente in code, "target_asistente not found!"
assert target_grafico in code, "target_grafico not found!"

code = code.replace(target_metricas, replacement_metricas, 1)
code = code.replace(target_asistente, replacement_asistente, 1)
code = code.replace(target_grafico, replacement_grafico, 1)

with open(app_path, "w", encoding="utf-8") as f:
    f.write(code)

print("app.py successfully updated with presentation mode endpoints and logic!")
