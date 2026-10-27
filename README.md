# Desempleo y delincuencia en Colombia – análisis espacial

App de Streamlit + pipeline de datos que descarga y procesa **fuentes oficiales**:

- **Policía Nacional**: registros de delitos a nivel de registro (2020-2026).
- **DANE**: tasa de desocupación departamental anual (anexos GEIH).
- **Geometría** departamental desde servicio ArcGIS público.

El flujo construye un **panel anual departamento × año**, calcula **Moran I** global y estima un modelo de panel exploratorio con efectos fijos (no causal).

---

## Archivos

| Archivo | Descripción |
|---------|-------------|
| `app.py` | Aplicación Streamlit |
| `data_pipeline.py` | Descarga, limpieza, unión, Moran y modelo |
| `requirements.txt` | Dependencias (debe estar en la raíz del repo) |

---

## Despliegue en Streamlit Community Cloud

1. Crea un repositorio en GitHub y sube **en la raíz**:
   - `app.py`
   - `data_pipeline.py`
   - `requirements.txt`
   - (opcional) este `README.md`

2. Entra a [share.streamlit.io](https://share.streamlit.io) → **New app** → selecciona el repo y el archivo `app.py`.

3. Si aparece el error *“Faltan dependencias: geopandas, libpysal, …”*:
   - Verifica que `requirements.txt` esté en la **raíz** del mismo repo que contiene `app.py`.
   - Ve a **Manage app → Reboot** (no instales paquetes dentro de `app.py`).

4. La primera ejecución tarda varios minutos (los Excel de Policía pesan 30-40 MB cada uno). Por defecto se procesan solo los 2 años más recientes de delitos para no saturar el contenedor.

---

## Uso local / Google Colab

```bash
pip install -r requirements.txt
streamlit run app.py
```

O desde un notebook:

```python
from data_pipeline import run_pipeline
res = run_pipeline(max_anios_delitos=2, anios_dane=[2022, 2023, 2024])
print(res["panel"].head())
```

---

## Correcciones principales respecto al notebook original

1. **Excel de Policía**: el encabezado real está en la **fila 11** (no en la 0). Se lee con `header=11` y se detecta la hoja `"DELITOS A NIVEL DE REGISTRO"`.
2. **Enlaces directos** de respaldo para Policía y DANE (evita depender solo del scraper de la página).
3. **DANE**: User-Agent de navegador + extracción de la hoja *Departamentos anual* (bloques por departamento y fila “Tasa de Desocupación (TD)”).
4. **ZIP 2022/2023**: se omiten por defecto (requieren descompresión); se priorizan los `.xlsx` recientes.
5. **Streamlit**: `requirements.txt` en la raíz; sin `pip install` dentro del código.
6. No se inventan tasas ni se interpolan valores faltantes.

---

## Límites (importante)

- Los delitos son **registros reportados** por la Policía, no la totalidad de hechos ocurridos.
- La tasa departamental del DANE es **anual**. No debe convertirse en trimestral por interpolación.
- El modelo de panel es **asociación contemporánea con efectos fijos**, no un VAR espacial ni identificación causal.
- Moran detecta autocorrelación espacial global, no causalidad.
- Siempre revisa los logs y la auditoría antes de usar los resultados en un informe académico.

---

## Fuentes oficiales

- [Policía Nacional – Información de delitos a nivel de registro año 2020-2026](https://www.policia.gov.co/contenido/informacion-delitos-nivel-registro-ano-2020-2026)
- [DANE – Mercado laboral por departamentos (históricos)](https://www.dane.gov.co/index.php/estadisticas-por-tema/mercado-laboral/mercado-laboral-por-departamentos/mercado-laboral-por-departamento-historicos)
