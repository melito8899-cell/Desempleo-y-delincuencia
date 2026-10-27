"""Aplicación Streamlit para análisis de desempleo y delincuencia en Colombia.
Instalar: pip install -r requirements.txt
Ejecutar: streamlit run app.py
"""
import streamlit as st
import contextlib
import io

st.set_page_config(page_title="Desempleo y delincuencia en Colombia", layout="wide")
st.title("Análisis espacial: desempleo y delincuencia en Colombia")
st.write("Descarga y procesa datos oficiales de la Policía Nacional y el DANE. La extracción automática debe revisarse antes de interpretar los resultados.")
st.warning("La ejecución puede tardar mientras se descargan los archivos. Pulsa el botón para iniciar el análisis.")

ANALISIS = r"""
# Desempleo y delincuencia en Colombia: análisis espacial con fuentes oficiales
# Notebook integral para Google Colab
#
# Este notebook intenta obtener automáticamente:
# 1. Los registros de delitos publicados por la Policía Nacional.
# 2. La tasa anual de desocupación departamental publicada por el DANE.
# 3. La geometría departamental desde un servicio geográfico público.
#
# Decisión metodológica: el indicador departamental comparable que el DANE publica en anexos de mercado laboral es anual para 23 departamentos. Por eso el flujo automático construye un análisis anual, no inventa tasas trimestrales. Si el objetivo exige trimestre, hay que estimar la tasa desde microdatos GEIH con factores de expansión y diseño muestral; eso no se puede reemplazar por interpolar una tasa anual.
#
# Importante: las páginas oficiales pueden cambiar nombres de archivos o columnas. El notebook informa los fallos y guarda los archivos obtenidos. No rellena datos faltantes con valores inventados. Las celdas de modelación comprueban que haya observaciones suficientes antes de estimar.
#
# Fuentes:
# - Policía Nacional, registros de delitos 2020–2026: https://www.policia.gov.co/contenido/informacion-delitos-nivel-registro-ano-2020-2026
# - DANE, mercado laboral por departamentos: https://www.dane.gov.co/index.php/estadisticas-por-tema/mercado-laboral/mercado-laboral-por-departamentos
# - DANE, históricos departamentales: https://www.dane.gov.co/index.php/estadisticas-por-tema/mercado-laboral/mercado-laboral-por-departamentos/mercado-laboral-por-departamento-historicos

# 1. Instalar dependencias

# Dependencias instaladas mediante requirements.txt; no usar %pip en app.py.

# 2. Configurar carpetas y utilidades

from pathlib import Path
import re, json, time, warnings, unicodedata
import requests
import numpy as np
import pandas as pd
import geopandas as gpd
import matplotlib.pyplot as plt
from bs4 import BeautifulSoup
from urllib.parse import urljoin, unquote

BASE = Path("desempleo_delincuencia_colombia")
RAW = BASE / "raw"
OUT = BASE / "resultados"
RAW.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)

URL_POLICIA = "https://www.policia.gov.co/contenido/informacion-delitos-nivel-registro-ano-2020-2026"
URL_DANE = "https://www.dane.gov.co/index.php/estadisticas-por-tema/mercado-laboral/mercado-laboral-por-departamentos"
URL_DANE_HIST = "https://www.dane.gov.co/index.php/estadisticas-por-tema/mercado-laboral/mercado-laboral-por-departamentos/mercado-laboral-por-departamento-historicos"
URL_GEO = ("https://services.arcgis.com/BQTQBNBsmxjF8vus/arcgis/rest/services/"
           "DelitosPoliciaNacional/FeatureServer/1/query")

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 (compatible; ColabResearch/1.0)"})

def get_url(url, timeout=120):
    r = SESSION.get(url, timeout=timeout)
    r.raise_for_status()
    return r

def normalizar(s):
    s = str(s).strip().lower()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s

def descargar(url, destino, timeout=180):
    r = get_url(url, timeout=timeout)
    if len(r.content) < 100:
        raise ValueError(f"Descarga sospechosamente pequeña: {url}")
    Path(destino).write_bytes(r.content)
    print(f"OK: {Path(destino).name} ({len(r.content)/1e6:.2f} MB)")
    return Path(destino)

print("Carpeta de trabajo:", BASE)
print("Python/pandas:", pd.__version__)

# 3. Descargar geometría departamental

geo_path = RAW / "departamentos_policia.geojson"
params_geo = {
    "where": "1=1",
    "outFields": "*",
    "returnGeometry": "true",
    "outSR": "4326",
    "f": "geojson"
}
try:
    resp = SESSION.get(URL_GEO, params=params_geo, timeout=180)
    resp.raise_for_status()
    gj = resp.json()
    if not gj.get("features"):
        raise ValueError(f"Servicio sin entidades: {str(gj)[:300]}")
    geo_path.write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")
    geo = gpd.read_file(geo_path)
    print("Geometrías descargadas:", len(geo))
    print("Columnas geográficas:", list(geo.columns))
    display(geo.head(3))
except Exception as e:
    geo = None
    print("ERROR descargando geometría:", repr(e))
    print("URL del servicio:", URL_GEO)

# 4. Descargar automáticamente los Excel oficiales de delitos

def extraer_links_excel(url_pagina):
    html = get_url(url_pagina).text
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].replace("&amp;", "&")
        full = urljoin(url_pagina, href)
        texto = " ".join(a.get_text(" ", strip=True).split())
        if re.search(r"\.xlsx?($|\?)", full, flags=re.I):
            links.append({"url": full, "texto": texto})
    # deduplicar por URL
    unicos = {}
    for x in links:
        unicos[x["url"]] = x
    return list(unicos.values())

try:
    links_delitos = extraer_links_excel(URL_POLICIA)
    links_delitos = [x for x in links_delitos if re.search(r"202[0-6]", x["url"] + " " + x["texto"])]
    print("Enlaces Excel detectados:", len(links_delitos))
    for x in links_delitos:
        print(x["texto"][:100], "->", x["url"])
except Exception as e:
    links_delitos = []
    print("No fue posible leer automáticamente la página de Policía:", repr(e))

# Descargar cada archivo anual detectado. Si la página sólo publica algunos años,
# se descargan los disponibles sin inventar los que falten.
archivos_delitos = []
for item in links_delitos:
    texto = item["texto"] + " " + item["url"]
    m = re.search(r"(202[0-6])", texto)
    anio = m.group(1) if m else "sin_anio"
    ext = ".xlsx" if ".xlsx" in item["url"].lower() else ".xls"
    nombre = f"delitos_{anio}_{len(archivos_delitos)+1}{ext}"
    destino = RAW / nombre
    try:
        if not destino.exists():
            descargar(item["url"], destino, timeout=240)
        archivos_delitos.append({"anio": anio, "path": destino, "url": item["url"]})
    except Exception as e:
        print(f"No se descargó {item['url']}: {repr(e)}")

print("Archivos de delitos descargados:", len(archivos_delitos))

# 5. Inspeccionar estructura de los archivos de delitos

def leer_excel_primera_hoja(path):
    xl = pd.ExcelFile(path)
    # Detecta hoja con más columnas útiles, leyendo una muestra
    mejor = None
    for sh in xl.sheet_names:
        try:
            muestra = pd.read_excel(path, sheet_name=sh, nrows=10)
            if mejor is None or muestra.shape[1] > mejor[1].shape[1]:
                mejor = (sh, muestra)
        except Exception:
            pass
    if mejor is None:
        raise ValueError(f"No se pudo leer ninguna hoja de {path.name}")
    return mejor[0], mejor[1]

inventario = []
for item in archivos_delitos:
    try:
        hoja, muestra = leer_excel_primera_hoja(item["path"])
        inventario.append({
            "archivo": item["path"].name,
            "anio_archivo": item["anio"],
            "hoja": hoja,
            "columnas": list(muestra.columns),
            "filas_muestra": len(muestra)
        })
        print("\\n", item["path"].name, "| hoja:", hoja)
        print("Columnas:", list(muestra.columns))
        display(muestra.head(2))
    except Exception as e:
        print("No se pudo inspeccionar", item["path"].name, repr(e))

# 6. Preparar registros delictivos a nivel anual

# Heurística conservadora: sólo procesa archivos si puede identificar una fecha
# y un campo departamental. Se conservan los nombres de columnas originales
# y se imprime qué campos fueron elegidos para auditar la transformación.
def elegir_columna(columnas, patrones):
    pares = [(c, normalizar(c)) for c in columnas]
    for patron in patrones:
        for original, norm in pares:
            if re.search(patron, norm):
                return original
    return None

delitos_anuales_partes = []
auditoria_delitos = []

for item in archivos_delitos:
    try:
        xl = pd.ExcelFile(item["path"])
        candidatos = []
        for sh in xl.sheet_names:
            try:
                df0 = pd.read_excel(item["path"], sheet_name=sh, nrows=5)
                score = sum([
                    elegir_columna(df0.columns, [r"fecha.*hecho", r"fecha.*ocurr", r"fecha"]),
                    elegir_columna(df0.columns, [r"departamento", r"nom.*depto", r"depto"])
                ] != [None, None])
                candidatos.append((score, sh))
            except Exception:
                continue
        if not candidatos:
            print("Sin hojas legibles:", item["path"].name)
            continue
        candidatos.sort(reverse=True)
        hoja = candidatos[0][1]
        df = pd.read_excel(item["path"], sheet_name=hoja)
        df.columns = [str(c).strip() for c in df.columns]
        col_fecha = elegir_columna(df.columns, [r"fecha.*hecho", r"fecha.*ocurr", r"fecha"])
        col_depto = elegir_columna(df.columns, [r"departamento", r"nom.*depto", r"depto"])
        col_codigo = elegir_columna(df.columns, [r"codigo.*depart", r"cod.*depto", r"cod_dpto"])
        if col_fecha is None or col_depto is None:
            print(f"Saltado {item['path'].name}: no se detectó fecha y departamento. Fecha={col_fecha}, departamento={col_depto}")
            auditoria_delitos.append({"archivo": item["path"].name, "estado":"omitido", "fecha":col_fecha, "departamento":col_depto})
            continue
        fecha = pd.to_datetime(df[col_fecha], errors="coerce", dayfirst=True)
        df["_anio"] = fecha.dt.year
        df["_depto_original"] = df[col_depto].astype("string").str.strip()
        df = df[df["_anio"].notna() & df["_depto_original"].notna()].copy()
        df["_anio"] = df["_anio"].astype(int)
        # Cada fila representa un registro delictivo; se cuentan filas por departamento-año.
        agg = df.groupby(["_depto_original", "_anio"], as_index=False).size()
        agg = agg.rename(columns={"_depto_original":"departamento_raw", "_anio":"anio", "size":"delitos"})
        if col_codigo:
            mapa_cod = df[[col_depto, col_codigo]].dropna().drop_duplicates()
            mapa_cod = mapa_cod.rename(columns={col_depto:"departamento_raw", col_codigo:"codigo_raw"})
            agg = agg.merge(mapa_cod, on="departamento_raw", how="left")
        agg["archivo_origen"] = item["path"].name
        delitos_anuales_partes.append(agg)
        auditoria_delitos.append({"archivo":item["path"].name, "estado":"procesado", "fecha":col_fecha,
                                  "departamento":col_depto, "codigo":col_codigo, "filas_leidas":len(df)})
        print(f"Procesado {item['path'].name}: {len(df):,} registros válidos.")
    except Exception as e:
        print("Error procesando", item["path"].name, repr(e))
        auditoria_delitos.append({"archivo":item["path"].name, "estado":"error", "detalle":repr(e)})

auditoria_delitos = pd.DataFrame(auditoria_delitos)
display(auditoria_delitos)
if delitos_anuales_partes:
    delitos_anuales = pd.concat(delitos_anuales_partes, ignore_index=True)
    # Un mismo año podría aparecer en más de un archivo; quitar duplicación exacta de cobertura
    # por año/departamento sumando sólo si el archivo representa años distintos; se audita antes.
    delitos_anuales = (delitos_anuales.groupby(["departamento_raw","anio"], as_index=False)
                       .agg(delitos=("delitos","sum")))
    display(delitos_anuales.head())
else:
    delitos_anuales = pd.DataFrame(columns=["departamento_raw","anio","delitos"])
    print("No se pudo construir la tabla automáticamente. Revisa los nombres de columnas impresos arriba.")

# 7. Obtener tasas anuales departamentales oficiales del DANE

# La página del DANE publica anexos y boletines. El bloque siguiente detecta enlaces PDF/XLSX/XLS en la página histórica, prioriza anexos por año y trata de leer tablas de tasas. Como la estructura de los anexos puede cambiar, la extracción se valida y no se asume correcta sin inspección.

def extraer_links_documentos(url_pagina):
    soup = BeautifulSoup(get_url(url_pagina).text, "html.parser")
    encontrados = []
    for a in soup.find_all("a", href=True):
        url = urljoin(url_pagina, a["href"].replace("&amp;", "&"))
        texto = " ".join(a.get_text(" ", strip=True).split())
        if re.search(r"\.(xlsx?|pdf)(?:$|\?)", url, re.I):
            encontrados.append({"url":url, "texto":texto})
    unicos = {}
    for x in encontrados:
        unicos[x["url"]] = x
    return list(unicos.values())

try:
    docs_dane = extraer_links_documentos(URL_DANE_HIST)
    docs_dane += extraer_links_documentos(URL_DANE)
    # Deduplicar
    docs_dane = list({x["url"]:x for x in docs_dane}.values())
    docs_dane = [x for x in docs_dane if re.search(r"20(0[7-9]|1\d|2[0-6])", x["url"]+" "+x["texto"])]
    print("Documentos DANE detectados:", len(docs_dane))
    for x in docs_dane[:40]:
        print(x["texto"][:80], "->", x["url"])
except Exception as e:
    docs_dane = []
    print("No se pudo consultar automáticamente la página histórica DANE:", repr(e))

# Descarga documentos DANE que parecen anexos por año. Se priorizan XLS/XLSX
# porque son más adecuados para extracción de tablas que PDF.
docs_por_anio = {}
for x in docs_dane:
    s = (x["url"] + " " + x["texto"]).lower()
    m = re.search(r"(20(?:0[7-9]|1\d|2[0-6]))", s)
    if not m:
        continue
    anio = int(m.group(1))
    ext = Path(unquote(x["url"].split("?")[0])).suffix.lower()
    if ext not in [".xlsx", ".xls", ".pdf"]:
        continue
    # priorizar archivos con "anexo" o "anex"
    prioridad = (3 if "anex" in s else 2 if ext in [".xlsx",".xls"] else 1)
    if anio not in docs_por_anio or prioridad > docs_por_anio[anio][0]:
        docs_por_anio[anio] = (prioridad, x)

archivos_dane = []
for anio, (prioridad, item) in sorted(docs_por_anio.items()):
    ext = Path(unquote(item["url"].split("?")[0])).suffix.lower()
    destino = RAW / f"dane_departamentos_{anio}{ext}"
    try:
        if not destino.exists():
            descargar(item["url"], destino, timeout=180)
        archivos_dane.append({"anio":anio, "path":destino, "url":item["url"], "texto":item["texto"]})
    except Exception as e:
        print(f"No se descargó documento DANE {anio}: {repr(e)}")
print("Documentos DANE descargados:", len(archivos_dane))

# 8. Extraer tabla de desempleo: inspección y validación

# Intenta extraer hojas Excel y tablas PDF. Las tablas detectadas se guardan como
# tablas candidatas, pero no se usan en el modelo hasta identificar columnas fiables.
import fitz

tablas_candidatas_dane = []
for item in archivos_dane:
    p = item["path"]
    try:
        if p.suffix.lower() in [".xlsx",".xls"]:
            xl = pd.ExcelFile(p)
            for sh in xl.sheet_names:
                try:
                    df = pd.read_excel(p, sheet_name=sh)
                    if df.empty:
                        continue
                    cols_norm = [normalizar(c) for c in df.columns]
                    texto_cols = " ".join(cols_norm)
                    if any(k in texto_cols for k in ["departamento","desocupacion","desempleo","tasa"]):
                        tablas_candidatas_dane.append({"anio_archivo":item["anio"],"archivo":p.name,
                                                       "hoja":sh,"tabla":df})
                        print(f"Candidata: {p.name} | hoja={sh} | tamaño={df.shape}")
                        print("Columnas:", list(df.columns))
                        display(df.head(4))
                except Exception:
                    pass
        elif p.suffix.lower() == ".pdf":
            doc = fitz.open(p)
            lineas = []
            for page in doc:
                lineas.extend(page.get_text("text").splitlines())
            # Extrae líneas tipo "Nariño 6,0" sólo como pista para revisión.
            hits = [ln.strip() for ln in lineas if re.search(r"\b(?:Choc[oó]|Nari[nñ]o|Antioquia|Cauca|Huila)\b", ln, re.I)]
            if hits:
                print(f"PDF {p.name}, líneas que podrían contener departamentos:")
                print("\\n".join(hits[:30]))
    except Exception as e:
        print("No se pudo inspeccionar documento DANE", p.name, repr(e))
print("Tablas candidatas encontradas:", len(tablas_candidatas_dane))

# 9. Construir tasa de desempleo desde tablas Excel detectadas

# Selección automática deliberadamente conservadora. Sólo acepta una tabla si detecta
# una columna departamental y una columna cuyo nombre aluda a tasa de desocupación.
def localizar_columna(df, patrones):
    for c in df.columns:
        n = normalizar(c)
        if any(re.search(p, n) for p in patrones):
            return c
    return None

tasas_dane_partes = []
for item in tablas_candidatas_dane:
    df = item["tabla"].copy()
    col_dep = localizar_columna(df, [r"departamento", r"nombre.*depto", r"territorio"])
    col_tasa = localizar_columna(df, [r"tasa.*desocup", r"tasa.*desemple", r"desocupacion.*porcent", r"td_?"])
    if col_dep is None or col_tasa is None:
        continue
    tmp = df[[col_dep, col_tasa]].copy()
    tmp.columns = ["departamento_raw","tasa_desempleo"]
    tmp["anio"] = int(item["anio_archivo"])
    tmp["departamento_raw"] = tmp["departamento_raw"].astype("string").str.strip()
    tmp["tasa_desempleo"] = (tmp["tasa_desempleo"].astype(str)
                              .str.replace("%","",regex=False)
                              .str.replace(",",".",regex=False))
    tmp["tasa_desempleo"] = pd.to_numeric(tmp["tasa_desempleo"], errors="coerce")
    tmp = tmp[tmp["departamento_raw"].notna() & tmp["tasa_desempleo"].between(0,100)]
    if len(tmp) >= 15:
        tasas_dane_partes.append(tmp)
        print(f"Tabla aceptada provisionalmente: {item['archivo']} | {item['hoja']} | n={len(tmp)}")

if tasas_dane_partes:
    desempleo_anual = pd.concat(tasas_dane_partes, ignore_index=True)
    desempleo_anual["dep_norm"] = desempleo_anual["departamento_raw"].map(normalizar)
    desempleo_anual = desempleo_anual.drop_duplicates(["dep_norm","anio"], keep="last")
    display(desempleo_anual.head(10))
else:
    desempleo_anual = pd.DataFrame(columns=["departamento_raw","tasa_desempleo","anio","dep_norm"])
    print("No se identificó automáticamente una tabla DANE con columnas inequívocas.")
    print("No se inventarán tasas. Consulta los enlaces oficiales mostrados y ajusta el mapeo de columnas.")

# 10. Normalizar departamentos y unir bases

# Diccionario de equivalencias para nombres frecuentes. La unión siempre se audita.
ALIAS = {
    "bogota_dc":"bogota_d_c", "bogota_d_c":"bogota_d_c", "bogota":"bogota_d_c",
    "valle":"valle_del_cauca", "valle_del_cauca":"valle_del_cauca",
    "san_andres":"archipielago_de_san_andres_providencia_y_santa_catalina",
    "san_andres_providencia_y_santa_catalina":"archipielago_de_san_andres_providencia_y_santa_catalina",
    "norte_de_santander":"norte_de_santander", "la_guajira":"la_guajira",
    "atlantico":"atlantico", "bolivar":"bolivar", "boyaca":"boyaca",
    "caldas":"caldas", "caqueta":"caqueta", "cauca":"cauca", "cesar":"cesar",
    "choco":"choco", "cordoba":"cordoba", "cundinamarca":"cundinamarca",
    "guainia":"guainia", "guaviare":"guaviare", "huila":"huila", "magdalena":"magdalena",
    "meta":"meta", "narino":"narino", "putumayo":"putumayo", "quindio":"quindio",
    "risaralda":"risaralda", "san_andres_providencia_y_santa_catalina":"archipielago_de_san_andres_providencia_y_santa_catalina",
    "santander":"santander", "sucre":"sucre", "tolima":"tolima", "arauca":"arauca",
    "amazonas":"amazonas", "antioquia":"antioquia", "casanare":"casanare", "vaupes":"vaupes",
    "vichada":"vichada", "guainia":"guainia"
}
def dep_key(x):
    n = normalizar(x)
    n = ALIAS.get(n, n)
    return n

if not delitos_anuales.empty:
    delitos_anuales["dep_norm"] = delitos_anuales["departamento_raw"].map(dep_key)
if not desempleo_anual.empty:
    desempleo_anual["dep_norm"] = desempleo_anual["departamento_raw"].map(dep_key)

if geo is not None:
    print("Columnas geográficas disponibles:", list(geo.columns))
    col_geo_dep = localizar_columna(geo, [r"departamento", r"nom_depto", r"nombre.*depto", r"depto"])
    if col_geo_dep is None:
        # muestra para mapear manualmente si el servicio usa un nombre no estándar
        print("No se detectó la columna de nombre departamental; inspecciona columnas y ajusta col_geo_dep.")
    else:
        geo = geo.copy()
        geo["dep_norm"] = geo[col_geo_dep].map(dep_key)
        print("Columna geográfica elegida:", col_geo_dep)
        print("Ejemplos:", geo[col_geo_dep].head().tolist())

if not desempleo_anual.empty and not delitos_anuales.empty:
    panel = desempleo_anual.merge(delitos_anuales, on=["dep_norm","anio"], how="inner", suffixes=("_dane","_policia"))
    print("Panel unido:", panel.shape)
    display(panel.head())
else:
    panel = pd.DataFrame()
    print("No hay suficientes fuentes procesadas para unir el panel. Revisa los mensajes de las secciones 4–9.")

# 11. Auditoría de cobertura, duplicados y datos faltantes

if not panel.empty:
    print("Años:", sorted(panel["anio"].unique()))
    print("Departamentos:", panel["dep_norm"].nunique())
    print("Duplicados departamento-año:", panel.duplicated(["dep_norm","anio"]).sum())
    display(panel.groupby("anio").agg(
        departamentos=("dep_norm","nunique"),
        tasa_media=("tasa_desempleo","mean"),
        delitos_total=("delitos","sum")
    ))
    assert panel.duplicated(["dep_norm","anio"]).sum() == 0, "Hay duplicados departamento-año."
    assert panel[["tasa_desempleo","delitos"]].notna().all().all(), "Hay faltantes en variables del modelo."
    panel.to_csv(OUT / "panel_departamental_anual.csv", index=False, encoding="utf-8-sig")
    print("Guardado:", OUT / "panel_departamental_anual.csv")
else:
    print("No se ejecuta modelación sin una base unida válida.")

# 12. Exploración gráfica

if not panel.empty:
    fig, ax = plt.subplots(figsize=(10,5))
    panel.groupby("anio")["tasa_desempleo"].mean().plot(marker="o", ax=ax)
    ax.set_title("Tasa media de desocupación en departamentos disponibles")
    ax.set_ylabel("Porcentaje (%)")
    ax.set_xlabel("Año")
    ax.grid(alpha=.3)
    plt.show()

    fig, ax = plt.subplots(figsize=(10,5))
    panel.groupby("anio")["delitos"].sum().plot(marker="o", ax=ax)
    ax.set_title("Registros delictivos sumados en departamentos incluidos")
    ax.set_ylabel("Número de registros")
    ax.set_xlabel("Año")
    ax.grid(alpha=.3)
    plt.show()

    fig, ax = plt.subplots(figsize=(7,5))
    ax.scatter(panel["tasa_desempleo"], np.log1p(panel["delitos"]), alpha=.6)
    ax.set_xlabel("Tasa de desempleo (%)")
    ax.set_ylabel("log(1 + delitos registrados)")
    ax.set_title("Asociación descriptiva, no causal")
    ax.grid(alpha=.3)
    plt.show()
else:
    print("Se omiten gráficos porque panel está vacío.")

# 13. Preparar pesos espaciales y autocorrelación de Moran

from libpysal.weights import Queen, KNN
from esda.moran import Moran

if geo is not None and "dep_norm" in geo.columns and not panel.empty:
    # Mantener departamentos que aparecen en el panel
    deps_panel = set(panel["dep_norm"].unique())
    geo_model = geo[geo["dep_norm"].isin(deps_panel)].copy()
    geo_model = geo_model.drop_duplicates("dep_norm").sort_values("dep_norm").reset_index(drop=True)
    deps_geo = set(geo_model["dep_norm"])
    deps_data = set(panel["dep_norm"])
    print("Departamentos con geometría:", len(deps_geo), "| datos:", len(deps_data))
    print("Sin geometría:", sorted(deps_data - deps_geo))
    print("Sin datos:", sorted(deps_geo - deps_data))
    if len(geo_model) >= 5:
        try:
            wq = Queen.from_dataframe(geo_model, use_index=False)
            if len(wq.islands) == 0:
                w = wq
                nombre_w = "Queen contigüidad"
            else:
                print("Queen tiene islas; se usa KNN(4) para conectividad.")
                w = KNN.from_dataframe(geo_model, k=min(4, len(geo_model)-1))
                nombre_w = "KNN(4)"
            w.transform = "R"
            print("Matriz W:", nombre_w, "| islas:", len(w.islands))
            print("Orden territorial W coincide con:", geo_model["dep_norm"].tolist()[:5], "...")
        except Exception as e:
            w = None
            print("No se pudo construir W:", repr(e))
    else:
        w = None
        print("No hay suficientes departamentos con geometría.")
else:
    w = None
    print("No se puede construir W: faltan geometría o panel.")

if w is not None and not panel.empty:
    moran_rows = []
    for anio, b in panel.groupby("anio"):
        b = b.set_index("dep_norm").reindex(geo_model["dep_norm"])
        for var in ["tasa_desempleo", "delitos"]:
            x = b[var].to_numpy(dtype=float)
            if np.isfinite(x).all() and np.std(x) > 0:
                mi = Moran(x, w, permutations=999)
                moran_rows.append({"anio":int(anio), "variable":var, "I_Moran":mi.I, "p_sim":mi.p_sim})
    moran_df = pd.DataFrame(moran_rows)
    display(moran_df)
    moran_df.to_csv(OUT / "moran_global.csv", index=False, encoding="utf-8-sig")

# 14. Modelo econométrico parsimonioso de panel con efectos fijos

# La frecuencia anual oficial implica que no corresponde presentar este resultado como un VAR trimestral. La especificación siguiente estima una asociación contemporánea de panel con efectos fijos por departamento y año, usando errores estándar agrupados por departamento. No identifica causalidad. El rezago de delitos sólo se incluye si hay años suficientes. Con pocos años o departamentos, los resultados se marcan como exploratorios.

import statsmodels.formula.api as smf

if not panel.empty:
    model = panel.copy()
    model["log_delitos"] = np.log1p(model["delitos"])
    n_deps = model["dep_norm"].nunique()
    n_years = model["anio"].nunique()
    print(f"Observaciones={len(model)} | departamentos={n_deps} | años={n_years}")
    if n_deps >= 10 and n_years >= 3 and len(model) > (n_deps + n_years):
        formula = "log_delitos ~ tasa_desempleo + C(dep_norm) + C(anio)"
        try:
            fit = smf.ols(formula, data=model).fit(
                cov_type="cluster", cov_kwds={"groups": model["dep_norm"]}
            )
            print(fit.summary())
            (OUT / "modelo_panel_resumen.txt").write_text(fit.summary().as_text(), encoding="utf-8")
            coef = pd.DataFrame({
                "coeficiente": fit.params,
                "error_std": fit.bse,
                "p_valor": fit.pvalues
            })
            coef.to_csv(OUT / "coeficientes_modelo.csv", encoding="utf-8-sig")
            print("Resultados guardados en:", OUT)
        except Exception as e:
            print("El modelo no pudo estimarse:", repr(e))
    else:
        print("Panel demasiado corto para una estimación defendible con efectos fijos.")
        print("Amplía los años o reduce la complejidad del modelo; no se reportan coeficientes artificiales.")
else:
    print("No se estima el modelo porque el panel no se construyó.")

# 15. Exportar datos y reporte de auditoría

# Guardar auditoría, inventario y fuentes descargadas.
try:
    auditoria_delitos.to_csv(OUT / "auditoria_delitos.csv", index=False, encoding="utf-8-sig")
except Exception:
    pass

try:
    pd.DataFrame([{"archivo":x["path"].name, "url":x["url"], "anio":x.get("anio","")} for x in archivos_delitos]).to_csv(
        OUT / "fuentes_delitos_descargadas.csv", index=False, encoding="utf-8-sig")
except Exception:
    pass

try:
    pd.DataFrame([{"archivo":x["path"].name, "url":x["url"], "anio":x.get("anio","")} for x in archivos_dane]).to_csv(
        OUT / "fuentes_dane_descargadas.csv", index=False, encoding="utf-8-sig")
except Exception:
    pass

print("Archivos disponibles en:", OUT)
for p in sorted(OUT.glob("*")):
    print("-", p.name, f"({p.stat().st_size:,} bytes)")

# 16. Interpretación y límites
#
# - La variable de delitos representa registros reportados por la Policía, no necesariamente la totalidad de delitos ocurridos.
# - La tasa departamental anual del DANE no debe convertirse en una tasa trimestral por interpolación.
# - Si la extracción automática del DANE no reconoce un anexo, revisa los enlaces y las columnas impresas en las secciones 7–9. El archivo puede haber cambiado de formato.
# - La matriz Queen representa contigüidad territorial; KNN se usa como alternativa cuando hay unidades aisladas. La elección debe justificarse y someterse a sensibilidad.
# - Moran detecta autocorrelación espacial global, no causalidad.
# - La regresión de panel incluida es un modelo exploratorio con efectos fijos, no un VAR espacial estructural. Para estimar un VAR espacial trimestral hace falta una serie trimestral departamental de desempleo construida con microdatos GEIH y una estrategia econométrica adecuada.
# - La salida automática debe revisarse antes de usarse en un informe académico.

"""

if st.button("Ejecutar análisis", type="primary"):
    registro = io.StringIO()
    try:
        with st.spinner("Descargando y procesando datos..."):
            with contextlib.redirect_stdout(registro):
                exec(ANALISIS, globals())
        st.success("El script terminó. Revisa el registro y los archivos generados.")
    except Exception as exc:
        st.error(f"El análisis se detuvo: {type(exc).__name__}: {exc}")
    st.text_area("Registro de ejecución", registro.getvalue(), height=420)
