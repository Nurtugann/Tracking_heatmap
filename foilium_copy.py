import streamlit as st

# Попытка очистить кеш, если возникнет ошибка — просто пропустим
try:
    st.cache_data.clear()
except Exception:
    pass

import requests
import json
import datetime
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
import re
import io

st.set_page_config(layout="wide")
st.title("🚗 Карта трека + 📊 Отчёты + 🗺️ Переходы регионов (по нескольким юнитам)")

# --- Константы ---
TOKEN = "c611c2bab48335e36a4b59be460c57d2BF8416B73C4A65F2B8A88A5848E97CD4471F14C6"
BASE_URL = "https://hst-api.wialon.host/wialon/ajax.html"
REGIONS_GEOJSON = "OSMB-f1ec2d0019a5c0c4984f489cdc13d5d26a7949fd.geojson"
CITIES_GEOJSON = "hotosm_kaz_populated_places_points_geojson.geojson"

def login(token):
    r = requests.get(
        BASE_URL,
        params={
            "svc": "token/login",
            "params": json.dumps({"token": token})
        }
    )
    return r.json().get("eid")

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

# --- Авторизация и получение списка юнитов/ресурсов ---
SID = login(TOKEN)
units = get_items(SID, "avl_unit", 1)
resources = get_items(SID, "avl_resource", 8193)
if not resources or not units:
    st.error("Нет ресурсов или юнитов.")
    st.stop()

unit_dict = {u["nm"]: u["id"] for u in units}
selected_units = st.multiselect("Выберите юниты (для отчётов и карты):", list(unit_dict))
if not selected_units:
    st.warning("Пожалуйста, выберите хотя бы один юнит.")
    st.stop()

res = resources[0]
tpl_id = list(res["rep"].values())[0]["id"]

# --- Блок выбора диапазона дат вместо одного дня ---
today = datetime.date.today()
selected_dates = st.date_input(
    "Выберите диапазон дат",
    value=(today, today),
    help="Для выбора периода: кликните дату, затем удерживайте Shift и выберите вторую дату"
)

if isinstance(selected_dates, tuple) and len(selected_dates) == 2:
    date_from, date_to = selected_dates
else:
    date_from = date_to = selected_dates

# --- Функции для работы с API и GIS ---

