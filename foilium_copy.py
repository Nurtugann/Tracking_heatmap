import streamlit as st
import requests
import json
import datetime
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
import re

st.cache_data.clear()
st.set_page_config(layout="wide")
st.title("🚗 Карта трека + 📊 Отчёты + 🗺️ Переходы регионов (по нескольким юнитам)")

# Константы
TOKEN = "c611c2bab48335e36a4b59be460c57d2DC99601D0C49777B24DFE07B7614A2826A62C393"
BASE_URL = "https://hst-api.wialon.host/wialon/ajax.html"

@st.cache_data
def login(token):
    r = requests.get(
        BASE_URL,
        params={
            "svc": "token/login",
            "params": json.dumps({"token": token})
        }
    )
    return r.json().get("eid")

@st.cache_data
def get_items(sid, item_type, flags):
    r = requests.get(
        BASE_URL,
        params={
            "svc": "core/search_items",
            "params": json.dumps({
                "spec": {
                    "itemsType": item_type,
                    "propName": "sys_name",
                    "propValueMask": "*",
                    "sortType": "sys_name"
                },
                "force": 1,
                "flags": flags,
                "from": 0,
                "to": 0
            }),
            "sid": sid
        }
    )
    return r.json().get("items", [])

# Авторизация и получение списка юнитов/ресурсов
SID = login(TOKEN)
units = get_items(SID, "avl_unit", 1)
resources = get_items(SID, "avl_resource", 8193)

if not resources or not units:
    st.error("Нет ресурсов или юнитов.")
    st.stop()

unit_dict = {u["nm"]: u["id"] for u in units}
# По умолчанию не выбираем ни один юнит
selected_units = st.multiselect("Выберите юниты:", list(unit_dict))

if not selected_units:
    st.warning("Пожалуйста, выберите хотя бы один юнит.")
    st.stop()

res = resources[0]
tpl_id = list(res["rep"].values())[0]["id"]

today = datetime.date.today()
selected_date = st.date_input("Выберите день", today)
date_from = date_to = selected_date

from_ts = int(datetime.datetime.combine(date_from, datetime.time.min).timestamp())
to_ts = int(datetime.datetime.combine(date_to, datetime.time.max).timestamp())

def get_track(sid, unit_id):
    """
    Получаем трек юнита через messages/load_interval.
    Здесь прибавляем +5 часов к значению времени (UTC -> местное)
    – это значение используется для вычисления переходов между регионами.
    """
    r = requests.get(BASE_URL, params={
        "svc": "messages/load_interval",
        "params": json.dumps({
            "itemId": unit_id,
            "timeFrom": from_ts,
            "timeTo": to_ts,
            "flags": 0x1,
            "flagsMask": 0,
            "loadCount": 0xffffffff
        }),
        "sid": sid
    })
    js = r.json()
    points = []
    for m in js.get("messages", []):
        if m.get("pos"):
            t = m.get("t")
            try:
                # Прибавляем +5 часов к времени из сообщений
                if isinstance(t, str):
                    dt = datetime.datetime.strptime(t, "%Y-%m-%d %H:%M:%S")
                else:
                    dt = datetime.datetime.fromtimestamp(t)
                t_local = (dt + datetime.timedelta(hours=0)).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                t_local = t
            points.append({
                "lat": m["pos"]["y"],
                "lon": m["pos"]["x"],
                "time": t_local,  # уже локальное время (UTC+5)
                "spd": m.get("spd", 0)
            })
    return points

def execute_report(sid, res_id, tpl_id, unit_id):
    r = requests.get(BASE_URL, params={
        "svc": "report/exec_report",
        "params": json.dumps({
            "reportResourceId": res_id,
            "reportTemplateId": tpl_id,
            "reportObjectId": unit_id,
            "reportObjectSecId": 0,
            "interval": {"from": from_ts, "to": to_ts, "flags": 0}
        }),
        "sid": sid
    })
    return r.json()

def get_result_rows(sid, table_index, row_count):
    r = requests.get(BASE_URL, params={
        "svc": "report/get_result_rows",
        "params": json.dumps({
            "tableIndex": table_index,
            "indexFrom": 0,
            "indexTo": row_count
        }),
        "sid": sid
    })
    data = r.json()
    if isinstance(data, dict) and "rows" in data:
        return data["rows"]
    elif isinstance(data, list):
        return data
    else:
        return []

