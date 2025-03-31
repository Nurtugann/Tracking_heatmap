import streamlit as st
import pandas as pd
import numpy as np
import json

st.set_page_config(layout="wide")
st.title("🔥 Теплокарта с переключаемыми слоями (JS + Leaflet) - Нормализация")

# --------------------------------------------------------------------------------
# 1. Загрузка данных
# --------------------------------------------------------------------------------
@st.cache_data
def load_data():
    df = pd.read_csv("data.csv")
    df["Начало"] = pd.to_datetime(df["Начало"], errors="coerce")
    df["Конец"]  = pd.to_datetime(df["Конец"],  errors="coerce")
    return df

df = load_data()

# --------------------------------------------------------------------------------
# 2. Фильтры (слайдер дат и выбор агента)
# --------------------------------------------------------------------------------
min_time = df["Начало"].min()
max_time = df["Конец"].max()

time_range = st.slider(
    "Выберите период",
    min_value=min_time.to_pydatetime(),
    max_value=max_time.to_pydatetime(),
    value=(min_time.to_pydatetime(), max_time.to_pydatetime())
)

filtered_df = df[
    (df["Начало"] >= time_range[0]) &
    (df["Конец"]  <= time_range[1])
]

agents = ["Все"] + sorted(filtered_df["Группировка"].dropna().unique())
selected_agent = st.selectbox("Агент", agents)
if selected_agent != "Все":
    filtered_df = filtered_df[filtered_df["Группировка"] == selected_agent]


# --------------------------------------------------------------------------------
# 3. Расчёт времени пребывания (наивный подход)
# --------------------------------------------------------------------------------
def calculate_time_spent(df_local, threshold=1e-4):
    """
    Наивный, простой подход к расчёту dwelling_time,
    в котором для каждой группы перебираются пары подряд идущих записей.
    """
    df_copy = df_local.copy()
    df_copy.sort_values(by=["Группировка", "Начало"], inplace=True)
    df_copy["dwelling_time"] = 0.0
    
    for group, group_data in df_copy.groupby("Группировка"):
        indices = group_data.index.to_list()
        for i in range(len(indices) - 1):
            i0, i1 = indices[i], indices[i + 1]
            dist = np.sqrt(
                (group_data.loc[i0, "latitude_конеч"] - group_data.loc[i1, "latitude_нач"]) ** 2 +
                (group_data.loc[i0, "longitude_конеч"] - group_data.loc[i1, "longitude_нач"]) ** 2
            )
            if dist < threshold:
                t0 = group_data.loc[i0, "Конец"]
                t1 = group_data.loc[i1, "Начало"]
                if pd.notnull(t0) and pd.notnull(t1):
                    delta = (t1 - t0).total_seconds()
                    if delta > 0:
                        df_copy.at[i0, "dwelling_time"] = delta
    return df_copy

df_time = calculate_time_spent(filtered_df)
df_time_sum = df_time.groupby(["latitude_конеч", "longitude_конеч"], dropna=False)["dwelling_time"].sum().reset_index()


heat_points = []
for _, row in df_time_sum.iterrows():
    lat = row["latitude_конеч"]
    lon = row["longitude_конеч"]
    val = row["dwelling_time"]
    if pd.notnull(lat) and pd.notnull(lon) and val > 0:
        # Обрезаем снизу и сверху
        # Нормируем в [0..1]:
        heat_points.append([lat, lon, 1])


# --------------------------------------------------------------------------------
# 5. Слой "Остановки" (детальные точки с popup)
# --------------------------------------------------------------------------------
detailed_events = df_time[df_time["dwelling_time"] > 0].copy()
detailed_events["Прибытие"] = detailed_events["Конец"] + pd.to_timedelta(detailed_events["dwelling_time"], unit="s")
detailed_events["Конец_str"] = detailed_events["Конец"].dt.strftime("%Y-%m-%d %H:%M:%S")
detailed_events["Прибытие_str"] = detailed_events["Прибытие"].dt.strftime("%Y-%m-%d %H:%M:%S")

