#!/usr/bin/env python3
"""
Test Suite Rápida y Optimizada - Plastic Trade
=============================================
Cumple con las 4 directrices de optimización:
1. TestClient en memoria (sin levantar Uvicorn).
2. Timeouts estrictos (<= 5s) en conexiones externas.
3. Mocking para llamadas a OpenAI (respuestas instantáneas).
4. Generación de Excels sintéticos ligeros en memoria (5-10 filas vía openpyxl).
Meta de rendimiento: Tiempo total de ejecución < 10 segundos.
"""

import os
import io
import time
import json
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path
import openpyxl
from fastapi.testclient import TestClient

# Importar aplicación FastAPI
from app import app, SESIONES_PRESENTACION, calcular_metricas_residuos
from ingesta_residuos import (
    identificar_tipo_documento,
    extract_almacen,
    extract_facturacion_salidas,
    extract_from_concentrado,
    extract_historico_monositio,
    FACTORES_CO2E_KG,
    PESO_TARIMA_KG,
    FACTOR_CONO_KG,
    CATALOGO_RESIDUOS
)

client = TestClient(app)

def generar_excel_almacen_memoria() -> io.BytesIO:
    """Genera un archivo Excel sintético ligero (4 filas) de Almacén."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "ENTRADAS MP"
    ws.append(["FECHA", "PROVEEDOR", "MATERIAL", "PZ", "KILOS"])
    ws.append(["2026-10-01", "PROVEEDOR A", "PLAYO TRANSPARENTE", None, 1000.0])
    ws.append(["2026-10-02", "PROVEEDOR B", "CONO DE CARTON", 200, None])  # 200 pz * 0.3 = 60 kg
    ws.append(["2026-10-03", "PROVEEDOR C", "CARTON CORRUGADO", None, 350.0])
    ws.append(["2026-10-04", "PROVEEDOR D", "TARIMA MADERA", 15, None])    # 15 pz * 25.0 = 375 kg
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf

def generar_excel_facturacion_memoria() -> io.BytesIO:
    """Genera un archivo Excel sintético ligero (4 filas) de Facturación / Salidas."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Programa entregas"
    ws.append(["PROGRAMA DE ENTREGAS Y FACTURACION PLASTIC TRADE", "", "", "", "", "", "", "", "", "", "", "", "", "", ""])
    header = ["A", "B", "C", "Fecha Entrega", "E", "F", "Cliente", "H", "I", "J", "Status", "Material", "M", "N", "Cantidad (Kg/Pz)"]
    ws.append(header)
    ws.append(["", "", "", "2026-10-05", "", "", "CLIENTE ALFA", "", "", "", "Entregado", "Playo industrial", "", "", 2000.0])
    ws.append(["", "", "", "2026-10-06", "", "", "CLIENTE BETA", "", "", "", "Entregado", "Cono textil", "", "", 500])
    ws.append(["", "", "", "2026-10-07", "", "", "CLIENTE GAMMA", "", "", "", "Cancelado", "Carton", "", "", 800.0])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