def detect_region_crossings(points, regions_geojson_path):
    if not points:
        return []
    df = pd.DataFrame(points)
    # Здесь "time" уже строковое значение с прибавлением +5 часов (из get_track)
    try:
        df["datetime"] = pd.to_datetime(df["time"], format="%Y-%m-%d %H:%M:%S")
    except Exception as e:
        st.warning(f"Ошибка преобразования времени: {e}")
        df["datetime"] = pd.to_datetime(df["time"], errors='coerce')
    df["geometry"] = df.apply(lambda row: Point(row["lon"], row["lat"]), axis=1)
    regions = gpd.read_file(regions_geojson_path)
    gdf = gpd.GeoDataFrame(df, geometry="geometry", crs=regions.crs)
    def get_region(point):
        for _, reg in regions.iterrows():
            if reg["geometry"].contains(point):
                return reg["shapeName"]
        return None
    gdf["region"] = gdf["geometry"].apply(get_region)
    crossings = []
    prev = None
    for _, row in gdf.iterrows():
        if row["region"] != prev:
            if prev is not None:
                crossings.append({
                    "from_region": prev,
                    "to_region": row["region"],
                    "time": (row["datetime"] + datetime.timedelta(hours=2.5)).strftime("%Y-%m-%d %H:%M:%S"),
                    "lat": row["lat"],
                    "lon": row["lon"]
                })
            prev = row["region"]
    return crossings

with open("geoBoundaries-KAZ-ADM2.geojson", "r", encoding="utf-8") as f:
    regions_geojson_str = json.dumps(json.load(f))
with open("hotosm_kaz_populated_places_points_geojson.geojson", "r", encoding="utf-8") as f:
    cities_geojson_str = json.dumps(json.load(f))

