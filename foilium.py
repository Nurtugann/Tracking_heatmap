import streamlit as st
import requests
import json
import datetime
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
import re
import io

st.cache_data.clear()
st.set_page_config(layout="wide")
st.title("🚗 Карта трека + 📊 Отчёты + 🗺️ Переходы регионов (по нескольким юнитам)")

# Константы
TOKEN = "c611c2bab48335e36a4b59be460c57d2DC99601D0C49777B24DFE07B7614A2826A62C393"
BASE_URL = "https://hst-api.wialon.host/wialon/ajax.html"
REGIONS_GEOJSON = "OSMB-f1ec2d0019a5c0c4984f489cdc13d5d26a7949fd.geojson"
CITIES_GEOJSON = "hotosm_kaz_populated_places_points_geojson.geojson"

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
# Пользователь выбирает юниты для отображения карты и отчетов (основной блок)
selected_units = st.multiselect("Выберите юниты (для отчётов и карты):", list(unit_dict))
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
    Здесь прибавляем +5 часов к значению времени (UTC -> местное).
    Это значение используется для вычисления переходов между регионами.
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
    """
    Оптимизированная функция определения переходов между регионами с использованием spatial join.
    Если в GeoDataFrame отсутствует столбец "shapeName", он создаётся на основе "name".
    При формировании итогового времени к нему прибавляется +4.99 часов.
    """
    if not points:
        return []
    
    df = pd.DataFrame(points)
    try:
        df["datetime"] = pd.to_datetime(df["time"], format="%Y-%m-%d %H:%M:%S")
    except Exception as e:
        st.warning(f"Ошибка преобразования времени: {e}")
        df["datetime"] = pd.to_datetime(df["time"], errors='coerce')
    
    df["geometry"] = df.apply(lambda row: Point(row["lon"], row["lat"]), axis=1)
    gdf_points = gpd.GeoDataFrame(df, geometry="geometry", crs="EPSG:4326")
    
    with open(regions_geojson_path, "r", encoding="utf-8") as f:
        regions_geojson = json.load(f)
    gdf_regions = gpd.GeoDataFrame.from_features(regions_geojson["features"])
    gdf_regions.crs = "EPSG:4326"
    
    if "shapeName" not in gdf_regions.columns:
        if "name" in gdf_regions.columns:
            gdf_regions["shapeName"] = gdf_regions["name"]
        else:
            gdf_regions["shapeName"] = ""
    
    gdf_joined = gpd.sjoin(
        gdf_points,
        gdf_regions[['geometry', 'shapeName']],
        how="left",
        predicate='within'
    )
    
    gdf_joined["region"] = gdf_joined["shapeName"]
    gdf_joined = gdf_joined.sort_values("datetime").reset_index(drop=True)
    gdf_joined["prev_region"] = gdf_joined["region"].shift()
    crossings = gdf_joined[gdf_joined["region"] != gdf_joined["prev_region"]].iloc[1:]
    if crossings.empty:
        return []
    
    crossings_list = list(crossings.apply(lambda row: {
        "from_region": row["prev_region"],
        "to_region": row["region"],
        "time": (row["datetime"] + datetime.timedelta(hours=4.99)).strftime("%Y-%m-%d %H:%M:%S"),
        "lat": row["lat"],
        "lon": row["lon"]
    }, axis=1))
    
    return crossings_list