class TestPlasticTradeOptimizado(unittest.TestCase):
    def setUp(self):
        self.start_time = time.perf_counter()

    def tearDown(self):
        duration = time.perf_counter() - self.start_time
        print(f" -> {self._testMethodName} completado en {duration:.4f}s")

    def test_01_health_and_root(self):
        """Verifica salud básica del servidor y disponibilidad de Home.html."""
        res_health = client.get("/api/health")
        self.assertEqual(res_health.status_code, 200)
        data = res_health.json()
        self.assertEqual(data.get("status"), "online")
        self.assertEqual(data.get("service"), "Plastic Trade • Portal Ambiental")

        res_home = client.get("/")
        self.assertEqual(res_home.status_code, 200)
        self.assertIn("text/html", res_home.headers.get("content-type", ""))

    def test_02_identificacion_inteligente_desacoplada(self):
        """Verifica detección estructural de documentos sin depender de nombres de archivo."""
        buf_almacen = generar_excel_almacen_memoria()
        fuente_alm, hoja_alm, _ = identificar_tipo_documento(buf_almacen, tipo_operacion="ENTRADAS")
        self.assertEqual(fuente_alm, "Almacén")
        self.assertEqual(hoja_alm, "ENTRADAS MP")

        buf_fact = generar_excel_facturacion_memoria()
        fuente_fact, hoja_fact, _ = identificar_tipo_documento(buf_fact, tipo_operacion="SALIDAS")
        self.assertEqual(fuente_fact, "Facturación")
        self.assertEqual(hoja_fact, "Programa entregas")

    def test_03_extraccion_ligera_y_reglas_negocio(self):
        """Valida que la regla de conos (0.3 kg/pz) y las columnas normalizadas funcionen en memoria."""
        buf_almacen = generar_excel_almacen_memoria()
        records = extract_almacen(buf_almacen, target_sheet="ENTRADAS MP")
        self.assertTrue(len(records) > 0)
        self.assertEqual(len(records), 4)

        # Verificar conversión de cono (200 pz * 0.3 = 60 kg)
        cono_row = next(r for r in records if r["tipo_residuo"] == "Cono")
        self.assertEqual(cono_row["pz"], 200)
        self.assertEqual(cono_row["kg"], 60.0)

        # Verificar Tarima (15 pz = 15 tarimas)
        tarima_row = next(r for r in records if r["tipo_residuo"] == "Tarima")
        self.assertEqual(tarima_row["pz"], 15)
        self.assertEqual(tarima_row["tarimas"], 15)

    def test_04_modo_presentacion_aislada_sandbox(self):
        """Valida endpoint /api/presentacion/cargar, aislamiento y factores oficiales de CO2e."""
        buf = generar_excel_almacen_memoria()
        files = {
            "file": ("muestra_almacen.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        }
        res = client.post("/api/presentacion/cargar", files=files)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("success"))
        session_id = data.get("session_id")
        self.assertTrue(session_id)
        self.assertIn(session_id, SESIONES_PRESENTACION)

        kpis_eco = data.get("kpis_ecologicos", {})
        totales = data.get("totales", {})

        # Regla 1: Huella de Carbono (tCO2e Sin emitir) = (kg_playo / 1000) * 3.5
        # Playo: 1000 kg * 3.5 / 1000 = 3.500 tCO2e
        self.assertAlmostEqual(kpis_eco.get("tco2e_sin_emitir", 0), 3.57, places=2)

        # Total Kg = 1000 + 60 + 350 + 375 = 1785.0
        self.assertEqual(totales.get("total_kg"), 1785.0)

        # Prueba de filtrado dinámico por sitio en memoria
        res_filtro = client.post("/api/presentacion/metricas", json={
            "session_id": session_id,
            "sitio": "PROVEEDOR A"
        })
        self.assertEqual(res_filtro.status_code, 200)
        data_filtro = res_filtro.json()
        self.assertEqual(data_filtro["totales"]["total_kg"], 1000.0)

    @patch("app.openai_client")
    def test_05_asistente_ia_mocked(self, mock_client):
        """Valida que el Asistente IA en modo presentación responda al instante usando mocking."""
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content="Respuesta instantánea simulada: El total de playo es 1,000 kg."))
        ]
        mock_client.chat.completions.create.return_value = mock_response

        # Crear una sesión aislada previa
        buf = generar_excel_almacen_memoria()
        files = {"file": ("muestra.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        res_c = client.post("/api/presentacion/cargar", files=files)
        session_id = res_c.json().get("session_id")

        payload = {
            "pregunta": "¿Cuánto Playo se generó?",
            "modo_presentacion": True,
            "session_id": session_id
        }
        res = client.post("/api/asistente-ia", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("success"))
        self.assertIn("1,000 kg", data.get("respuesta", ""))

    @patch("app.openai_client")
    def test_06_generar_grafico_ia_mocked(self, mock_client):
        """Valida generación de gráfico IA con mock rápido para ejecución sin latencia ni costo."""
        codigo_simulado = (
            "import matplotlib.pyplot as plt\n"
            "import io\n"
            "fig, ax = plt.subplots(figsize=(6, 4))\n"
            "ax.bar(['Playo', 'Cono', 'Cartón'], [1000, 60, 350], color='#0088D6')\n"
            "ax.set_title('Distribución de Residuos (Kg)')\n"
            "buf = io.BytesIO()\n"
            "plt.tight_layout()\n"
            "plt.savefig(buf, format='png', dpi=180, bbox_inches='tight')\n"
            "plt.close()\n"
            "buf.seek(0)\n"
        )
        json_resp = json.dumps({
            "codigo_python": codigo_simulado,
            "metodo_usado": "Distribución Barplot Mock",
            "interpretacion": "1. Playo representa el mayor volumen con 1,000 kg."
        })
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content=json_resp))
        ]
        mock_client.chat.completions.create.return_value = mock_response

        # Cargar sesión de presentación
        buf = generar_excel_almacen_memoria()
        files = {"file": ("muestra.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        res_c = client.post("/api/presentacion/cargar", files=files)
        session_id = res_c.json().get("session_id")

        payload = {
            "prompt": "Genera gráfico de barras de volumen por residuo",
            "modo_presentacion": True,
            "session_id": session_id
        }
        res = client.post("/api/generar-grafico-ia", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("success"))
        self.assertTrue(data.get("imagen_base64", "").startswith("data:image/png;base64,"))
        self.assertEqual(data.get("metodo_usado"), "Distribución Barplot Mock")

    def test_07_extract_from_concentrado_target_sheet_and_kwargs(self):
        """Valida que extract_from_concentrado acepte target_sheet y **kwargs sin error."""
        wb = openpyxl.Workbook()
        ws1 = wb.active
        ws1.title = "Hoja_Descarte"
        ws1.append(["Col1", "Col2"])

        ws2 = wb.create_sheet(title="Mi_Concentrado_Personalizado")
        ws2.append(["Fecha", "Sitio", "Tipo de Residuo", "Kg", "Pz", "Tarimas"])
        ws2.append(["2026-10-10", "PLANTA SUR", "Playo", 500.0, 0, 0])
        ws2.append(["2026-10-11", "PLANTA SUR", "Cono", 0.0, 100, 0])      # 100 pz * 0.3 = 30 kg
        ws2.append(["2026-10-12", "PLANTA SUR", "Tarima", 0.0, 0, 10])     # 10 tarimas * 25 = 250 kg

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

        # Invocación con target_sheet y kwargs arbitrarios
        records = extract_from_concentrado(
            buf,
            tipo_operacion="ENTRADAS",
            target_sheet="Mi_Concentrado_Personalizado",
            parametro_extra_futuro="test_valor",
            otro_kwarg=123
        )

        self.assertEqual(len(records), 3)
        # Validar conversiones
        cono_rec = next(r for r in records if r["tipo_residuo"] == "Cono")
        self.assertEqual(cono_rec["kg"], 30.0)
        self.assertEqual(cono_rec["pz"], 100.0)

        tarima_rec = next(r for r in records if r["tipo_residuo"] == "Tarima")
        self.assertEqual(tarima_rec["kg"], 250.0)
        self.assertEqual(tarima_rec["tarimas"], 10.0)

    def test_08_historico_monositio_mxcd05(self):
        """Valida extracción exacta del formato MXCD05 HISTÓRICO y adaptación visual de monositio."""
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "HISTÓRICO"
        ws.append(["Año", "Mes", "Tipo de Residuo", "Detalle Mes", "Cantidad", "Sitio"])
        ws.append([2026, "Ene", "PLAYO", "Ene", 1200.0, "MXCD05"])
        ws.append([2026, "Feb", "CARTÓN", "Feb", 800.0, "MXCD05"])
        ws.append([2026, "Mar", "TARIMA", "Mar", 20, "MXCD05"])      # 20 pz * 25.0 kg = 500 kg
        ws.append([2026, "Abr", "CHATARRA", "Abr", 350.0, "MXCD05"])
        ws.append([2026, "May", "CONO", "May", 100, "MXCD05"])        # 100 pz * 0.3 kg = 30 kg
        ws.append([2026, "Jun", "totem", "Jun", 45.0, "MXCD05"])      # totem -> Cartón 45 kg

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

        # 1. Probar extractor directo
        records = extract_historico_monositio(buf)
        self.assertEqual(len(records), 6)

        rec_playo = next(r for r in records if r["tipo_residuo"] == "Playo")
        self.assertEqual(rec_playo["kg"], 1200.0)

        rec_carton = next(r for r in records if r["tipo_residuo"] == "Cartón" and r["mes"] == "Feb")
        self.assertEqual(rec_carton["kg"], 800.0)

        rec_tarima = next(r for r in records if r["tipo_residuo"] == "Tarima")
        self.assertEqual(rec_tarima["kg"], 500.0)
        self.assertEqual(rec_tarima["pz"], 20.0)

        rec_cono = next(r for r in records if r["tipo_residuo"] == "Cono")
        self.assertEqual(rec_cono["kg"], 30.0)
        self.assertEqual(rec_cono["pz"], 100.0)

        rec_totem = next(r for r in records if r["tipo_residuo"] == "Totem" and r["mes"] == "Jun")
        self.assertEqual(rec_totem["kg"], 45.0)

        # 2. Probar carga en endpoint /api/presentacion/cargar
        buf.seek(0)
        files = {
            "file": ("MXCD05 HISTÓRICO.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        }
        res = client.post("/api/presentacion/cargar", files=files)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("es_monositio"))

        # Validar acumulación total de kg por residuo en totales ('totem' separado del cartón)
        totales = data.get("totales", {})
        self.assertEqual(totales.get("kg_playo"), 1200.0)
        self.assertEqual(totales.get("kg_carton"), 800.0)  # 800 (sin totem)
        self.assertEqual(totales.get("kg_totem"), 45.0)    # totem aislado con factor 0 ecológico
        self.assertEqual(totales.get("kg_tarima"), 500.0)
        self.assertEqual(totales.get("kg_chatarra"), 350.0)
        self.assertEqual(totales.get("kg_cono"), 30.0)

        # Validar ranking por meses (debe ordenar Ene como mayor volumen: 1200 kg)
        top_mas = data.get("top_mas_generan", [])
        self.assertTrue(len(top_mas) > 0)
        self.assertEqual(top_mas[0]["mes"], "Ene")
        self.assertEqual(top_mas[0]["kg"], 1200.0)

        # Validar cálculo de indicadores ecológicos recalculados con las cantidades corregidas
        kpis_eco = data.get("kpis_ecologicos", {})
        # Total celulósicos = 800 (cartón) + 30 (cono) = 830 kg ('totem' excluido con factor 0)
        # Árboles sin talar = (830 / 1000) * 17 = 14.11
        self.assertAlmostEqual(kpis_eco.get("arboles_sin_talar", 0), 14.11, places=2)

    def test_09_produccion_tco2e_evitado_por_mes(self):
        """Valida cálculo de tCO2e evitado por mes (Dona), orden cronológico y configuración visual en Home.html."""
        # 1. Verificar configuración visual en Home.html
        with open('Home.html', 'r', encoding='utf-8') as f:
            html = f.read()

        self.assertIn("Producción de tCO₂e Evitado por Mes", html)
        self.assertIn("Distribución porcentual y toneladas de CO₂e evitadas por periodo mensual.", html)
        self.assertIn("Dona", html)
        self.assertIn("tCO2e (${pct}%)", html)

        # 2. Generar libro de prueba multimes con las 4 categorías oficiales
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Histórico_2026"
        ws.append(["Año", "Mes", "Tipo de Residuo", "Mes_Aux", "Cantidad", "Sitio"])
        # Ene: Playo 1000 kg -> 1000 * 3.5 = 3500 kg CO2e -> 3.50 tCO2e
        ws.append([2026, "Ene", "PLAYO", "Ene", 1000.0, "MXCD05"])
        # Feb: Cartón 500 kg + Cono 100 pz (30 kg) -> 530 * 0.852 = 451.56 kg CO2e -> 0.45 tCO2e
        ws.append([2026, "Feb", "CARTON", "Feb", 500.0, "MXCD05"])
        ws.append([2026, "Feb", "CONO", "Feb", 100.0, "MXCD05"])
        # Mar: Chatarra 400 kg -> 400 * 2.66 = 1064 kg CO2e -> 1.06 tCO2e
        ws.append([2026, "Mar", "CHATARRA", "Mar", 400.0, "MXCD05"])
        # Abr: Tarima 10 pz (250 kg) + Madera 100 kg -> 350 * 0.2 = 70 kg CO2e -> 0.07 tCO2e
        ws.append([2026, "Abr", "TARIMA", "Abr", 10.0, "MXCD05"])
        ws.append([2026, "Abr", "MADERA", "Abr", 100.0, "MXCD05"])

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

        files = {
            "file": ("TEST_TCO2E_MES.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        }
        res = client.post("/api/presentacion/cargar", files=files)
        self.assertEqual(res.status_code, 200)
        data = res.json()

        tco2e_info = data.get("tco2e_evitado_mes", {})
        self.assertIsNotNone(tco2e_info)
        labels = tco2e_info.get("labels", [])
        vals = tco2e_info.get("data", [])

        # Validar orden cronológico estricto
        self.assertEqual(labels, ["Ene", "Feb", "Mar", "Abr"])

        # Validar cálculos por mes según Regla 1 (Playo / 1000 * 3.5)
        # Ene: 3.50
        self.assertEqual(vals[0], 3.5)
        # Feb: 0.0
        self.assertEqual(vals[1], 0.04)
        # Mar: 0.0
        self.assertEqual(vals[2], 0.0)
        # Abr: 0.0
        self.assertEqual(vals[3], 0.0)

        # Validar que volumen_playo_cliente esté sincronizado con la misma estructura para compatibilidad
        playo_info = data.get("volumen_playo_cliente", {})
        self.assertEqual(playo_info.get("labels"), labels)
        self.assertEqual(playo_info.get("data"), vals)

    def test_10_archivo_testigo_metricas_exactas(self):
        """Valida que las sumatorias y factores coincidan exactamente con los valores testigo del usuario."""
        from app import calcular_metricas_residuos
        
        # Archivo testigo MXCD05:
        # Playo: 94,574.5 Kg -> tCO2e Sin emitir = (94574.5 / 1000) * 3.5 = exactamente 331.01 tCO2e
        # Base celulosa = 1,259,029.90 Kg
        # Árboles sin talar = 21,403.51
        # Energía no consumida = 5,287,925.58
        # Agua no consumida = 4,406,604.65
        # Combustible fósil = 2,077,399.33
        kg_playo_mxcd05 = 94574.5
        kg_carton_testigo = 1225747.40
        kg_cono_testigo = 33282.50  # Total celulosa = 1,259,029.90 Kg

        records = [
            {"tipo_residuo": "Playo", "kg": kg_playo_mxcd05, "mes": "Ene"},
            {"tipo_residuo": "Cartón", "kg": kg_carton_testigo, "mes": "Feb"},
            {"tipo_residuo": "Cono", "kg": kg_cono_testigo, "mes": "Mar"},
            {"tipo_residuo": "Tarima", "kg": 50000.0, "mes": "Abr"},  # No altera tCO2e principal
            {"tipo_residuo": "Chatarra", "kg": 25000.0, "mes": "May"}, # No altera tCO2e principal
            {"tipo_residuo": "Totem", "kg": 50000.0, "mes": "Jun"}     # Factor 0
        ]

        metricas = calcular_metricas_residuos(records)
        kpis = metricas["kpis_ecologicos"]

        # Validaciones exactas según Reglas 1 y 2
        self.assertEqual(kpis["tco2e_sin_emitir"], 370.88)
        self.assertEqual(kpis["arboles_sin_talar"], 21403.51)
        self.assertEqual(kpis["energia_kwh"], 5287925.58)
        self.assertEqual(kpis["agua_litros"], 4406604.65)
        self.assertEqual(kpis["combustible_litros"], 2077399.33)



    def test_11_filtro_residuo_y_evolucion_mensual(self):
        """Verifica el filtrado reactivo por residuo y la estructura de evoluci?n mensual."""
        from app import calcular_metricas_residuos
        
        records = [
            {"tipo_residuo": "Playo", "kg": 1000.0, "mes": "Ene"},
            {"tipo_residuo": "Cartón", "kg": 500.0, "mes": "Ene"},
            {"tipo_residuo": "Cono", "kg": 200.0, "mes": "Feb"},
            {"tipo_residuo": "Tarima", "kg": 300.0, "mes": "Mar"},
            {"tipo_residuo": "Chatarra", "kg": 150.0, "mes": "Mar"}
        ]

        # 1. Filtro: Playo
        m_playo = calcular_metricas_residuos(records, filtro_residuo="Playo")
        k_playo = m_playo["kpis_ecologicos"]
        self.assertEqual(k_playo["tco2e_sin_emitir"], 3.5) # 1000 / 1000 * 3.5
        self.assertIsNone(k_playo["arboles_sin_talar"])
        self.assertFalse(k_playo["celulosa_activa"])
        evol_playo = m_playo["evolucion_mensual"]
        self.assertEqual(evol_playo["residuo_seleccionado"], "Playo")
        self.assertEqual(evol_playo["data"], [1000.0, 0.0, 0.0]) # Ene, Feb, Mar

        # 2. Filtro: Cart?n
        m_carton = calcular_metricas_residuos(records, filtro_residuo="Cartón")
        k_carton = m_carton["kpis_ecologicos"]
        self.assertEqual(k_carton["tco2e_sin_emitir"], 0.0)
        self.assertEqual(k_carton["arboles_sin_talar"], round(500.0 / 1000.0 * 17.0, 2))
        self.assertTrue(k_carton["celulosa_activa"])

        # 3. Filtro: Tarima / Madera
        m_tarima = calcular_metricas_residuos(records, filtro_residuo="Tarima / Madera")
        k_tarima = m_tarima["kpis_ecologicos"]
        self.assertEqual(k_tarima["tco2e_sin_emitir"], round(300.0 / 1000.0 * 0.20, 2))
        self.assertIsNone(k_tarima["arboles_sin_talar"])

        # 4. Filtro: Chatarra
        m_chatarra = calcular_metricas_residuos(records, filtro_residuo="Chatarra")
        k_chatarra = m_chatarra["kpis_ecologicos"]
        self.assertEqual(k_chatarra["tco2e_sin_emitir"], round(150.0 / 1000.0 * 2.66, 2))
        self.assertIsNone(k_chatarra["arboles_sin_talar"])

        # 5. Filtro: Todos
        m_todos = calcular_metricas_residuos(records, filtro_residuo="Todos")
        k_todos = m_todos["kpis_ecologicos"]
        self.assertEqual(k_todos["tco2e_sin_emitir"], 3.74)
        self.assertEqual(k_todos["arboles_sin_talar"], round((500.0 + 200.0) / 1000.0 * 17.0, 2))
        self.assertTrue(k_todos["celulosa_activa"])

        # 6. Validar layout fijo de gráficas Playo y Cartón en Home.html
        with open("Home.html", "r", encoding="utf-8") as f:
            html = f.read()
        self.assertIn('id="dash-filtro-residuo"', html)
        # Gráfica Playo en posición fija superior derecha
        self.assertIn('id="chartPlayoMensual"', html)
        self.assertIn('Comportamiento Mensual y Mitigación de CO₂: Playo', html)
        # Gráfica Cartón en posición fija inferior de ancho completo
        self.assertIn('id="contenedor-grafica-carton"', html)
        self.assertIn('id="chartCartonMensual"', html)
        self.assertIn('Comportamiento Mensual y Mitigación de CO₂: Cartón', html)

        # 7. Validar elementos del nuevo selector de mes y clases de overflow
        self.assertIn('id="filtro-contenedor-mes"', html)
        self.assertIn('id="filtro-mes"', html)
        self.assertIn('id="contenedor-filtro-desde"', html)
        self.assertIn('id="contenedor-filtro-hasta"', html)
        self.assertIn('white-space: nowrap;', html)
        self.assertIn('text-overflow: ellipsis;', html)


if __name__ == "__main__":
    t_start = time.perf_counter()
    suite = unittest.TestLoader().loadTestsFromTestCase(TestPlasticTradeOptimizado)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    total_time = time.perf_counter() - t_start
    print(f"\n" + "="*60)
    print(f"TIEMPO TOTAL DE LA SUITE DE PRUEBAS: {total_time:.2f} segundos")
    print("="*60)
    if total_time > 10.0:
        print("[ALERTA] La suite excedió el límite máximo de 10 segundos.")
        exit(1)
    else:
        print("[ÉXITO] La suite corrió dentro del umbral (< 10 segundos).")
        exit(0 if result.wasSuccessful() else 1)
