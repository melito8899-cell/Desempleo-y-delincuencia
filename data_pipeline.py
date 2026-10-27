"""
Pipeline robusto de descarga y procesamiento:
- Delitos Policía Nacional (nivel de registro 2020-2026)
- Tasa de desocupación departamental DANE (anexos anuales)
- Geometría departamental (servicio ArcGIS público)

Diseñado para Google Colab y Streamlit Community Cloud.
No inventa datos: si falla la extracción, lo reporta y continúa.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, unquote

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

warnings.filterwarnings("ignore", category=FutureWarning)

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

BASE = Path("/tmp/desempleo_delincuencia_colombia")
RAW = BASE / "raw"
OUT = BASE / "resultados"

URL_POLICIA = "https://www.policia.gov.co/contenido/informacion-delitos-nivel-registro-ano-2020-2026"
URL_DANE_HIST = (
    "https://www.dane.gov.co/index.php/estadisticas-por-tema/"
    "mercado-laboral/mercado-laboral-por-departamentos/"
    "mercado-laboral-por-departamento-historicos"
)
URL_GEO = (
    "https://services.arcgis.com/BQTQBNBsmxjF8vus/arcgis/rest/services/"
    "DelitosPoliciaNacional/FeatureServer/1/query"
)

# Enlaces directos conocidos (fallback si el scraper falla)
DIRECT_DELITOS = {
    2020: "https://www.policia.gov.co/sites/default/files/INFORMACI%C3%93N_DE_DELITOS_A_NIVEL_DE_REGISTRO_A%C3%91O_2020_3.xlsx",
    2021: "https://www.policia.gov.co/sites/default/files/INFORMACI%C3%93N_DE_DELITOS_A_NIVEL_DE_REGISTRO_A%C3%91O_2021_3.xlsx",
    2022: "https://www.policia.gov.co/sites/default/files/INFORMACI%C3%93N_DE_DELITOS_A_NIVEL_DE_REGISTRO_A%C3%91O_2022.zip",
    2023: "https://www.policia.gov.co/sites/default/files/INFORMACI%C3%93N_DE_DELITOS_A_NIVEL_DE_REGISTRO_A%C3%91O_2023.zip",
    2024: "https://www.policia.gov.co/sites/default/files/INFORMACI%C3%93N_DE_DELITOS_A_NIVEL_DE_REGISTRO_A%C3%91O_2024_3.xlsx",
    2025: "https://www.policia.gov.co/sites/default/files/INFORMACI%C3%93N_DE_DELITOS_A_NIVEL_DE_REGISTRO_A%C3%91O_2025_3.xlsx",
    2026: "https://www.policia.gov.co/sites/default/files/INFORMACI%C3%93N_DELITOS_A_NIVEL_DE_REGISTRO_A%C3%91O_2026_3.xlsx",
}

DIRECT_DANE = {
    2020: "https://www.dane.gov.co/files/investigaciones/boletines/ech/ml_depto/anexo_dep_20.xls",
    2021: "https://www.dane.gov.co/files/investigaciones/boletines/ech/ml_depto/anexo_dep_21.xls",
    2022: "https://www.dane.gov.co/files/investigaciones/boletines/ech/ml_depto/anexo_dep_22.xls",
    2023: "https://www.dane.gov.co/files/operaciones/GEIH/anex-GEIHDepartamentos-2023.xls",
    2024: "https://www.dane.gov.co/files/operaciones/GEIH/anex-GEIHDepartamentos-2024.xls",
    2025: "https://www.dane.gov.co/files/operaciones/GEIH/anex-GEIHDepartamentos-2025.xls",
}

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
    }
)

ALIAS = {
    "bogota_dc": "bogota_d_c",
    "bogota_d_c": "bogota_d_c",
    "bogota": "bogota_d_c",
    "bogota_d.c": "bogota_d_c",
    "valle": "valle_del_cauca",
    "valle_del_cauca": "valle_del_cauca",
    "san_andres": "archipielago_de_san_andres_providencia_y_santa_catalina",
    "san_andres_providencia_y_santa_catalina": "archipielago_de_san_andres_providencia_y_santa_catalina",
    "norte_de_santander": "norte_de_santander",
    "la_guajira": "la_guajira",
    "atlantico": "atlantico",
    "bolivar": "bolivar",
    "boyaca": "boyaca",
    "caldas": "caldas",
    "caqueta": "caqueta",
    "cauca": "cauca",
    "cesar": "cesar",
    "choco": "choco",
    "cordoba": "cordoba",
    "cundinamarca": "cundinamarca",
    "guainia": "guainia",
    "guaviare": "guaviare",
    "huila": "huila",
    "magdalena": "magdalena",
    "meta": "meta",
    "narino": "narino",
    "putumayo": "putumayo",
    "quindio": "quindio",
    "risaralda": "risaralda",
    "santander": "santander",
    "sucre": "sucre",
    "tolima": "tolima",
    "arauca": "arauca",
    "amazonas": "amazonas",
    "antioquia": "antioquia",
    "casanare": "casanare",
    "vaupes": "vaupes",
    "vichada": "vichada",
}


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def ensure_dirs() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)


def normalizar(s: Any) -> str:
    s = str(s).strip().lower()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s


def dep_key(x: Any) -> str:
    n = normalizar(x)
    return ALIAS.get(n, n)


def get_url(url: str, timeout: int = 120) -> requests.Response:
    r = SESSION.get(url, timeout=timeout)
    r.raise_for_status()
    return r


def descargar(url: str, destino: Path, timeout: int = 240) -> Path:
    r = get_url(url, timeout=timeout)
    if len(r.content) < 200:
        raise ValueError(f"Descarga sospechosamente pequeña: {url}")
    destino.write_bytes(r.content)
    return destino


# ---------------------------------------------------------------------------
# Geometría
# ---------------------------------------------------------------------------

def descargar_geometria() -> Optional[Any]:
    """Devuelve GeoDataFrame o None."""
    try:
        import geopandas as gpd
    except ImportError:
        print("geopandas no instalado")
        return None

    geo_path = RAW / "departamentos.geojson"
    params = {
        "where": "1=1",
        "outFields": "*",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "geojson",
    }
    try:
        resp = SESSION.get(URL_GEO, params=params, timeout=180)
        resp.raise_for_status()
        gj = resp.json()
        if not gj.get("features"):
            raise ValueError("Servicio sin entidades")
        geo_path.write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")
        geo = gpd.read_file(geo_path)
        # Normalizar nombre de departamento
        col_dep = None
        for c in geo.columns:
            if re.search(r"departamento|nom.*depto|nombre|depto", normalizar(c)):
                col_dep = c
                break
        if col_dep is None and "NOMBRE_DEPTO" in geo.columns:
            col_dep = "NOMBRE_DEPTO"
        if col_dep is None and "CODIGO_DEPTO" in geo.columns:
            # fallback: usar código
            geo["dep_norm"] = geo["CODIGO_DEPTO"].astype(str).str.zfill(2)
        else:
            geo["dep_norm"] = geo[col_dep].map(dep_key)
        print(f"Geometrías: {len(geo)} | columna depto: {col_dep}")
        return geo
    except Exception as e:
        print(f"ERROR geometría: {e!r}")
        return None


# ---------------------------------------------------------------------------
# Delitos (Policía)
# ---------------------------------------------------------------------------

def _leer_delitos_excel(path: Path) -> pd.DataFrame:
    """Lee un .xlsx de delitos con header en fila 11."""
    xl = pd.ExcelFile(path, engine="openpyxl")
    hoja = None
    for sh in xl.sheet_names:
        if "DELITO" in sh.upper() or "REGISTRO" in sh.upper():
            hoja = sh
            break
    if hoja is None:
        hoja = xl.sheet_names[0]

    # Header real está en la fila 11 (0-indexed)
    df = pd.read_excel(path, sheet_name=hoja, header=11, engine="openpyxl")
    df.columns = [str(c).strip() for c in df.columns]
    # Eliminar columnas completamente vacías
    df = df.dropna(axis=1, how="all")
    return df


def procesar_delitos(
    anios: Optional[List[int]] = None,
    max_anios: int = 4,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Descarga y agrega delitos por departamento-año.
    Por defecto procesa los 4 años más recientes para no saturar memoria/tiempo.
    """
    ensure_dirs()
    if anios is None:
        anios = sorted(DIRECT_DELITOS.keys(), reverse=True)[:max_anios]

    partes = []
    auditoria = []

    for anio in anios:
        url = DIRECT_DELITOS.get(anio)
        if not url:
            continue
        # Solo .xlsx por ahora (los .zip requieren unzip adicional)
        if url.lower().endswith(".zip"):
            auditoria.append({"anio": anio, "estado": "omitido_zip", "detalle": "archivo zip"})
            print(f"Omitido {anio}: archivo ZIP (requiere extracción manual)")
            continue

        destino = RAW / f"delitos_{anio}.xlsx"
        try:
            if not destino.exists() or destino.stat().st_size < 1000:
                print(f"Descargando delitos {anio}...")
                descargar(url, destino, timeout=300)
            df = _leer_delitos_excel(destino)
            print(f"  {anio}: columnas = {list(df.columns)[:8]}...")

            # Detectar columnas clave
            col_depto = None
            col_fecha = None
            col_cant = None
            for c in df.columns:
                n = normalizar(c)
                if col_depto is None and re.search(r"^departamento$|depto", n):
                    col_depto = c
                if col_fecha is None and re.search(r"fecha.*hecho|fecha", n):
                    col_fecha = c
                if col_cant is None and re.search(r"cantidad|cant", n):
                    col_cant = c

            if col_depto is None:
                auditoria.append({"anio": anio, "estado": "sin_depto", "columnas": list(df.columns)})
                print(f"  {anio}: no se detectó columna DEPARTAMENTO")
                continue

            df = df.copy()
            df["_depto"] = df[col_depto].astype("string").str.strip()
            df = df[df["_depto"].notna() & (df["_depto"] != "") & (df["_depto"].str.lower() != "nan")]

            if col_cant and col_cant in df.columns:
                df["_cant"] = pd.to_numeric(df[col_cant], errors="coerce").fillna(1)
            else:
                df["_cant"] = 1

            # Año: preferir columna de fecha; si no, usar el año del archivo
            if col_fecha and col_fecha in df.columns:
                fechas = pd.to_datetime(df[col_fecha], errors="coerce", dayfirst=False)
                df["_anio"] = fechas.dt.year
                df.loc[df["_anio"].isna(), "_anio"] = anio
            else:
                df["_anio"] = anio

            df["_anio"] = df["_anio"].astype(int)
            agg = (
                df.groupby(["_depto", "_anio"], as_index=False)["_cant"]
                .sum()
                .rename(columns={"_depto": "departamento_raw", "_anio": "anio", "_cant": "delitos"})
            )
            agg["dep_norm"] = agg["departamento_raw"].map(dep_key)
            partes.append(agg)
            auditoria.append(
                {
                    "anio": anio,
                    "estado": "ok",
                    "filas": len(df),
                    "departamentos": agg["departamento_raw"].nunique(),
                    "delitos": int(agg["delitos"].sum()),
                }
            )
            print(f"  {anio}: {len(df):,} filas → {int(agg['delitos'].sum()):,} delitos")
        except Exception as e:
            auditoria.append({"anio": anio, "estado": "error", "detalle": repr(e)})
            print(f"  ERROR {anio}: {e!r}")

    if partes:
        delitos = pd.concat(partes, ignore_index=True)
        delitos = (
            delitos.groupby(["departamento_raw", "dep_norm", "anio"], as_index=False)["delitos"].sum()
        )
    else:
        delitos = pd.DataFrame(columns=["departamento_raw", "dep_norm", "anio", "delitos"])

    auditoria_df = pd.DataFrame(auditoria)
    return delitos, auditoria_df


