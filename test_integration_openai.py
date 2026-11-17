#!/usr/bin/env python3
"""
Test de Integración Real con OpenAI (Plastic Trade)
==================================================
Ejecuta 1 sola llamada real a OpenAI con timeout estricto de 5.0s
para validar conectividad y credenciales sin saturar cuotas.
"""

import os
import time
import unittest
from dotenv import load_dotenv
import openai

class TestIntegracionOpenAIReal(unittest.TestCase):
    def test_llamada_real_asistente(self):
        load_dotenv()
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            self.skipTest("OPENAI_API_KEY no configurada. Saltando test de integración en vivo.")

        client = openai.OpenAI(api_key=api_key, timeout=5.0)
        t0 = time.perf_counter()
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "user", "content": "Di únicamente 'OK'"}
            ],
            max_tokens=10,
            temperature=0.0
        )
        duration = time.perf_counter() - t0
        content = response.choices[0].message.content.strip()
        print(f"\n[INTEGRACIÓN REAL OPENAI] Conexión exitosa en {duration:.2f}s. Respuesta: '{content}'")
        self.assertTrue(len(content) > 0)
        self.assertLess(duration, 5.0, "La llamada a OpenAI tardó más de 5 segundos.")

if __name__ == "__main__":
    unittest.main(verbosity=2)