def get_track(sid, unit_id, day_from_ts, day_to_ts):
    """
    Получаем трек юнита через messages/load_interval за указанный день.
    Время возвращается в UTC; конверсия в локальное делается при отображении отчёта (+5 часов).
    """
    r = requests.get(BASE_URL, params={
        "svc": "messages/load_interval",
        "params": json.dumps({
            "itemId": unit_id,
            "timeFrom": day_from_ts,
            "timeTo": day_to_ts,
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
                # Сохраняем UTC-метку, без смещения
                utc_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                utc_str = t
            points.append({
                "lat": m["pos"]["y"],
                "lon": m["pos"]["x"],
                "time": utc_str,
                "spd": m.get("spd", 0)
            })
    return points

def execute_report(sid, res_id, tpl_id, unit_id, day_from_ts, day_to_ts):
    r = requests.get(BASE_URL, params={
        "svc": "report/exec_report",
        "params": json.dumps({
            "reportResourceId": res_id,
            "reportTemplateId": tpl_id,
            "reportObjectId": unit_id,
            "reportObjectSecId": 0,
            "interval": {"from": day_from_ts, "to": day_to_ts, "flags": 0}
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
    Время остаётся в UTC, без прибавления +5 часов. 
    Будем конвертировать в локальное только при отображении отчёта.
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
        # Сохраняем UTC-время без смещения
        "time": row["datetime"].strftime("%Y-%m-%d %H:%M:%S"),
        "lat": row["lat"],
        "lon": row["lon"]
    }, axis=1))
    
    return crossings_list

def compute_time_in_responsible_regions(crossings, start_of_day_ts, last_message_ts, responsible_set, initial_region):
    """
    По списку событий crossings (UTC-время) возвращаем словарь вида {region: total_seconds},
    где region берётся только из responsible_set.
    Учёт времени идёт до last_message_ts (UTC), не до конца суток.
    
    Если initial_region ∈ responsible_set, считаем, что юнит "вошёл" в него в start_of_day_ts.
    Каждое crossing с to_region=R фиксирует вход в R (UTC), с from_region=R фиксирует выход (UTC).
    Если после всех crossings юнит всё ещё внутри R, добавляем (last_message_ts - время входа).
    """
    entry_ts_map = {r: None for r in responsible_set}
    durations = {r: 0 for r in responsible_set}
    
    # Если в 00:00 UTC юнит уже в начальном регионе, и этот регион — ответственный,
    # считаем, что он "вошёл" в него ровно в start_of_day_ts.
    if initial_region in responsible_set:
        entry_ts_map[initial_region] = start_of_day_ts
    
    for ev in crossings:
        t_e = int(datetime.datetime.strptime(ev["time"], "%Y-%m-%d %H:%M:%S").timestamp())
        if t_e < start_of_day_ts or t_e > last_message_ts:
            continue
        
        r_to = ev["to_region"]
        r_from = ev["from_region"]
        
        # Заезд в ответственный регион r_to
        if r_to in responsible_set and entry_ts_map[r_to] is None:
            entry_ts_map[r_to] = t_e
        
        # Выезд из ответственного региона r_from
        if r_from in responsible_set and entry_ts_map[r_from] is not None:
            exit_ts = t_e
            durations[r_from] += (exit_ts - entry_ts_map[r_from])
            entry_ts_map[r_from] = None
    
    # После всех событий: если внутри какого-то r entry_ts_map[r] != None,
    # значит юнит остался в r до last_message_ts
    for r in responsible_set:
        if entry_ts_map[r] is not None:
            durations[r] += (last_message_ts - entry_ts_map[r])
            entry_ts_map[r] = None
    
    return durations

def create_departure_report(unit_dict, units_to_process, SID, regions_geojson_path, responsible_regions, day_from_ts, day_to_ts):
    """
    Возвращает DataFrame с колонками:
      ["Юнит", "Домашний регион", "Время выезда", "Статус",
       "Вернулся", "Время возвращения",
       "Первый въезд в ответственные регионы",
       "Комментарий по регионам",
       "Время в ответственных регионах"]
    за один день (day_from_ts .. day_to_ts), где все события хранятся в UTC.
    При отображении отчёта (+5 часов) таблицы преобразуют UTC → локальное.
    """
    results = []
    
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
        track = get_track(SID, unit_id, day_from_ts, day_to_ts)

        if not track:
            results.append({
                "Юнит": unit_name,
                "Домашний регион": None,
                "Время выезда": None,
                "Статус": "Нет данных по треку",
                "Вернулся": None,
                "Время возвращения": None,
                "Первый въезд в ответственные регионы": "",
                "Комментарий по регионам": "Нет данных по треку",
                "Время в ответственных регионах": ""
            })
            my_bar.progress(i / total_units, text=f"{unit_name} — нет данных")
            continue

        # Найдём timestamp последней точки (UTC)
        last_point_time = track[-1]["time"]
        last_message_dt = datetime.datetime.strptime(last_point_time, "%Y-%m-%d %H:%M:%S")
        last_message_ts = int(last_message_dt.timestamp())

        # Определяем домашний регион (по первой точке)
        df_first = pd.DataFrame([track[0]])
        df_first["geometry"] = df_first.apply(lambda row: Point(row["lon"], row["lat"]), axis=1)
        gdf_first = gpd.GeoDataFrame(df_first, geometry="geometry", crs="EPSG:4326")
        gdf_first_joined = gpd.sjoin(
            gdf_first,
            gdf_regions[['geometry', 'shapeName']],
            how="left",
            predicate="within"
        )
        home_region = gdf_first_joined.iloc[0]["shapeName"] if not gdf_first_joined.empty else None

        # Получаем crossings (UTC)
        crossings = detect_region_crossings(track, regions_geojson_path)

        # Определяем список ответственных регионов для этого юнита
        responsible_set = set(responsible_regions.get(unit_name, []))

        # Считаем, сколько секунд провёл юнит в каждом ответственном регионе
        region_seconds = compute_time_in_responsible_regions(
            crossings,
            start_of_day_ts=day_from_ts,
            last_message_ts=last_message_ts,
            responsible_set=responsible_set,
            initial_region=home_region
        )

        # Формируем строку "Регион: ЧЧ:ММ:СС"
        readable_times_resp = []
        for region_name, total_sec in region_seconds.items():
            if total_sec <= 0:
                continue
            hours = total_sec // 3600
            minutes = (total_sec % 3600) // 60
            seconds = total_sec % 60
            readable_times_resp.append(f"{region_name}: {hours:02d}:{minutes:02d}:{seconds:02d}")
        time_in_resp_str = "\n".join(readable_times_resp)

        # Визит/возврат для домашнего региона (по UTC-событиям)
        departure_event = None
        return_event = None
        returned_home = None

        if crossings:
            # Находим первый выезд из home_region (UTC)
            for idx, ev in enumerate(crossings):
                if ev["from_region"] == home_region:
                    departure_event = ev
                    break

            if departure_event:
                after_dep = crossings[idx+1:]
                return_indices = [j for j, e in enumerate(after_dep) if e["to_region"] == home_region]
                if return_indices:
                    last_return_idx = return_indices[-1]
                    return_event = after_dep[last_return_idx]
                    after_ret = after_dep[last_return_idx+1:]
                    left_again = any(e["from_region"] == home_region for e in after_ret)
                    if not left_again:
                        returned_home = True

        # Анализ ответственных регионов: первый въезд и статус посещения
        visited_regions = set(e["to_region"] for e in crossings if e["to_region"])
        first_entry_times = {}

        # 1) Если home_region входит в responsible_set, считаем, что первый въезд – 00:00 местного
        if home_region in responsible_set:
            # day_from_ts – timestamp для 00:00 местного, 
            # а UTC = местное − 5 часов
            first_entry_times[home_region] = day_from_ts - 5 * 3600

        # 2) Дальше обрабатываем реальные переходы
        for ev in crossings:
            region = ev["to_region"]
            if region in responsible_set and region not in first_entry_times:
                first_entry_times[region] = ev["time"]

        # Собираем «человеко-читаемую» строку
        entry_times_str = []
        for r, t in first_entry_times.items():
            # t может быть либо int (UTC-ts), либо строкой UTC из crossing
            if isinstance(t, int):
                # переводим UTC → местное (+5h)
                val = datetime.datetime.fromtimestamp(t) + datetime.timedelta(hours=5)
                entry_times_str.append(f"{r}: {val.strftime('%H:%M:%S')}")
            else:
                # t — строка UTC, конвертируем в местное
                val = pd.to_datetime(t) + pd.Timedelta(hours=5)
                entry_times_str.append(f"{r}: {val.strftime('%H:%M:%S')}")
        entry_times_str = "\n".join(entry_times_str)

        visited_resp = responsible_set & visited_regions
        not_visited_resp = responsible_set - visited_regions

        def format_regions(region_set):
            return ", ".join(sorted(str(r) for r in region_set if pd.notna(r)))

        if not responsible_set:
            region_comment = "❔ Нет назначенных регионов"
        elif home_region in responsible_set:
            # Если домашний регион тоже ответственный, то автоматически засчитан
            hit = visited_resp.union({home_region})
            missed = responsible_set - hit
            if missed:
                region_comment = f"✅ Посетил: {format_regions(hit)} | ❌ Не посетил: {format_regions(missed)}"
            else:
                region_comment = f"✅ Посетил все регионы: {format_regions(hit)}"
        elif not visited_resp:
            region_comment = "❌ Ни один ответственный регион не посещён"
        else:
            missed = not_visited_resp
            if missed:
                region_comment = f"✅ Посетил: {format_regions(visited_resp)} | ❌ Не посетил: {format_regions(missed)}"
            else:
                region_comment = f"✅ Посетил все регионы: {format_regions(visited_resp)}"

        # Для полей "Время выезда" и "Время возвращения" конвертируем UTC → местное (+5)
        dep_local = (
            (pd.to_datetime(departure_event["time"]) + pd.Timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
            if departure_event else
            None
        )
        ret_local = (
            (pd.to_datetime(return_event["time"]) + pd.Timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
            if return_event else
            None
        )

        results.append({
            "Юнит": unit_name,
            "Домашний регион": home_region,
            "Время выезда": dep_local,
            "Статус": "Выехал" if departure_event else "Еще не выехал",
            "Вернулся": True if return_event else False,
            "Время возвращения": ret_local,
            "Первый въезд в ответственные регионы": entry_times_str,
            "Комментарий по регионам": region_comment,
            "Время в ответственных регионах": time_in_resp_str
        })

        my_bar.progress(i / total_units, text=f"{unit_name} ✅")

    my_bar.empty()
    return pd.DataFrame(results)

# --- Чтение GeoJSON для карты (регионов и населённых пунктов) ---
with open(REGIONS_GEOJSON, "r", encoding="utf-8") as f:
    regions_geojson_str = json.dumps(json.load(f))
with open(CITIES_GEOJSON, "r", encoding="utf-8") as f:
    cities_geojson_str = json.dumps(json.load(f))

# ------------------ Блок 1: "🚀 Запустить отчёты и карту для выбранных юнитов" ------------------
if st.button("🚀 Запустить отчёты и карту для выбранных юнитов"):
    all_dates = pd.date_range(start=date_from, end=date_to, freq="D").to_pydatetime().tolist()

    for cur_date in all_dates:
        day_str = cur_date.strftime("%Y-%m-%d")
        st.markdown(f"## 📅 Дата: {day_str}")

        # Пересчитываем метки времени только для этого дня (UTC)
        day_from_ts = int(datetime.datetime.combine(cur_date.date(), datetime.time.min).timestamp())
        day_to_ts   = int(datetime.datetime.combine(cur_date.date(), datetime.time.max).timestamp())

        for unit_name in selected_units:
            st.markdown(f"### 🚘 Юнит: {unit_name}")
            unit_id = unit_dict[unit_name]

            # Получаем отчёт и трек за текущий день
            report_result = execute_report(SID, res["id"], tpl_id, unit_id, day_from_ts, day_to_ts)
            detailed_points = get_track(SID, unit_id, day_from_ts, day_to_ts)

            # 1) Переходы между регионами (UTC)
            crossings = detect_region_crossings(detailed_points, REGIONS_GEOJSON)
            if crossings:
                st.subheader("⛳ Переходы между регионами")
                # При отображении конвертируем UTC → местное (+5) для "time"
                df_crossings = pd.DataFrame(crossings)
                df_crossings["Юнит"] = unit_name
                df_crossings["time_local"] = df_crossings["time"].apply(
                    lambda t: (pd.to_datetime(t) + pd.Timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
                )
                st.dataframe(
                    df_crossings.drop(columns=["time"]).rename(columns={"time_local": "time"}),
                    use_container_width=True
                )
            else:
                st.info("Нет переходов найдено за этот день.")

            # 2) Таблицы отчёта (unit_trips и unit_trace), с конверсией UTC → местное (+5)
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
                                    # Конвертируем UTC → +5 часов
                                    dt = datetime.datetime.strptime(raw_val, "%Y-%m-%d %H:%M:%S") + datetime.timedelta(hours=5)
                                    val = dt.strftime("%Y-%m-%d %H:%M:%S")
                                except Exception:
                                    val = raw_val
                            elif isinstance(raw_val, (int, float)):
                                # В случае timestamp тоже +5
                                dt = datetime.datetime.fromtimestamp(raw_val) + datetime.timedelta(hours=5)
                                val = dt.strftime("%Y-%m-%d %H:%M:%S")
                            else:
                                val = raw_val
                            line.append(val)
                        parsed_rows.append(line)

                    df = pd.DataFrame(parsed_rows, columns=headers)
                    # Обрабатываем колонки "Начало" и "Конец" аналогично
                    df["Начало"] = (
                        df
                        .apply(
                            lambda row: (
                                pd.to_datetime(str(row["Начало"]), format="%Y-%m-%d %H:%M:%S", errors="raise")
                            )
                            if re.match(r"^\d{4}-\d{2}-\d{2}", str(row["Начало"]))
                            else pd.to_datetime(
                                f"{row['Grouping']} {row['Начало']}",
                                format="%Y-%m-%d %H:%M:%S",
                                errors="coerce"
                            )
                            , axis=1
                        )
                        + pd.Timedelta(hours=5)
                    ).dt.strftime("%H:%M:%S")

                    df["Конец"] = (
                        df
                        .apply(
                            lambda row: (
                                pd.to_datetime(str(row["Конец"]), format="%Y-%m-%d %H:%M:%S", errors="raise")
                            )
                            if re.match(r"^\d{4}-\d{2}-\d{2}", str(row["Конец"]))
                            else pd.to_datetime(
                                f"{row['Grouping']} {row['Конец']}",
                                format="%Y-%m-%d %H:%M:%S",
                                errors="coerce"
                            )
                            , axis=1
                        )
                        + pd.Timedelta(hours=5)
                    ).dt.strftime("%H:%M:%S")

                    df.rename(columns={"Grouping": "День"}, inplace=True)
                    st.markdown(f"#### 📋 Таблица '{table['name']}' для {unit_name}")
                    st.dataframe(df, use_container_width=True)
            else:
                st.warning(f"❌ Ошибка в отчёте за {day_str} для {unit_name}")
                st.json(report_result)

            # 3) Карта для этого дня
            coords = [[p["lat"], p["lon"]] for p in detailed_points]
            last = coords[-1] if coords else None

            car_icon_url = "https://cdn-icons-png.flaticon.com/512/854/854866.png"
            coords_json = json.dumps(coords)
            last_point_json = json.dumps(last)
            map_html = f"""
            <div id="map_{day_str}_{unit_name}" style="height: 400px; margin-bottom: 30px;"></div>
            <script>
                var map = L.map('map_{day_str}_{unit_name}').setView([48.0, 68.0], 6);
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
                <link
                  rel="stylesheet"
                  href="https://unpkg.com/leaflet@1.7.1/dist/leaflet.css"
                />
                <script src="https://unpkg.com/leaflet@1.7.1/dist/leaflet.js"></script>
                <script src="https://unpkg.com/leaflet.markercluster@1.4.1/dist/leaflet.markercluster.js"></script>
            </head>
            <body>{map_html}</body></html>
            """, height=420)

    st.success("✅ Построение отчетов и карт завершено.")

# ------------------ Блок 2: "📤 Сформировать отчёт по выезду из домашнего региона" ------------------
if st.button("📤 Сформировать отчёт по выезду из домашнего региона (Для всех) (Excel + таблицы)"):
    # Сначала читаем CSV с ответственными регионами
    df = pd.read_csv("manager_region.csv")
    responsible_regions = (
        df.groupby("Car_numb")["Region_mapped"]
        .apply(lambda x: list(set(x)))
        .to_dict()
    )

    all_dates = pd.date_range(start=date_from, end=date_to, freq="D").to_pydatetime().tolist()

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for cur_date in all_dates:
            day_str = cur_date.strftime("%Y-%m-%d")
            day_from_ts = int(datetime.datetime.combine(cur_date.date(), datetime.time.min).timestamp())
            day_to_ts   = int(datetime.datetime.combine(cur_date.date(), datetime.time.max).timestamp())

            report_df = create_departure_report(
                unit_dict, list(unit_dict.keys()),
                SID, REGIONS_GEOJSON, responsible_regions,
                day_from_ts, day_to_ts
            )

            not_departed_df = report_df[report_df["Статус"] == "Еще не выехал"]
            departed_df     = report_df[report_df["Статус"] == "Выехал"]

            # Если хотя бы один DF не пуст, записываем их
            if not not_departed_df.empty or not departed_df.empty:
                sheet_not = f"{day_str}_НеВыехал"
                sheet_dep = f"{day_str}_Выехал"
                not_departed_df.to_excel(writer, sheet_name=sheet_not, index=False)
                departed_df.to_excel(writer, sheet_name=sheet_dep, index=False)
            else:
                # Иначе — лист «Нет данных»
                dummy = pd.DataFrame({"Сообщение": [f"Нет данных за {day_str}"]})
                dummy.to_excel(writer, sheet_name=f"{day_str}_НетДанных", index=False)

    excel_data = output.getvalue()

    st.download_button(
        label="📥 Скачать Excel-отчет (по всем дням сразу)",
        data=excel_data,
        file_name=f"departure_report_{date_from}_{date_to}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    st.success("✅ Единый Excel-отчет сформирован и готов к загрузке.")
