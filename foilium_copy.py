import streamlit as st
import requests
import json
import pandas as pd
import datetime

st.set_page_config(layout="wide")
st.title("🚗 Карта трека + 📊 Отчёт из Wialon (с GeoJSON)")

TOKEN = "c611c2bab48335e36a4b59be460c57d2DC99601D0C49777B24DFE07B7614A2826A62C393"
BASE_URL = "https://hst-api.wialon.host/wialon/ajax.html"

@st.cache_data
def login(token):
    params = {"svc": "token/login", "params": json.dumps({"token": token})}
    return requests.get(BASE_URL, params=params).json().get("eid")

SID = login(TOKEN)

@st.cache_data
def get_items(sid, item_type, flags):
    params = {
        "svc": "core/search_items",
        "params": json.dumps({
            "spec": {
                "itemsType": item_type,
                "propName": "sys_name",
                "propValueMask": "*",
                "sortType": "sys_name",
                "propType": "property"
            },
            "force": 1,
            "flags": flags,
            "from": 0,
            "to": 0
        }),
        "sid": sid
    }
    return requests.get(BASE_URL, params=params).json().get("items", [])

units = get_items(SID, "avl_unit", 1)
resources = get_items(SID, "avl_resource", 8193)

if not units or not resources:
    st.warning("Нет данных для отображения.")
    st.stop()

unit_dict = {u["nm"]: u["id"] for u in units}
res = resources[0]
tpl_id = list(res["rep"].values())[0]["id"]

unit_name = st.selectbox("Юнит:", list(unit_dict.keys()))
unit_id = unit_dict[unit_name]

today = datetime.date.today()
date_range = st.date_input("Период для отчета и трека", (today - datetime.timedelta(days=1), today))
if isinstance(date_range, tuple):
    date_from, date_to = date_range
else:
    date_from = date_to = date_range

def get_unit_track(sid, unit_id, date_from, date_to):
    from_ts = int(datetime.datetime.combine(date_from, datetime.time.min).timestamp())
    to_ts = int(datetime.datetime.combine(date_to, datetime.time.max).timestamp())
    params = {
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
    }
    data = requests.get(BASE_URL, params=params).json()
    coords = [
        [m["pos"]["y"], m["pos"]["x"]] for m in data.get("messages", []) if m.get("pos")
    ]
    return coords

def execute_report(sid, res_id, tpl_id, unit_id, from_ts, to_ts):
    params = {
        "svc": "report/exec_report",
        "params": json.dumps({
            "reportResourceId": res_id,
            "reportTemplateId": tpl_id,
            "reportObjectId": unit_id,
            "reportObjectSecId": 0,
            "interval": {"from": from_ts, "to": to_ts, "flags": 0}
        }),
        "sid": sid
    }
    return requests.get(BASE_URL, params=params).json()

# Загрузка файлов GeoJSON (убедитесь, что файлы лежат в той же папке или укажите верный путь)
with open("geoBoundaries-KAZ-ADM2.geojson", "r", encoding="utf-8") as f:
    regions_geojson_str = json.dumps(json.load(f))

with open("hotosm_kaz_populated_places_points_geojson.geojson", "r", encoding="utf-8") as f:
    cities_geojson_str = json.dumps(json.load(f))

if st.button("📥 Выполнить"):
    from_ts = int(datetime.datetime.combine(date_from, datetime.time.min).timestamp())
    to_ts = int(datetime.datetime.combine(date_to, datetime.time.max).timestamp())

    report_result = execute_report(SID, res["id"], tpl_id, unit_id, from_ts, to_ts)
    coords = get_unit_track(SID, unit_id, date_from, date_to)
    last_point = coords[-1] if coords else None

    if "reportResult" in report_result:
        for table_index, table in enumerate(report_result["reportResult"]["tables"]):
            st.subheader(table["label"])
            row_count = table["rows"]
            row_resp = requests.get(BASE_URL, params={
                "svc": "report/get_result_rows",
                "params": json.dumps({
                    "tableIndex": table_index,
                    "indexFrom": 0,
                    "indexTo": row_count
                }),
                "sid": SID
            }).json()

            # Проверяем, пришёл ли список или словарь
            if isinstance(row_resp, list):
                rows = row_resp
            elif isinstance(row_resp, dict) and "rows" in row_resp:
                rows = row_resp["rows"]
            else:
                st.error("❌ Ошибка при получении строк отчёта")
                st.json(row_resp)
                continue

            headers = table["header"]
            parsed_rows = []
            for row in rows:
                parsed_cells = []
                for cell in row["c"]:
                    if isinstance(cell, dict) and "t" in cell:
                        parsed_cells.append(cell["t"])
                    else:
                        parsed_cells.append(cell)
                parsed_rows.append(parsed_cells)

            df = pd.DataFrame(parsed_rows, columns=headers)
            st.dataframe(df, use_container_width=True)
    else:
        st.error("❌ Ошибка при выполнении отчёта")
        st.json(report_result)

    # Карта
    car_icon_url = "https://cdn-icons-png.flaticon.com/512/854/854866.png"
    coords_json = json.dumps(coords)
    last_point_json = json.dumps(last_point)

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8" />
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.7.1/dist/leaflet.css"/>
        <link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.4.1/dist/MarkerCluster.css"/>
        <style>#map {{ height: 600px; }}</style>
    </head>
    <body>
    <div id="map"></div>

    <script src="https://unpkg.com/leaflet@1.7.1/dist/leaflet.js"></script>
    <script src="https://unpkg.com/leaflet.markercluster@1.4.1/dist/leaflet.markercluster.js"></script>

    <script>
        var map = L.map('map').setView([48.0, 68.0], 6);
        L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png').addTo(map);

        var coords = {coords_json};
        var lastPoint = {last_point_json};

        if (coords.length > 0) {{
            var track = L.polyline(coords, {{color: 'red'}}).addTo(map);
            map.fitBounds(track.getBounds());

            if (lastPoint) {{
                var carIcon = L.icon({{
                    iconUrl: "{car_icon_url}",
                    iconSize: [32, 32],
                    iconAnchor: [16, 16]
                }});
                var marker = L.marker([lastPoint[0], lastPoint[1]], {{icon: carIcon}}).addTo(map);
                marker.bindPopup("🚗 Последняя точка трека").openPopup();
            }}
        }}

        var regionLayer = L.geoJSON({regions_geojson_str}, {{
            style: {{ color: 'black', weight: 1, fillOpacity: 0 }}
        }}).addTo(map);

        var cityCluster = L.markerClusterGroup();
        L.geoJSON({cities_geojson_str}, {{
            pointToLayer: function(feature, latlng) {{
                return L.marker(latlng).bindPopup(feature.properties.name || "Без названия");
            }}
        }}).addTo(cityCluster);
        cityCluster.addTo(map);

        var overlays = {{
            "Границы регионов": regionLayer,
            "Населённые пункты": cityCluster
        }};
        L.control.layers(null, overlays, {{collapsed: false}}).addTo(map);
    </script>
    </body>
    </html>
    """
    st.components.v1.html(html, height=650, scrolling=False)