# ---------------------------------------------------------------------------
# Desempleo (DANE)
# ---------------------------------------------------------------------------

def _extraer_td_de_anexo(path: Path, anio_archivo: int) -> pd.DataFrame:
    """
    Extrae tasa de desocupación (TD) de la hoja 'Departamentos anual'
    de los anexos DANE (estructura de bloques por departamento).
    """
    try:
        xl = pd.ExcelFile(path)
    except Exception:
        # fallback engine
        xl = pd.ExcelFile(path, engine="xlrd")

    hoja = None
    for sh in xl.sheet_names:
        if "departamentos anual" in sh.lower() and "hombre" not in sh.lower() and "mujer" not in sh.lower():
            hoja = sh
            break
    if hoja is None:
        for sh in xl.sheet_names:
            if "departamento" in sh.lower():
                hoja = sh
                break
    if hoja is None:
        hoja = xl.sheet_names[0]

    df = pd.read_excel(path, sheet_name=hoja, header=None)
    # Localizar fila de años (Concepto | 2007 | 2008 | ...)
    anio_cols: Dict[int, int] = {}
    fila_concepto = None
    for i in range(min(20, len(df))):
        row = df.iloc[i]
        if str(row.iloc[0]).strip().lower() in ("concepto", "conceptos"):
            fila_concepto = i
            for j in range(1, len(row)):
                try:
                    a = int(float(row.iloc[j]))
                    if 2000 <= a <= 2030:
                        anio_cols[a] = j
                except Exception:
                    pass
            break

    if not anio_cols:
        # Algunos anexos tienen el año del archivo como única columna de datos
        # o estructura distinta; intentar detectar años en las primeras filas
        for i in range(min(15, len(df))):
            for j in range(1, min(20, df.shape[1])):
                try:
                    a = int(float(df.iloc[i, j]))
                    if 2000 <= a <= 2030:
                        anio_cols[a] = j
                except Exception:
                    pass
        if anio_cols:
            fila_concepto = 0

    if not anio_cols:
        raise ValueError(f"No se localizaron columnas de años en {path.name}")

    # Lista conocida de departamentos (23 + Bogotá) para filtrar ruido
    DEPTOS_VALIDOS = {
        "antioquia", "atlantico", "bolivar", "boyaca", "caldas", "caqueta",
        "cauca", "cesar", "choco", "cordoba", "cundinamarca", "huila",
        "la_guajira", "magdalena", "meta", "narino", "norte_de_santander",
        "quindio", "risaralda", "santander", "sucre", "tolima", "valle_del_cauca",
        "bogota_d_c", "bogota", "bogota_dc",
    }

    # Recorrer bloques: cada departamento empieza con su nombre en col 0
    registros = []
    i = 0
    while i < len(df):
        val = df.iloc[i, 0]
        if pd.isna(val):
            i += 1
            continue
        nombre = str(val).strip()
        nombre_norm = dep_key(nombre)
        # ¿Es un nombre de departamento conocido?
        es_depto = (
            len(nombre) > 2
            and nombre_norm in DEPTOS_VALIDOS
            and not re.search(
                r"tasa|poblacion|concepto|serie|total|nota|fuente|geih|departamentos$",
                nombre,
                re.I,
            )
            and not nombre.replace(".", "").replace(",", "").isdigit()
        )
        if es_depto:
            # Buscar "Tasa de Desocupación (TD)" en las siguientes ~15 filas
            for k in range(i + 1, min(i + 18, len(df))):
                concepto = str(df.iloc[k, 0]).strip().lower()
                if "tasa de desocupaci" in concepto or concepto.startswith("td ") or "td)" in concepto:
                    for anio, col in anio_cols.items():
                        try:
                            tasa = float(df.iloc[k, col])
                            if 0 <= tasa <= 100:
                                registros.append(
                                    {
                                        "departamento_raw": nombre,
                                        "anio": anio,
                                        "tasa_desempleo": tasa,
                                    }
                                )
                        except Exception:
                            pass
                    break
            i += 1
        else:
            i += 1

    out = pd.DataFrame(registros)
    if out.empty:
        raise ValueError(f"No se extrajo ninguna TD de {path.name}")
    out["dep_norm"] = out["departamento_raw"].map(dep_key)
    # Preferir el año del archivo si está presente; si no, todos
    if anio_archivo in out["anio"].values:
        out = out[out["anio"] == anio_archivo].copy()
    return out.drop_duplicates(["dep_norm", "anio"])


