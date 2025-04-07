import streamlit as st
import requests
import json
import pandas as pd
import datetime
import geopandas as gpd
from shapely.geometry import Point

st.set_page_config(layout="wide")
st.title("🚗 Поездки, переходы и карта трека (ресурс 36516)")

TOKEN = "c611c2bab48335e36a4b59be460c57d2DC99601D0C49777B24DFE07B7614A2826A62C393"
BASE_URL = "https://hst-api.wialon.host/wialon/ajax.html"

RESOURCE_ID = 36516  # <-- Жёстко прописываем ваш ресурс, как в Playground

@st.cache_data
def login(token):
    params = {"svc": "token/login", "params": json.dumps({"token": token})}
    resp = requests.get(BASE_URL, params=params).json()
    return resp.get("eid")

SID = login(TOKEN)

@st.cache_data
def get_units(sid):
    """Все юниты (avl_unit)."""
    params = {
        "svc": "core/search_items",
        "params": json.dumps({
            "spec": {
                "itemsType": "avl_unit",
                "propName": "sys_name",
                "propValueMask": "*",
                "sortType": "sys_name",
                "propType": "property"
            },
            "force": 1,
            "flags": 1,
            "from": 0,
            "to": 0
        }),
        "sid": sid
    }
    data = requests.get(BASE_URL, params=params).json()
    return data.get("items", [])

units = get_units(SID)
if not units:
    st.error("Нет доступных юнитов. Проверьте токен или права.")
    st.stop()

# Выбираем юнит
unit_dict = {u["nm"]: u["id"] for u in units}
unit_name = st.selectbox("Юнит:", list(unit_dict.keys()))
unit_id = unit_dict[unit_name]

# Дата
today = datetime.date.today()
date_range = st.date_input("Период (начало - конец)", (today - datetime.timedelta(days=1), today))
if isinstance(date_range, tuple):
    date_from, date_to = date_range
else:
    date_from = date_to = date_range

def get_track_points(sid, unit_id, d_from, d_to):
    """Сообщения (точки трека) за период."""
    from_ts = int(datetime.datetime.combine(d_from, datetime.time.min).timestamp())
    to_ts = int(datetime.datetime.combine(d_to, datetime.time.max).timestamp())
    req = {
        "svc": "messages/load_interval",
        "params": json.dumps({
            "itemId": unit_id,
            "timeFrom": from_ts,
            "timeTo": to_ts,
            "flags": 0x1,       # подгружаем геопозиции
            "flagsMask": 0,
            "loadCount": 0xffffffff
        }),
        "sid": sid
    }
    resp = requests.get(BASE_URL, params=req).json()
    points = []
    for msg in resp.get("messages", []):
        pos = msg.get("pos")
        if pos:
            points.append({
                "lat": pos["y"],
                "lon": pos["x"],
                "time": msg["t"],
                "spd": msg.get("spd", 0)
            })
    return points

def exec_trips_report_like_playground(sid, resource_id, unit_id, d_from, d_to):
    """
    Точно копируем логику Playground:
    - Шаблон "unit_trips" создаётся "на лету" (id=0)
    - reportResourceId = 36516
    - Те же столбцы (begin, location, end, location2, duration, foll_dur, mileage, mileage2)
    """
    from_ts = int(datetime.datetime.combine(d_from, datetime.time.min).timestamp())
    to_ts = int(datetime.datetime.combine(d_to, datetime.time.max).timestamp())

    columns = [
        "begin", "location", "end", "location2",
        "duration", "foll_dur", "mileage", "mileage2"
    ]
    template = {
        "id": 0,
        "n": "unit_trips",   # Имя шаблона
        "ct": "avl_unit",
        "p": "",
        "tbl": [{
            "n": "unit_trips",
            "l": "Trips",
            "c": ",".join(columns),
            "cl": "",
            "s": "",
            "sl": "",
            "p": "",
            "sch": {"f1":0, "f2":0, "t1":0, "t2":0, "m":0, "y":0, "w":0},
            "f": 0
        }]
    }

    interval = {"from": from_ts, "to": to_ts, "flags": 0}

    req = {
        "svc": "report/exec_report",
        "params": json.dumps({
            "reportResourceId": resource_id,
            "reportTemplate": template,
            "reportObjectId": unit_id,
            "reportObjectSecId": 0,
            "interval": interval
        }),
        "sid": sid
    }
    resp = requests.get(BASE_URL, params=req).json()
    return resp

def parse_trips_report(sid, report_data):
    """Парсим результат exec_report. Если нет 'reportResult', показываем JSON."""
    if "reportResult" not in report_data:
        st.warning("❌ Нет данных по поездкам. Ответ от сервера:")
        st.json(report_data)
        return

    tables = report_data["reportResult"].get("tables", [])
    if not tables:
        st.info("Отчёт выполнен, но таблиц нет.")
        return

    table = tables[0]
    row_count = table["rows"]
    headers = table["header"]

    if row_count == 0:
        st.info("Нет данных по поездкам за этот период.")
        return

    # Загружаем строки
    req_rows = {
        "svc": "report/get_result_rows",
        "params": json.dumps({
            "tableIndex": 0,
            "indexFrom": 0,
            "indexTo": row_count
        }),
        "sid": sid
    }
    row_data = requests.get(BASE_URL, params=req_rows).json()

    rows = row_data["rows"] if isinstance(row_data, dict) and "rows" in row_data else row_data
    if not rows:
        st.warning("Строки не загружены.")
        return

    parsed_rows = []
    for r in rows:
        row_cells = []
        for c in r["c"]:
            val = c["t"] if isinstance(c, dict) and "t" in c else c
            # Попытка парсинга даты +5ч
            if isinstance(val, str) and "-" in val and ":" in val:
                try:
                    dt = datetime.datetime.strptime(val, "%Y-%m-%d %H:%M:%S") + datetime.timedelta(hours=5)
                    val = dt.strftime("%Y-%m-%d %H:%M:%S")
                except:
                    pass
            row_cells.append(val)
        parsed_rows.append(row_cells)

    df = pd.DataFrame(parsed_rows, columns=headers)
    st.subheader("📄 Поездки (Trips)")
    st.dataframe(df, use_container_width=True)