if st.button("🚀 Запустить отчёты и карту"):
    # Встраиваем index.html (Wialon-репорт через JS) – там уже реализована обработка времени с +5 через adjustTime
    unit_ids = [unit_dict[name] for name in selected_units]
    units_json = json.dumps(unit_ids)
    with open("index.html", "r", encoding="utf-8") as f:
        html = f.read()
    injected_js = f"""
    <script>
    window.preselectedUnits = {units_json};
    </script>
    """
    st.markdown("🔽 Ниже откроется Wialon-репорт для выбора и запуска произвольных отчётов:")
    st.components.v1.html(html + injected_js, height=800, scrolling=True)

    for unit_name in selected_units:
        st.markdown(f"## 🚘 Юнит: {unit_name}")
        unit_id = unit_dict[unit_name]

        report_result = execute_report(SID, res["id"], tpl_id, unit_id)
        detailed_points = get_track(SID, unit_id)
        coords = [[p["lat"], p["lon"]] for p in detailed_points]
        last = coords[-1] if coords else None

        # Таблица переходов – данные уже содержат +5 часов (из get_track)
        crossings = detect_region_crossings(detailed_points, "geoBoundaries-KAZ-ADM2.geojson")
        if crossings:
            st.subheader("⛳ Переходы между регионами")
            st.dataframe(pd.DataFrame(crossings))

        # Обработка отчёта (для таблиц unit_trips и unit_trace)
        if "reportResult" in report_result:
            for table_index, table in enumerate(report_result["reportResult"]["tables"]):
                if table["name"] not in ["unit_trips", "unit_trace"]:
                    continue
                row_count = table["rows"]
                headers = table["header"]
                data = get_result_rows(SID, table_index, row_count)
                rows = data  # data уже список

                parsed_rows = []
                for row_obj in rows:
                    line = []
                    for cell in row_obj["c"]:
                        # Для отчётов предполагаем, что время из отчёта приходит в UTC,
                        # и здесь прибавляем +5 часов для получения местного времени.
                        if isinstance(cell, dict) and "t" in cell:
                            raw_val = cell["t"]
                        else:
                            raw_val = cell
                        if isinstance(raw_val, str) and re.match(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$', raw_val):
                            try:
                                dt = datetime.datetime.strptime(raw_val, "%Y-%m-%d %H:%M:%S") + datetime.timedelta(hours=5)
                                val = dt.strftime("%Y-%m-%d %H:%M:%S")
                            except Exception:
                                val = raw_val
                        elif isinstance(raw_val, (int, float)):
                            dt = datetime.datetime.fromtimestamp(raw_val) + datetime.timedelta(hours=5)
                            val = dt.strftime("%Y-%m-%d %H:%M:%S")
                        else:
                            val = raw_val
                        line.append(val)
                    parsed_rows.append(line)

                df = pd.DataFrame(parsed_rows, columns=headers)
                # Если в таблице отдельно заданы колонки "день" и "время", можно объединить их:
                df["Начало"] = pd.to_datetime(df["Grouping"].astype(str) + " " + df["Начало"].astype(str),
                                                    format="%Y-%m-%d %H:%M:%S") + pd.Timedelta(hours=5)
                df["Конец"] = pd.to_datetime(df["Grouping"].astype(str) + " " + df["Конец"].astype(str),
                                                    format="%Y-%m-%d %H:%M:%S") + pd.Timedelta(hours=5)
                df.drop('Grouping', axis=1, inplace=True)
                st.markdown(f"### 📋 Таблица поездок (или trace) для {unit_name}")
                st.dataframe(df, use_container_width=True)
        else:
            st.warning("❌ Ошибка в отчёте")
            st.json(report_result)

        # --- Карта с управляемыми слоями ---
        car_icon_url = "https://cdn-icons-png.flaticon.com/512/854/854866.png"
        coords_json = json.dumps(coords)
        last_point_json = json.dumps(last)
        map_html = f"""
        <div id="map_{unit_name}" style="height: 600px;"></div>
        <script>
            var map = L.map('map_{unit_name}').setView([48.0, 68.0], 6);
            L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png').addTo(map);
            var coords = {coords_json};
            var last = {last_point_json};
            if (coords.length > 0) {{
                var track = L.polyline(coords, {{color: 'red'}}).addTo(map);
                map.fitBounds(track.getBounds());
                if (last) {{
                    var carIcon = L.icon({{
                        iconUrl: "{car_icon_url}",
                        iconSize: [32, 32],
                        iconAnchor: [16, 16]
                    }});
                    L.marker([last[0], last[1]], {{icon: carIcon}}).addTo(map)
                        .bindPopup("🚗 Последняя точка");
                }}
            }}
            var regionsLayer = L.geoJSON({regions_geojson_str}, {{
                style: function(feature) {{
                    return {{ color: 'black', weight: 1, fillOpacity: 0 }};
                }},
                onEachFeature: function(feature, layer) {{
                    if (feature.properties && feature.properties.shapeName) {{
                        layer.bindTooltip(feature.properties.shapeName, {{
                            permanent: true,
                            direction: 'center',
                            className: 'region-label'
                        }});
                    }}
                }}
            }});
            var citiesLayer = L.geoJSON({cities_geojson_str}, {{
                pointToLayer: function(feature, latlng) {{
                    var marker = L.marker(latlng);
                    if (feature.properties && feature.properties.name) {{
                        marker.bindPopup(feature.properties.name);
                    }}
                    return marker;
                }}
            }});
            var cityCluster = L.markerClusterGroup();
            cityCluster.addLayer(citiesLayer);
            var overlays = {{
                "Границы регионов": regionsLayer,
                "Пункты населения": cityCluster
            }};
            L.control.layers(null, overlays, {{collapsed: false}}).addTo(map);
            regionsLayer.addTo(map);
            cityCluster.addTo(map);
        </script>
        <style>
            .region-label {{
                background-color: rgba(255, 255, 255, 0.7);
                border: none;
                font-size: 12px;
                padding: 2px;
            }}
            .city-label {{
                background-color: rgba(255, 255, 255, 0.7);
                border: none;
                font-size: 10px;
                padding: 2px;
            }}
        </style>
        """
        st.components.v1.html(f"""
        <html>
        <head>
            <link rel="stylesheet" href="https://unpkg.com/leaflet@1.7.1/dist/leaflet.css"/>
            <script src="https://unpkg.com/leaflet@1.7.1/dist/leaflet.js"></script>
            <script src="https://unpkg.com/leaflet.markercluster@1.4.1/dist/leaflet.markercluster.js"></script>
        </head>
        <body>{map_html}</body></html>
        """, height=800)