markers_js = ""
for _, row in detailed_events.iterrows():
    lat = row["latitude_конеч"]
    lon = row["longitude_конеч"]
    agent = row["Группировка"]
    popup_text = (
        f"Агент: {agent}<br>"
        f"Прибытие: {row['Конец_str']}<br>"
        f"Отъезд: {row['Прибытие_str']}"
    )
    if pd.notnull(lat) and pd.notnull(lon):
        popup_text_escaped = popup_text.replace("'", "\\'")
        markers_js += (
            f"L.circleMarker([{lat}, {lon}], "
            "{radius: 3, color: 'purple', fillOpacity: 0.8})"
            f".bindPopup('{popup_text_escaped}')"
            ".addTo(markerLayer);\n"
        )

# --------------------------------------------------------------------------------
# 6. Загрузка geojson: границы регионов и населённые пункты
# --------------------------------------------------------------------------------
with open("geoBoundaries-KAZ-ADM2.geojson", "r", encoding="utf-8") as f:
    region_geojson = json.load(f)
region_geojson_str = json.dumps(region_geojson)

with open("hotosm_kaz_populated_places_points_geojson.geojson", "r", encoding="utf-8") as f:
    city_geojson = json.load(f)

city_markers_js = ""
for feature in city_geojson.get("features", []):
    geom = feature.get("geometry", {})
    props = feature.get("properties", {})
    if not geom or not props:
        continue
    if geom.get("type") == "Point" and "coordinates" in geom:
        lon, lat = geom["coordinates"]
        name = props.get("name") or "Без названия"
        name_escaped = name.replace("'", "\\'")
        city_markers_js += (
            f"var marker = L.marker([{lat}, {lon}]).bindPopup('{name_escaped}');\n"
            "cityMarkerCluster.addLayer(marker);\n"
        )

# --------------------------------------------------------------------------------
# 7. Генерация итогового HTML + JS (указываем max: 1.0)
# --------------------------------------------------------------------------------
html_template = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8" />
    <title>Leaflet HeatMap</title>

    <!-- Leaflet CSS -->
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.7.1/dist/leaflet.css" />

    <!-- Маркер-кластер плагин -->
    <link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.4.1/dist/MarkerCluster.css" />
    <link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.4.1/dist/MarkerCluster.Default.css" />

    <style>
        #mapid {{
            height: 800px;
            width: 100%;
        }}
    </style>
</head>
<body>
    <div id='mapid'></div>

    <!-- Leaflet JS -->
    <script src="https://unpkg.com/leaflet@1.7.1/dist/leaflet.js"></script>

    <!-- Маркер-кластер плагин -->
    <script src="https://unpkg.com/leaflet.markercluster@1.4.1/dist/leaflet.markercluster.js"></script>

    <!-- Leaflet-heat плагин -->
    <script src="https://unpkg.com/leaflet.heat/dist/leaflet-heat.js"></script>

    <script>
        // Инициализация карты
        var map = L.map('mapid').setView([48.0, 68.0], 5);

        // Подложка
        L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}.png', {{
            attribution: '&copy; OpenStreetMap contributors'
        }}).addTo(map);

        // ----- Слой "Границы регионов" -----
        var regionGeoJson = {region_geojson_str};
        var regionLayer = L.geoJson(regionGeoJson, {{
            style: function(feature) {{
                return {{
                    color: 'black',
                    weight: 1,
                    fillOpacity: 0
                }};
            }}
        }});

        // ----- Слой "Теплокарта" -----
        var heatData = {json.dumps(heat_points)};
        var heatLayer = L.heatLayer(heatData, {{
            radius: 20,
            blur: 10,
            maxZoom: 10,
        }});

        // ----- Слой "Остановки" (dwelling_time) -----
        var markerLayer = L.layerGroup();
        {markers_js}

        // ----- Слой "Населённые пункты" (кластер) -----
        var cityMarkerCluster = L.markerClusterGroup();
        {city_markers_js}

        // Собираем оверлеи
        var baseMaps = {{}};
        var overlayMaps = {{
            "Границы регионов": regionLayer,
            "Населённые пункты (кластер)": cityMarkerCluster,
            "Теплокарта (dwelling_time)": heatLayer,
            "Остановки (подробно)": markerLayer
        }};

        // Добавляем контрол переключения слоёв
        L.control.layers(baseMaps, overlayMaps, {{collapsed: false}}).addTo(map);

        // По умолчанию включаем некоторые слои
        regionLayer.addTo(map);
        heatLayer.addTo(map);
    </script>
</body>
</html>
"""

# --------------------------------------------------------------------------------
# 8. Отображение карты через Streamlit
# --------------------------------------------------------------------------------
st.components.v1.html(html_template, height=800, width=1400)