def detect_region_crossings(points, geojson_path):
    """Определяем переходы между регионами."""
    if not points:
        return []
    df = pd.DataFrame(points)
    df["datetime"] = pd.to_datetime(df["time"], unit="s") + pd.Timedelta(hours=5)
    df["geometry"] = df.apply(lambda x: Point(x["lon"], x["lat"]), axis=1)

    regions = gpd.read_file(geojson_path)
    gdf = gpd.GeoDataFrame(df, geometry="geometry", crs=regions.crs)

    def find_region(pt):
        for _, reg in regions.iterrows():
            if reg["geometry"].contains(pt):
                return reg["shapeName"]
        return None

    gdf["region"] = gdf["geometry"].apply(find_region)
    gdf = gdf.sort_values("datetime")

    transitions = []
    prev = None
    for _, row in gdf.iterrows():
        region = row["region"]
        if region != prev and prev is not None:
            transitions.append({
                "from_region": prev,
                "to_region": region,
                "transition_time": row["datetime"].strftime("%Y-%m-%d %H:%M:%S"),
                "lat": row["lat"],
                "lon": row["lon"]
            })
        prev = region
    return transitions

# Загружаем файлы GeoJSON
with open("geoBoundaries-KAZ-ADM2.geojson", "r", encoding="utf-8") as f:
    region_geojson_str = json.dumps(json.load(f))
with open("hotosm_kaz_populated_places_points_geojson.geojson", "r", encoding="utf-8") as f:
    city_geojson_str = json.dumps(json.load(f))

if st.button("📍 Показать"):
    # 1) Выполнить отчёт (Trips) с resource_id=36516
    report_data = exec_trips_report_like_playground(SID, RESOURCE_ID, unit_id, date_from, date_to)
    parse_trips_report(SID, report_data)

    # 2) Трек
    track_points = get_track_points(SID, unit_id, date_from, date_to)

    # 3) Переходы регионов
    crossing_list = detect_region_crossings(track_points, "geoBoundaries-KAZ-ADM2.geojson")
    if crossing_list:
        st.subheader("🧭 Переходы между регионами")
        st.dataframe(pd.DataFrame(crossing_list), use_container_width=True)

    # 4) Карта
    coords = [[p["lat"], p["lon"]] for p in track_points]
    last = coords[-1] if coords else None

    html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8" />
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.7.1/dist/leaflet.css"/>
    <link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.4.1/dist/MarkerCluster.css"/>
    <style>
      #map {{ height: 600px; }}
      .region-label {{
        font-size: 14px;
        font-weight: bold;
        color: #333;
      }}
    </style>
</head>
<body>
<div id="map"></div>

<script src="https://unpkg.com/leaflet@1.7.1/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet.markercluster@1.4.1/dist/leaflet.markercluster.js"></script>
<script>
  var map = L.map('map').setView([48, 68], 6);
  L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png').addTo(map);

  var coords = {json.dumps(coords)};
  var lastPt = {json.dumps(last)};

  if(coords.length > 0){{
    var poly = L.polyline(coords, {{color: 'red'}}).addTo(map);
    map.fitBounds(poly.getBounds());
    if(lastPt){{
      var carIcon = L.icon({{
        iconUrl: "https://cdn-icons-png.flaticon.com/512/854/854866.png",
        iconSize: [32,32],
        iconAnchor: [16,16]
      }});
      var marker = L.marker(lastPt, {{icon: carIcon}}).addTo(map);
      marker.bindPopup("🚗 Последняя точка трека").openPopup();
    }}
  }}

  // Слой регионов
  var regionLayer = L.geoJSON({region_geojson_str}, {{
    style: {{color:'black',weight:1,fillOpacity:0}},
    onEachFeature: function(f, layer){{
      if(f.properties && f.properties.shapeName){{
        layer.bindTooltip(f.properties.shapeName,{{
          permanent:true, direction:'center', className:'region-label'
        }});
      }}
    }}
  }}).addTo(map);

  // Слой городов (кластер)
  var cityLayer = L.markerClusterGroup();
  L.geoJSON({city_geojson_str}, {{
    pointToLayer: function(f, latlng){{
      return L.marker(latlng).bindPopup(f.properties.name||"Без названия");
    }}
  }}).addTo(cityLayer);
  cityLayer.addTo(map);

  // Переключатель
  L.control.layers(null, {{
    "Регионы": regionLayer,
    "Города": cityLayer
  }}, {{collapsed:false}}).addTo(map);
</script>
</body>
</html>
"""
    st.components.v1.html(html, height=650, scrolling=False)
