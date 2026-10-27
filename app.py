"""
Streamlit app: Análisis espacial de desempleo y delincuencia en Colombia
Fuentes oficiales: Policía Nacional + DANE
"""

import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

st.set_page_config(
    page_title="Desempleo y delincuencia – Colombia",
    page_icon="🇨🇴",
    layout="wide",
)

st.title("Análisis espacial: desempleo y delincuencia en Colombia")
st.markdown(
    """
Descarga y procesa datos oficiales de la **Policía Nacional** (registros de delitos)
y del **DANE** (tasa de desocupación departamental anual).  
La extracción automática debe revisarse antes de interpretar los resultados.
"""
)

with st.expander("Notas metodológicas", expanded=False):
    st.markdown(
        """
- Los delitos son **registros reportados**, no necesariamente la totalidad de hechos.
- La tasa de desocupación del DANE es **anual** (23 departamentos + Bogotá).  
  No se interpolan tasas trimestrales.
- El modelo de panel con efectos fijos es **exploratorio** (asociación contemporánea, no causalidad).
- Moran global mide autocorrelación espacial, no causalidad.
- Si falla la descarga automática, se informan los errores y no se inventan datos.
        """
    )

st.info(
    "La ejecución puede tardar varios minutos (descarga de archivos grandes de Policía). "
    "Pulsa el botón para iniciar."
)

# ---------------------------------------------------------------------------
# Sidebar: opciones
# ---------------------------------------------------------------------------
st.sidebar.header("Opciones")
max_anios = st.sidebar.slider(
    "Máximo de años de delitos a procesar (los más recientes)",
    min_value=1,
    max_value=5,
    value=2,
    help="Cada archivo de Policía pesa ~30-40 MB. Con 2-3 años suele bastar para una demo.",
)
anios_dane = st.sidebar.multiselect(
    "Años DANE a incluir",
    options=[2020, 2021, 2022, 2023, 2024, 2025],
    default=[2022, 2023, 2024],
)

run = st.button("Ejecutar análisis", type="primary")

