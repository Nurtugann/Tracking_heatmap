import streamlit as st
import folium
from folium.plugins import HeatMap, MarkerCluster
import pandas as pd
import numpy as np
import tempfile
import os
import json

st.set_page_config(layout="wide")
st.title("Теплокарта времени пребывания + Кластеризация населённых пунктов")

# --- 1) Загрузка данных ---
@st.cache_data
def load_data():
    df = pd.read_csv("data.csv")
    df["Начало"] = pd.to_datetime(df["Начало"], errors='coerce')
    df["Конец"]  = pd.to_datetime(df["Конец"],  errors='coerce')
    return df

df = load_data()

# --- 2) Фильтр по времени (слайдер) ---
min_time = min(df["Начало"].min(), df["Конец"].min())
max_time = max(df["Начало"].max(), df["Конец"].max())

min_time_py = min_time.to_pydatetime()
max_time_py = max_time.to_pydatetime()

time_range = st.slider(
    "Выберите период (по дате)",
    min_value=min_time_py,
    max_value=max_time_py,
    value=(min_time_py, max_time_py)
)

filtered_df = df[
    (df["Начало"] >= time_range[0]) &
    (df["Конец"]  <= time_range[1])
]

# --- 3) Выбор агента ---
all_agents = ["Все"] + sorted(filtered_df["Группировка"].dropna().unique())
selected_agent = st.selectbox("Выберите агента (Группировка)", all_agents)

if selected_agent != "Все":
    filtered_df = filtered_df[filtered_df["Группировка"] == selected_agent]

# ----------------------------------------------------------------------------
# ------------------ Расчёт времени пребывания "на точке" ---------------------
# ----------------------------------------------------------------------------
def calculate_time_spent(df_local, threshold=1e-4):
    df_copy = df_local.copy()
    df_copy.sort_values(by=["Группировка", "Начало"], inplace=True)
    df_copy["dwelling_time"] = 0.0

    for group_name, group_data in df_copy.groupby("Группировка", group_keys=False):
        idx_list = group_data.index.to_list()
        for i in range(len(idx_list) - 1):
            idx_i   = idx_list[i]
            idx_i1  = idx_list[i + 1]
            
            lat_end_i   = group_data.loc[idx_i,  "latitude_конеч"]
            lon_end_i   = group_data.loc[idx_i,  "longitude_конеч"]
            lat_start_i1= group_data.loc[idx_i1, "latitude_нач"]
            lon_start_i1= group_data.loc[idx_i1, "longitude_нач"]
            
            dist = np.sqrt((lat_end_i - lat_start_i1)**2 + (lon_end_i - lon_start_i1)**2)
            
            if dist < threshold:
                t_end   = group_data.loc[idx_i,  "Конец"]
                t_start = group_data.loc[idx_i1, "Начало"]
                if pd.notnull(t_end) and pd.notnull(t_start):
                    delta_sec = (t_start - t_end).total_seconds()
                    if delta_sec > 0:
                        df_copy.at[idx_i, "dwelling_time"] = delta_sec
    return df_copy

df_time = calculate_time_spent(filtered_df, threshold=1e-4)
df_time_sum = df_time.groupby(
    ["latitude_конеч", "longitude_конеч"], dropna=False
)["dwelling_time"].sum().reset_index()

# ----------------------------------------------------------------------------
# --------- Создаём карту: HeatMap + границы регионов + MarkerCluster --------
# ----------------------------------------------------------------------------

m = folium.Map(location=[48.0, 68.0], zoom_start=5)

# 1) Рисуем слой с границами регионов
with open("geoBoundaries-KAZ-ADM2.geojson", encoding="utf-8") as f:
    polygons = json.load(f)

folium.GeoJson(
    polygons,
    name="Границы регионов",
).add_to(m)

# 2) Рисуем слой с HeatMap (по времени пребывания)
heat_data = []
for _, row in df_time_sum.iterrows():
    lat = row["latitude_конеч"]
    lon = row["longitude_конеч"]
    weight = row["dwelling_time"]
    if pd.notnull(lat) and pd.notnull(lon):
        heat_data.append([lat, lon, weight])

HeatMap(
    heat_data,
    name="Время пребывания (HeatMap)",
    radius=20,
    blur=10,
    max_zoom=10
).add_to(m)

# 3) Теперь создадим MarkerCluster для населённых пунктов
with open("hotosm_kaz_populated_places_points_geojson.geojson", encoding="utf-8") as f:
    points = json.load(f)

marker_cluster = MarkerCluster(name="Населённые пункты (кластер)").add_to(m)

# Добавляем каждую точку в кластер
for feature in points["features"]:
    geom = feature["geometry"]
    props = feature["properties"]
    
    if geom["type"] == "Point":
        lon, lat = geom["coordinates"]
        name = props.get("name", "Без названия")
        folium.Marker(
            location=[lat, lon],
            popup=name,
            tooltip=name
        ).add_to(marker_cluster)

# Добавляем LayerControl
folium.LayerControl().add_to(m)

# --- 7) Отображаем карту в Streamlit ---
with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
    map_path = f.name
    m.save(map_path)

with open(map_path, 'r', encoding='utf-8') as f:
    html = f.read()

st.components.v1.html(html, height=600, width=1000)
os.remove(map_path)