def procesar_dane(anios: Optional[List[int]] = None) -> Tuple[pd.DataFrame, List[dict]]:
    ensure_dirs()
    if anios is None:
        anios = sorted(DIRECT_DANE.keys())

    partes = []
    log = []
    for anio in anios:
        url = DIRECT_DANE.get(anio)
        if not url:
            continue
        destino = RAW / f"dane_{anio}{Path(url).suffix}"
        try:
            if not destino.exists() or destino.stat().st_size < 500:
                print(f"Descargando DANE {anio}...")
                descargar(url, destino, timeout=120)
            td = _extraer_td_de_anexo(destino, anio)
            partes.append(td)
            log.append({"anio": anio, "estado": "ok", "n": len(td)})
            print(f"  DANE {anio}: {len(td)} departamentos")
        except Exception as e:
            log.append({"anio": anio, "estado": "error", "detalle": repr(e)})
            print(f"  ERROR DANE {anio}: {e!r}")

    if partes:
        desempleo = pd.concat(partes, ignore_index=True)
        desempleo = desempleo.drop_duplicates(["dep_norm", "anio"], keep="last")
    else:
        desempleo = pd.DataFrame(
            columns=["departamento_raw", "anio", "tasa_desempleo", "dep_norm"]
        )
    return desempleo, log