# ---------------------------------------------------------------------------
# Ejecución
# ---------------------------------------------------------------------------
if run:
    # Comprobar dependencias críticas
    missing = []
    for pkg in ["geopandas", "libpysal", "esda", "statsmodels", "bs4"]:
        try:
            __import__(pkg if pkg != "bs4" else "bs4")
        except ImportError:
            missing.append(pkg)
    if missing:
        st.error(
            f"Faltan dependencias: {', '.join(missing)}. "
            "En Streamlit Community Cloud sube un `requirements.txt` en la raíz del repositorio "
            "con geopandas, libpysal, esda, statsmodels, beautifulsoup4, xlrd, openpyxl, etc. "
            "Luego Manage app → Reboot. No instales paquetes dentro de app.py."
        )
        st.stop()

    with st.spinner("Descargando y procesando datos oficiales… esto puede tardar varios minutos"):
        try:
            from data_pipeline import run_pipeline

            res = run_pipeline(
                max_anios_delitos=max_anios,
                anios_dane=anios_dane if anios_dane else None,
            )
        except Exception as e:
            st.exception(e)
            st.stop()

    # Logs
    with st.expander("Registro de ejecución", expanded=True):
        for line in res["logs"]:
            st.text(line)

    panel = res["panel"]
    delitos = res["delitos"]
    desempleo = res["desempleo"]
    moran = res["moran"]
    fit = res["modelo"]
    geo = res["geo"]

    if panel.empty:
        st.warning(
            "No se pudo construir el panel unido. Revisa los logs. "
            "Posibles causas: estructura de Excel cambiada, años sin solape, o fallo de descarga."
        )
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Delitos (muestra)")
            st.dataframe(delitos.head(20) if not delitos.empty else pd.DataFrame())
        with col2:
            st.subheader("Desempleo (muestra)")
            st.dataframe(desempleo.head(20) if not desempleo.empty else pd.DataFrame())
        st.stop()

    # ------------------------------------------------------------------
    # Resultados
    # ------------------------------------------------------------------
    st.success(f"Panel construido: {len(panel)} observaciones | "
               f"{panel['dep_norm'].nunique()} departamentos | "
               f"años {sorted(panel['anio'].unique())}")

    st.subheader("Panel departamental anual")
    st.dataframe(
        panel.sort_values(["anio", "dep_norm"]).reset_index(drop=True),
        use_container_width=True,
    )

    # Descargar CSV
    csv = panel.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button(
        "Descargar panel CSV",
        data=csv,
        file_name="panel_departamental_anual.csv",
        mime="text/csv",
    )

    # Gráficos
    st.subheader("Exploración gráfica")
    c1, c2 = st.columns(2)
    with c1:
        fig, ax = plt.subplots(figsize=(7, 4))
        panel.groupby("anio")["tasa_desempleo"].mean().plot(marker="o", ax=ax)
        ax.set_title("Tasa media de desocupación")
        ax.set_ylabel("%")
        ax.grid(alpha=0.3)
        st.pyplot(fig)
        plt.close()
    with c2:
        fig, ax = plt.subplots(figsize=(7, 4))
        panel.groupby("anio")["delitos"].sum().plot(marker="o", ax=ax, color="darkred")
        ax.set_title("Total de registros delictivos")
        ax.set_ylabel("Registros")
        ax.grid(alpha=0.3)
        st.pyplot(fig)
        plt.close()

    fig, ax = plt.subplots(figsize=(7, 5))
    ax.scatter(panel["tasa_desempleo"], np.log1p(panel["delitos"]), alpha=0.65)
    ax.set_xlabel("Tasa de desempleo (%)")
    ax.set_ylabel("log(1 + delitos)")
    ax.set_title("Asociación descriptiva (no causal)")
    ax.grid(alpha=0.3)
    st.pyplot(fig)
    plt.close()

    # Moran
    if not moran.empty:
        st.subheader("Autocorrelación espacial global (Moran I)")
        st.dataframe(moran, use_container_width=True)
        st.caption("p_sim < 0.05 sugiere autocorrelación espacial significativa.")

    # Modelo
    if fit is not None:
        st.subheader("Modelo de panel (efectos fijos departamento + año)")
        st.code(fit.summary().as_text())
        st.caption(
            "Errores estándar clusterizados por departamento. "
            "Interpretación: asociación contemporánea controlando efectos fijos; no causalidad."
        )
    else:
        st.info("Modelo de panel no estimado (pocas observaciones o dependencias faltantes).")

    # Mapa simple si hay geo
    if geo is not None and not panel.empty:
        st.subheader("Mapa (último año disponible)")
        try:
            import geopandas as gpd

            ultimo = int(panel["anio"].max())
            p = panel[panel["anio"] == ultimo][["dep_norm", "tasa_desempleo", "delitos"]]
            g = geo.merge(p, on="dep_norm", how="inner")
            if not g.empty:
                fig, axes = plt.subplots(1, 2, figsize=(12, 6))
                g.plot(column="tasa_desempleo", cmap="YlOrRd", legend=True, ax=axes[0], edgecolor="k", linewidth=0.3)
                axes[0].set_title(f"Tasa de desempleo {ultimo}")
                axes[0].axis("off")
                g.plot(column="delitos", cmap="Reds", legend=True, ax=axes[1], edgecolor="k", linewidth=0.3)
                axes[1].set_title(f"Delitos registrados {ultimo}")
                axes[1].axis("off")
                st.pyplot(fig)
                plt.close()
        except Exception as e:
            st.warning(f"No se pudo dibujar el mapa: {e}")

else:
    st.markdown(
        """
### Cómo desplegar en Streamlit Community Cloud

1. Sube a un repositorio de GitHub (raíz):
   - `app.py`
   - `data_pipeline.py`
   - `requirements.txt`
2. En [share.streamlit.io](https://share.streamlit.io) conecta el repo.
3. Si aparece “Faltan dependencias”, confirma que `requirements.txt` está en la **raíz**
   y haz **Manage app → Reboot**.
4. No uses `%pip install` ni `os.system("pip install…")` dentro de `app.py`.

### Fuentes
- [Policía Nacional – Información de delitos a nivel de registro 2020-2026](https://www.policia.gov.co/contenido/informacion-delitos-nivel-registro-ano-2020-2026)
- [DANE – Mercado laboral por departamentos (históricos)](https://www.dane.gov.co/index.php/estadisticas-por-tema/mercado-laboral/mercado-laboral-por-departamentos/mercado-laboral-por-departamento-historicos)
        """
    )