def create_departure_report(unit_dict, units_to_process, SID, regions_geojson_path, responsible_regions=None):
    results = []
    
    # Загрузка GeoJSON и создание GeoDataFrame для регионов
    with open(regions_geojson_path, "r", encoding="utf-8") as f:
        regions_geojson = json.load(f)
    gdf_regions = gpd.GeoDataFrame.from_features(regions_geojson["features"])
    gdf_regions.crs = "EPSG:4326"
    if "shapeName" not in gdf_regions.columns:
        gdf_regions["shapeName"] = gdf_regions.get("name", "")
    
    progress_text = "🔄 Обработка юнитов..."
    my_bar = st.progress(0, text=progress_text)
    total_units = len(units_to_process)

    for i, unit_name in enumerate(units_to_process, start=1):
        unit_id = unit_dict[unit_name]
        track = get_track(SID, unit_id)

        if not track:
            results.append({
                "Юнит": unit_name,
                "Домашний регион": None,
                "Время выезда с региона": None,
                "Статус": "Нет данных по треку",
                "Вернулся в регион": None,
                "Время возвращения в регион": None,
                "Комментарий по регионам": "Нет данных по треку"
            })
            my_bar.progress(i / total_units, text=f"{unit_name} — нет данных")
            continue

        # Определение домашнего региона по первой точке
        df_first = pd.DataFrame([track[0]])
        df_first["geometry"] = df_first.apply(lambda row: Point(row["lon"], row["lat"]), axis=1)
        gdf_first = gpd.GeoDataFrame(df_first, geometry="geometry", crs="EPSG:4326")
        gdf_first_joined = gpd.sjoin(gdf_first, gdf_regions[['geometry', 'shapeName']], how="left", predicate="within")
        home_region = gdf_first_joined.iloc[0]["shapeName"] if not gdf_first_joined.empty else None

        # Получение переходов между регионами
        crossings = detect_region_crossings(track, regions_geojson_path)
        departure_event = None
        return_time = None
        returned_home = None

        if crossings:
            for idx, event in enumerate(crossings):
                if event["from_region"] == home_region and not departure_event:
                    departure_event = event
                    break

            if departure_event:
                after_departure = crossings[idx + 1:]
                return_indices = [j for j, e in enumerate(after_departure) if e["to_region"] == home_region]
                if return_indices:
                    last_return_idx = return_indices[-1]
                    return_event = after_departure[last_return_idx]

                    after_return = after_departure[last_return_idx + 1:]
                    left_again = any(e["from_region"] == home_region for e in after_return)

                    if not left_again:
                        returned_home = True
                        return_time = return_event["time"]

        # 💡 Анализ ответственных регионов
        visited_regions = set(e["to_region"] for e in crossings if e["to_region"])
        responsible = set(responsible_regions.get(unit_name, [])) if responsible_regions else set()
        # Словарь: регион → время первого входа
        first_entry_times = {}
        for event in crossings:
            region = event["to_region"]
            if region in responsible and region not in first_entry_times:
                first_entry_times[region] = event["time"]

        # Преобразуем в читаемый формат
        entry_times_str = '\n'.join(f"{r}: {pd.to_datetime(t).strftime('%H:%M:%S')}" for r, t in first_entry_times.items())


        visited_resp = responsible & visited_regions
        not_visited_resp = responsible - visited_regions

        def format_regions(region_set):
            return ', '.join(sorted(str(r) for r in region_set if pd.notna(r))) 


        if not responsible:
            region_comment = "❔ Нет назначенных регионов"
        elif not visited_resp:
            region_comment = "❌ Ни один ответственный регион не посещён"
        elif not_visited_resp:
            region_comment = (
                f"✅ Посетил: {format_regions(visited_resp)} | ❌ Не посетил: {format_regions(not_visited_resp)}"
            )
        else:
            region_comment = f"✅ Посетил все регионы: {format_regions(visited_resp)}"


        
        results.append({
            "Юнит": unit_name,
            "Домашний регион": home_region,
            "Время выезда с региона": departure_event["time"] if departure_event else None,
            "Статус": "Выехал" if departure_event else "Еще не выехал",
            "Вернулся в регион": returned_home if departure_event else None,
            "Время возвращения в регион": return_time if returned_home else None,
            "Первый заезд в назначенные регионы": entry_times_str,
            "Комментарий по регионам": region_comment
        })

        my_bar.progress(i / total_units, text=f"{unit_name} ✅")

    my_bar.empty()
    return pd.DataFrame(results)

# Чтение GeoJSON для регионов и пунктов населения (для карты и отчетов)
with open(REGIONS_GEOJSON, "r", encoding="utf-8") as f:
    regions_geojson_str = json.dumps(json.load(f))
with open(CITIES_GEOJSON, "r", encoding="utf-8") as f:
    cities_geojson_str = json.dumps(json.load(f))