# ---------------------------------------------------------------------------
# Panel y modelación
# ---------------------------------------------------------------------------

def construir_panel(
    delitos: pd.DataFrame,
    desempleo: pd.DataFrame,
) -> pd.DataFrame:
    if delitos.empty or desempleo.empty:
        return pd.DataFrame()
    panel = desempleo.merge(
        delitos[["dep_norm", "anio", "delitos", "departamento_raw"]],
        on=["dep_norm", "anio"],
        how="inner",
        suffixes=("_dane", "_policia"),
    )
    return panel


def calcular_moran(panel: pd.DataFrame, geo: Any) -> pd.DataFrame:
    if geo is None or panel.empty:
        return pd.DataFrame()
    try:
        from libpysal.weights import Queen, KNN
        from esda.moran import Moran
    except ImportError:
        print("libpysal/esda no disponibles")
        return pd.DataFrame()

    deps = set(panel["dep_norm"].unique())
    geo_m = geo[geo["dep_norm"].isin(deps)].drop_duplicates("dep_norm").sort_values("dep_norm")
    if len(geo_m) < 5:
        return pd.DataFrame()

    try:
        w = Queen.from_dataframe(geo_m, use_index=False)
        if len(w.islands) > 0:
            w = KNN.from_dataframe(geo_m, k=min(4, len(geo_m) - 1))
        w.transform = "R"
    except Exception as e:
        print(f"No se pudo construir W: {e!r}")
        return pd.DataFrame()

    rows = []
    for anio, b in panel.groupby("anio"):
        b = b.set_index("dep_norm").reindex(geo_m["dep_norm"])
        for var in ["tasa_desempleo", "delitos"]:
            x = b[var].to_numpy(dtype=float)
            if np.isfinite(x).all() and np.std(x) > 0:
                mi = Moran(x, w, permutations=499)
                rows.append({"anio": int(anio), "variable": var, "I_Moran": mi.I, "p_sim": mi.p_sim})
    return pd.DataFrame(rows)


