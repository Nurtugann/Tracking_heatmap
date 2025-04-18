# со всеми отчетами

import streamlit as st
import requests
import json
import datetime
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

st.set_page_config(layout="wide")
st.title("🚗 Карта трека + 📊 Отчёты + 🗺️ Переходы регионов (по нескольким юнитам)")

# Константы
TOKEN = "c611c2bab48335e36a4b59be460c57d2DC99601D0C49777B24DFE07B7614A2826A62C393"
BASE_URL = "https://hst-api.wialon.host/wialon/ajax.html"

# ===== Авторизация и загрузка данных =====
@st.cache_data
def login(token):
    r = requests.get(BASE_URL, params={"svc": "token/login", "params": json.dumps({"token": token})})
    return r.json().get("eid")

@st.cache_data
def get_items(sid, item_type, flags):
    r = requests.get(BASE_URL, params={
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
    })
    return r.json().get("items", [])

SID = login(TOKEN)
units = get_items(SID, "avl_unit", 1)
resources = get_items(SID, "avl_resource", 8193)

if not resources or not units:
    st.error("Нет ресурсов или юнитов.")
    st.stop()

unit_dict = {u["nm"]: u["id"] for u in units}
selected_units = st.multiselect("Выберите юниты:", list(unit_dict), default=list(unit_dict)[:1])

res = resources[0]
tpl_id = list(res["rep"].values())[0]["id"]

today = datetime.date.today()
date_range = st.date_input("Выберите диапазон дат", (today - datetime.timedelta(days=1), today))
if isinstance(date_range, tuple):
    date_from, date_to = date_range
else:
    date_from = date_to = date_range

from_ts = int(datetime.datetime.combine(date_from, datetime.time.min).timestamp())
to_ts = int(datetime.datetime.combine(date_to, datetime.time.max).timestamp())

# ===== Методы =====
def get_track(sid, unit_id):
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
    points = []
    for m in r.json().get("messages", []):
        if m.get("pos"):
            points.append({
                "lat": m["pos"]["y"],
                "lon": m["pos"]["x"],
                "time": m.get("t"),
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
    return r.json()

def detect_region_crossings(points, regions_geojson_path):
    if not points:
        return []
    df = pd.DataFrame(points)
    df["datetime"] = pd.to_datetime(df["time"], unit="s") + pd.Timedelta(hours=5)
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
                    "time": row["datetime"].strftime("%Y-%m-%d %H:%M:%S"),
                    "lat": row["lat"],
                    "lon": row["lon"]
                })
            prev = row["region"]
    return crossings

# ===== GeoJSON =====
with open("geoBoundaries-KAZ-ADM2.geojson", "r", encoding="utf-8") as f:
    regions_geojson_str = json.dumps(json.load(f))
with open("hotosm_kaz_populated_places_points_geojson.geojson", "r", encoding="utf-8") as f:
    cities_geojson_str = json.dumps(json.load(f))

# ===== Выполнение =====
if st.button("🚀 Запустить отчёты и карту"):
    for unit_name in selected_units:
        unit_id = unit_dict[unit_name]
        st.markdown(f"## 🚘 Юнит: {unit_name}")

        report_result = execute_report(SID, res["id"], tpl_id, unit_id)
        detailed_points = get_track(SID, unit_id)
        coords = [[p["lat"], p["lon"]] for p in detailed_points]
        last = coords[-1] if coords else None

        crossings = detect_region_crossings(detailed_points, "geoBoundaries-KAZ-ADM2.geojson")
        if crossings:
            st.subheader("⛳ Переходы между регионами")
            st.dataframe(pd.DataFrame(crossings))

        if "reportResult" in report_result:
            for table_index, table in enumerate(report_result["reportResult"]["tables"]):
                if table["name"] not in ["unit_generic", "unit_trips", "unit_stays"]:  # ← добавь нужные названия таблиц
                    continue

                st.write("Таблица:", table["name"])
                

                row_count = table["rows"]
                headers = table["header"]
                data = get_result_rows(SID, table_index, row_count)

                rows = data["rows"] if isinstance(data, dict) and "rows" in data else data
                parsed_rows = []
                for row in rows:
                    line = []
                    for cell in row["c"]:
                        val = cell["t"] if isinstance(cell, dict) and "t" in cell else cell
                        try:
                            dt = datetime.datetime.strptime(val, "%Y-%m-%d %H:%M:%S") + datetime.timedelta(hours=5)
                            val = dt.strftime("%Y-%m-%d %H:%M:%S")
                        except:
                            pass
                        line.append(val)
                    parsed_rows.append(line)

                df = pd.DataFrame(parsed_rows, columns=headers)
                st.markdown(f"### 📋 Таблица: {table['label']}")
                st.dataframe(df, use_container_width=True)
        else:
            st.warning("❌ Ошибка в отчёте")
            st.json(report_result)

        # Карта
        car_icon_url = "https://cdn-icons-png.flaticon.com/512/854/854866.png"
        coords_json = json.dumps(coords)
        last_point_json = json.dumps(last)

        html = f"""
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
                    L.marker([last[0], last[1]], {{icon: carIcon}}).addTo(map).bindPopup("🚗 Последняя точка");
                }}
            }}

            var regionLayer = L.geoJSON({regions_geojson_str}, {{
                style: {{ color: 'black', weight: 1, fillOpacity: 0 }},
                onEachFeature: function(feature, layer) {{
                    if (feature.properties && feature.properties.shapeName) {{
                        layer.bindTooltip(feature.properties.shapeName, {{
                            permanent: true, direction: 'center', className: 'region-label'
                        }});
                    }}
                }}
            }}).addTo(map);

            var cityCluster = L.markerClusterGroup();
            L.geoJSON({cities_geojson_str}, {{
                pointToLayer: function(feature, latlng) {{
                    return L.marker(latlng).bindPopup(feature.properties.name || "Без названия");
                }}
            }}).addTo(cityCluster);
            cityCluster.addTo(map);

            L.control.layers(null, {{
                "Границы регионов": regionLayer,
                "Города": cityCluster
            }}, {{collapsed: false}}).addTo(map);
        </script>
        """
        st.components.v1.html(f"""
        <html>
        <head>
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.7.1/dist/leaflet.css"/>
        <script src="https://unpkg.com/leaflet@1.7.1/dist/leaflet.js"></script>
        <script src="https://unpkg.com/leaflet.markercluster@1.4.1/dist/leaflet.markercluster.js"></script>
        </head>
        <body>{html}</body></html>
        """, height=400)

    # ===== Дополнительно: показать столбцы таблицы unit_trips =====
    st.markdown("### 📌 Доступные столбцы в таблице `unit_trips`")

    def get_table_columns(sid, resource_id, table_name="unit_trips"):
        r = requests.get(BASE_URL, params={
            "svc": "report/get_report_tables",
            "params": json.dumps({"resourceId": resource_id}),
            "sid": sid
        })
        all_tables = r.json()
        for table in all_tables:
            if table.get("n") == table_name:
                return table.get("col", [])
        return []

    columns = get_table_columns(SID, res["id"], "unit_trips")
    if columns:
        for col in columns:
            st.markdown(f"- `{col['n']}` — **{col['l']}**")
    else:
        st.warning("❌ Не удалось получить список столбцов.")