# ------------- Блок для выбранных юнитов: отчеты, карта, и переходы -------------
if st.button("🚀 Запустить отчёты и карту для выбранных юнитов"):
    for unit_name in selected_units:
        st.markdown(f"## 🚘 Юнит: {unit_name}")
        unit_id = unit_dict[unit_name]
        report_result = execute_report(SID, res["id"], tpl_id, unit_id)
        detailed_points = get_track(SID, unit_id)
        coords = [[p["lat"], p["lon"]] for p in detailed_points]
        last = coords[-1] if coords else None
        
        crossings = detect_region_crossings(detailed_points, REGIONS_GEOJSON)
        if crossings:
            st.subheader("⛳ Переходы между регионами")
            df_crossings = pd.DataFrame(crossings)
            df_crossings["unit"] = unit_name
            st.dataframe(df_crossings)
        else:
            st.info("Нет переходов найдено.")
        
        if "reportResult" in report_result:
            for table_index, table in enumerate(report_result["reportResult"]["tables"]):
                if table["name"] not in ["unit_trips", "unit_trace"]:
                    continue
                row_count = table["rows"]
                headers = table["header"]
                data = get_result_rows(SID, table_index, row_count)
                parsed_rows = []
                for row_obj in data:
                    line = []
                    for cell in row_obj["c"]:
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
                df["Начало"] = pd.to_datetime(df["Grouping"].astype(str) + " " + df["Начало"].astype(str),
                                              format="%Y-%m-%d %H:%M:%S") + pd.Timedelta(hours=5)
                df["Конец"] = pd.to_datetime(df["Grouping"].astype(str) + " " + df["Конец"].astype(str),
                                              format="%Y-%m-%d %H:%M:%S") + pd.Timedelta(hours=5)
                df["Начало"] = df["Начало"].dt.strftime("%H:%M:%S")
                df["Конец"] = df["Конец"].dt.strftime("%H:%M:%S")
                df.rename(columns={"Grouping": "День"}, inplace=True)
                st.markdown(f"### 📋 Таблица поездок (или trace) для {unit_name}")
                st.dataframe(df, use_container_width=True)
        else:
            st.warning("❌ Ошибка в отчёте")
            st.json(report_result)
        
        # --- Карта ---
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
                    if (feature.properties) {{
                        var regionName = feature.properties.shapeName || feature.properties.name;
                        if (regionName) {{
                            layer.bindTooltip(regionName, {{
                                permanent: true,
                                direction: 'center',
                                className: 'region-label'
                            }});
                        }}
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
        """, height=600)

if st.button("📤 Сформировать отчёт по выезду из домашнего региона (Для всех) (Excel + таблицы)"):

    df = pd.read_csv("manager_region.csv")

    responsible_regions = (
        df.groupby("Car_numb")["Region_mapped"]
        .apply(lambda x: list(set(x)))
        .to_dict()
    )

    with st.spinner("Генерация отчёта..."):
        report_df = create_departure_report(unit_dict, list(unit_dict.keys()), SID, REGIONS_GEOJSON, responsible_regions)
        
        # Таблица 1: те, кто ещё не выехал
        not_departed_df = report_df[report_df["Статус"] == "Еще не выехал"]
        departed_df = report_df[report_df["Статус"] == "Выехал"]

        if not not_departed_df.empty:
            st.subheader("🚫 Ещё не выехали из домашнего региона:")
            st.dataframe(not_departed_df.reset_index(drop=True), use_container_width=True)
        else:
            st.info("✅ Все юниты выехали из своих домашних регионов.")

        if not departed_df.empty:
            st.subheader("✅ Уже выехали из домашнего региона:")
            st.dataframe(departed_df.reset_index(drop=True), use_container_width=True)
        else:
            st.info("🚫 Никто не выехал из домашнего региона.")

        # Скачивание Excel с двумя таблицами
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            not_departed_df.to_excel(writer, sheet_name="Еще не выехал", index=False)
            departed_df.to_excel(writer, sheet_name="Уже выехал", index=False)
        excel_data = output.getvalue()

        st.download_button(
            label="📥 Скачать Excel-отчет (2 листа)",
            data=excel_data,
            file_name="departure_report_full.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