def estimar_panel(panel: pd.DataFrame) -> Optional[Any]:
    if panel.empty:
        return None
    try:
        import statsmodels.formula.api as smf
    except ImportError:
        return None

    model = panel.copy()
    model["log_delitos"] = np.log1p(model["delitos"])
    n_deps = model["dep_norm"].nunique()
    n_years = model["anio"].nunique()
    if n_deps < 8 or n_years < 2 or len(model) <= (n_deps + n_years):
        print("Panel demasiado corto para FE")
        return None
    formula = "log_delitos ~ tasa_desempleo + C(dep_norm) + C(anio)"
    try:
        fit = smf.ols(formula, data=model).fit(
            cov_type="cluster", cov_kwds={"groups": model["dep_norm"]}
        )
        return fit
    except Exception as e:
        print(f"Modelo falló: {e!r}")
        return None


# ---------------------------------------------------------------------------
# Orquestador
# ---------------------------------------------------------------------------

def run_pipeline(
    anios_delitos: Optional[List[int]] = None,
    anios_dane: Optional[List[int]] = None,
    max_anios_delitos: int = 3,
) -> Dict[str, Any]:
    """
    Ejecuta todo el flujo y devuelve un diccionario con resultados.
    Pensado para llamarse desde Streamlit o Colab.
    """
    ensure_dirs()
    logs: List[str] = []

    def log(msg: str) -> None:
        print(msg)
        logs.append(msg)

    log("1. Geometría...")
    geo = descargar_geometria()

    log("2. Delitos Policía...")
    delitos, aud_delitos = procesar_delitos(anios=anios_delitos, max_anios=max_anios_delitos)
    log(f"   Delitos agregados: {len(delitos)} filas")

    log("3. Desempleo DANE...")
    desempleo, aud_dane = procesar_dane(anios=anios_dane)
    log(f"   Desempleo: {len(desempleo)} filas")

    log("4. Panel...")
    panel = construir_panel(delitos, desempleo)
    log(f"   Panel unido: {panel.shape}")

    moran = pd.DataFrame()
    fit = None
    if not panel.empty:
        log("5. Moran...")
        moran = calcular_moran(panel, geo)
        log("6. Modelo panel...")
        fit = estimar_panel(panel)
        panel.to_csv(OUT / "panel_departamental_anual.csv", index=False, encoding="utf-8-sig")
        if not moran.empty:
            moran.to_csv(OUT / "moran_global.csv", index=False, encoding="utf-8-sig")
        if fit is not None:
            (OUT / "modelo_panel_resumen.txt").write_text(fit.summary().as_text(), encoding="utf-8")

    aud_delitos.to_csv(OUT / "auditoria_delitos.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(aud_dane).to_csv(OUT / "auditoria_dane.csv", index=False, encoding="utf-8-sig")

    return {
        "geo": geo,
        "delitos": delitos,
        "desempleo": desempleo,
        "panel": panel,
        "moran": moran,
        "modelo": fit,
        "logs": logs,
        "out_dir": OUT,
    }


if __name__ == "__main__":
    res = run_pipeline(max_anios_delitos=2)
    print("\n=== RESUMEN ===")
    print("Panel:", res["panel"].shape)
    if not res["panel"].empty:
        print(res["panel"].head(10))
    print("Archivos en", res["out_dir"])
