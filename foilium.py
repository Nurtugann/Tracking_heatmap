import streamlit as st
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

# —————— Корректная очистка кеша только после инициализации сессии ——————
# Попытка очистить кеш один раз, но только если SessionInfo уже готов
try:
    if "cache_cleared" not in st.session_state:
        st.cache_data.clear()
        st.session_state.cache_cleared = True
except RuntimeError:
    # если сессия ещё не инициализирована — отложим на следующий ран
    pass


# --- Константы ---
TOKEN = "c611c2bab48335e36a4b59be460c57d2BF8416B73C4A65F2B8A88A5848E97CD4471F14C6"
BASE_URL = "https://hst-api.wialon.host/wialon/ajax.html"
REGIONS_GEOJSON = "OSMB-f1ec2d0019a5c0c4984f489cdc13d5d26a7949fd.geojson"
CITIES_GEOJSON = "hotosm_kaz_populated_places_points_geojson.geojson"
ZERO_SPEED_THRESHOLD = 0  # Скорость <= 0 считаем как "остановка"

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
        pos = m.get("pos") or {}
        # Пытаемся получить скорость из pos["s"], а если нет – взять из m.get("spd")
        raw_speed = pos.get("s", m.get("spd", 0))
        try:
            speed = float(raw_speed)
        except Exception:
            speed = 0.0

        if pos:
            t = m.get("t")
            try:
                if isinstance(t, str):
                    dt = datetime.datetime.strptime(t, "%Y-%m-%d %H:%M:%S")
                else:
                    dt = datetime.datetime.fromtimestamp(t)
                utc_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                utc_str = t
            points.append({
                "lat": pos.get("y"),
                "lon": pos.get("x"),
                "time": utc_str,
                "spd": speed
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
        "time": row["datetime"].strftime("%Y-%m-%d %H:%M:%S"),
        "lat": row["lat"],
        "lon": row["lon"]
    }, axis=1))
    
    return crossings_list

def detect_stops(points, zero_threshold=1):
    """
    Находит интервалы, когда транспорт стоит:
    - «остановка» начинается, когда spd <= zero_threshold, 
      а до этого spd > zero_threshold;
    - «движение» восстанавливается, когда spd > zero_threshold после серии точек с spd <= zero_threshold.
    """
    if not points:
        return []

    df = pd.DataFrame(points)
    df["datetime_utc"] = pd.to_datetime(df["time"], format="%Y-%m-%d %H:%M:%S", errors="coerce")
    df["spd"] = pd.to_numeric(df["spd"], errors="coerce").fillna(0)

    df["is_stopped"] = df["spd"] <= zero_threshold

    stops = []
    in_stop = False
    stop_start_ts = None
    stop_start_idx = None

    for idx, row in df.iterrows():
        if not in_stop:
            prev_is_stopped = df.loc[idx - 1, "is_stopped"] if idx > 0 else False
            if row["is_stopped"] and not prev_is_stopped:
                in_stop = True
                stop_start_ts = row["datetime_utc"]
                stop_start_idx = idx
        else:
            if not row["is_stopped"]:
                stop_end_ts = row["datetime_utc"]
                lat = df.loc[stop_start_idx, "lat"]
                lon = df.loc[stop_start_idx, "lon"]
                stops.append({
                    "stop_start_utc": stop_start_ts,
                    "stop_end_utc": stop_end_ts,
                    "lat": lat,
                    "lon": lon
                })
                in_stop = False
                stop_start_ts = None
                stop_start_idx = None

    if in_stop and stop_start_ts is not None:
        lat = df.loc[stop_start_idx, "lat"]
        lon = df.loc[stop_start_idx, "lon"]
        stops.append({
            "stop_start_utc": stop_start_ts,
            "stop_end_utc": None,
            "lat": lat,
            "lon": lon
        })

    return stops

def compute_time_in_responsible_regions(crossings, start_of_day_ts, last_message_ts, responsible_set, initial_region):
    entry_ts_map = {r: None for r in responsible_set}
    durations = {r: 0 for r in responsible_set}

    if initial_region in responsible_set:
        entry_ts_map[initial_region] = start_of_day_ts

    for ev in crossings:
        t_e = int(datetime.datetime.strptime(ev["time"], "%Y-%m-%d %H:%M:%S").timestamp())
        if t_e < start_of_day_ts or t_e > last_message_ts:
            continue

        r_to = ev["to_region"]
        r_from = ev["from_region"]

        if r_to in responsible_set and entry_ts_map[r_to] is None:
            entry_ts_map[r_to] = t_e

        if r_from in responsible_set and entry_ts_map[r_from] is not None:
            exit_ts = t_e
            durations[r_from] += (exit_ts - entry_ts_map[r_from])
            entry_ts_map[r_from] = None

    for r in responsible_set:
        if entry_ts_map[r] is not None:
            durations[r] += (last_message_ts - entry_ts_map[r])
            entry_ts_map[r] = None

    return durations

def create_departure_report(unit_dict, units_to_process, SID, regions_geojson_path, responsible_regions, day_from_ts, day_to_ts):
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
            for idx2, ev in enumerate(crossings):
                if ev["from_region"] == home_region:
                    departure_event = ev
                    break

            if departure_event:
                after_dep = crossings[idx2+1:]
                return_indices = [j for j, e in enumerate(after_dep) if e["to_region"] == home_region]
                if return_indices:
                    last_return_idx = return_indices[-1]
                    return_event = after_dep[last_return_idx]
                    after_ret = after_dep[last_return_idx+1:]
                    left_again = any(e["from_region"] == home_region for e in after_ret)
                    if not left_again:
                        returned_home = True

        visited_regions = set(e["to_region"] for e in crossings if e["to_region"])
        first_entry_times = {}

        if home_region in responsible_set:
            first_entry_times[home_region] = day_from_ts - 5 * 3600

        for ev in crossings:
            region = ev["to_region"]
            if region in responsible_set and region not in first_entry_times:
                first_entry_times[region] = ev["time"]

        entry_times_str = []
        for r, t in first_entry_times.items():
            if isinstance(t, int):
                val = datetime.datetime.fromtimestamp(t) + datetime.timedelta(hours=5)
                entry_times_str.append(f"{r}: {val.strftime('%H:%M:%S')}")
            else:
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
            report_result   = execute_report(SID, res["id"], tpl_id, unit_id, day_from_ts, day_to_ts)
            detailed_points = get_track(SID, unit_id, day_from_ts, day_to_ts)

            if not detailed_points:
                st.info(f"❌ Нет точек трека для {unit_name} за {day_str}, пропускаем.")
                continue

            # 1) Переходы между регионами (UTC)
            crossings = detect_region_crossings(detailed_points, REGIONS_GEOJSON)
            if crossings:
                st.subheader("⛳ Переходы между регионами")
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

            # 3) Детекция остановок (UTC → местное + отметка на карте)
            stops_utc = detect_stops(detailed_points, zero_threshold=1)

            # === Определение домашнего региона ===
            df_first = pd.DataFrame([detailed_points[0]])
            df_first["geometry"] = df_first.apply(lambda row: Point(row["lon"], row["lat"]), axis=1)
            gdf_first = gpd.GeoDataFrame(df_first, geometry="geometry", crs="EPSG:4326")

            with open(REGIONS_GEOJSON, "r", encoding="utf-8") as f:
                regions_geojson = json.load(f)
            gdf_regions = gpd.GeoDataFrame.from_features(regions_geojson["features"])
            gdf_regions.crs = "EPSG:4326"
            if "shapeName" not in gdf_regions.columns:
                gdf_regions["shapeName"] = gdf_regions.get("name", "")

            gdf_first_joined = gpd.sjoin(
                gdf_first,
                gdf_regions[['geometry', 'shapeName']],
                how="left",
                predicate="within"
            )
            home_region = gdf_first_joined.iloc[0]["shapeName"] if not gdf_first_joined.empty else None

            # === Фильтрация остановок: только вне домашнего региона и > 15 минут ===
            filtered_stops = []
            for s in stops_utc:
                if s["stop_end_utc"] is None:
                    continue  # игнорируем незавершённые остановки
                duration = (s["stop_end_utc"] - s["stop_start_utc"]).total_seconds()
                if duration < 15 * 60:
                    continue  # игнорируем короткие остановки

                stop_point = gpd.GeoDataFrame(
                    {"geometry": [Point(s["lon"], s["lat"])]},
                    crs="EPSG:4326"
                )
                joined = gpd.sjoin(stop_point, gdf_regions[['geometry', 'shapeName']], how="left", predicate="within")
                stop_region = joined.iloc[0]["shapeName"] if not joined.empty else None

                if stop_region != home_region:
                    # Добавляем в финальный список, если остановка вне домашнего региона
                    start_local = s["stop_start_utc"] + datetime.timedelta(hours=5)
                    end_local   = s["stop_end_utc"] + datetime.timedelta(hours=5)
                    duration_str = f"{int(duration // 3600):02d}:{int((duration % 3600) // 60):02d}:{int(duration % 60):02d}"
                    filtered_stops.append({
                        "lat": s["lat"],
                        "lon": s["lon"],
                        "start_local": start_local.strftime("%Y-%m-%d %H:%M:%S"),
                        "end_local": end_local.strftime("%Y-%m-%d %H:%M:%S"),
                        "duration": duration_str
                    })

            # === Вывод таблицы с остановками вне домашнего региона ===
            if filtered_stops:
                df_stops = pd.DataFrame(filtered_stops)
                st.subheader("🛑 Остановки > 15 минут ВНЕ домашнего региона")
                st.dataframe(df_stops, use_container_width=True)
            else:
                st.info("Нет остановок > 15 минут вне домашнего региона за этот день.")

            # ——— Объединяем переходы и остановки в одну хронологическую таблицу ———
            try:
                # 1) Приводим переходы к единому виду
                df_cross = (
                    df_crossings
                    .drop(columns=["time"])
                    .rename(columns={"time_local": "time"})
                    .assign(
                        type="crossing",
                        duration=""
                    )
                    .loc[:, ["time", "type", "from_region", "to_region", "lat", "lon", "duration"]]
                )

                # 2) Приводим остановки к тому же виду
                df_stop = (
                    df_stops
                    .rename(columns={"start_local": "time", "duration": "duration"})
                    .assign(
                        type="stop",
                        from_region="", to_region=""
                    )
                    .loc[:, ["time", "type", "from_region", "to_region", "lat", "lon", "duration"]]
                )

                # 3) Склеиваем и сортируем по времени
                combined = (
                    pd.concat([df_cross, df_stop], ignore_index=True)
                    .assign(time=lambda df: pd.to_datetime(df["time"]))
                    .sort_values("time")
                    .reset_index(drop=True)
                )

                # 4) Выводим результат
                st.subheader("⏱️ Все события (переходы и остановки) в хронологическом порядке")
                st.dataframe(combined, use_container_width=True)
            except:
                st.info("Нет данных для вывода.")

            # 4) Отметка ⛔ точек нулевой скорости…
            zero_speed_points = []

            # Подготовим DataFrame с datetime и скоростью
            df = pd.DataFrame(detailed_points)
            df["datetime_utc"] = pd.to_datetime(df["time"], format="%Y-%m-%d %H:%M:%S", errors="coerce")
            df["spd"] = pd.to_numeric(df["spd"], errors="coerce").fillna(0)
            df["is_zero_speed"] = df["spd"] <= ZERO_SPEED_THRESHOLD

            in_zero       = False
            segment_start = None
            segment_first = None

            for idx, row in df.iterrows():
                if row["is_zero_speed"]:
                    if not in_zero:
                        # Начало нового сегмента — запомним только первую точку
                        in_zero       = True
                        segment_start = row["datetime_utc"]
                        segment_first = row
                else:
                    if in_zero:
                        # Конец сегмента
                        in_zero  = False
                        duration = (row["datetime_utc"] - segment_start).total_seconds()
                        if duration >= 15 * 60:
                            # Определяем, в каком регионе первая точка
                            pt_gdf = gpd.GeoDataFrame(
                                {"geometry":[Point(segment_first["lon"], segment_first["lat"])]},
                                crs="EPSG:4326"
                            )
                            joined = gpd.sjoin(pt_gdf, gdf_regions[["geometry","shapeName"]], how="left", predicate="within")
                            seg_region = joined.iloc[0]["shapeName"] if not joined.empty else None

                            # Добавляем только если не в домашнем регионе
                            if seg_region != home_region:
                                local_time = (segment_start + datetime.timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
                                zero_speed_points.append({
                                    "lat":  segment_first["lat"],
                                    "lon":  segment_first["lon"],
                                    "time": local_time
                                })

            # Обработка незавершённого сегмента в конце
            if in_zero:
                duration = (df.iloc[-1]["datetime_utc"] - segment_start).total_seconds()
                if duration >= 15 * 60:
                    pt_gdf = gpd.GeoDataFrame(
                        {"geometry":[Point(segment_first["lon"], segment_first["lat"])]},
                        crs="EPSG:4326"
                    )
                    joined = gpd.sjoin(pt_gdf, gdf_regions[["geometry","shapeName"]], how="left", predicate="within")
                    seg_region = joined.iloc[0]["shapeName"] if not joined.empty else None

                    if seg_region != home_region:
                        local_time = (segment_start + datetime.timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
                        zero_speed_points.append({
                            "lat":  segment_first["lat"],
                            "lon":  segment_first["lon"],
                            "time": local_time
                        })



            # 5) Карта для этого дня с треком, последней точкой, остановками и точками нулевой скорости
            coords = [[p["lat"], p["lon"]] for p in detailed_points]
            last   = coords[-1] if coords else None

            car_icon_url   = "https://cdn-icons-png.flaticon.com/512/854/854866.png"
            coords_json    = json.dumps(coords)
            last_point_json= json.dumps(last)
            stops_json     = json.dumps(filtered_stops)
            zero_pts_json  = json.dumps(zero_speed_points)



            map_html = f"""
            <div id="map_{day_str}_{unit_name}" style="height: 400px; margin-bottom: 30px;"></div>
            <script>
                var map = L.map('map_{day_str}_{unit_name}').setView([48.0, 68.0], 6);
                L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png').addTo(map);
                var coords = {coords_json};
                var last   = {last_point_json};
                var stops  = {stops_json};
                var zeroPoints = {zero_pts_json};

                // Рисуем трек
                if (coords.length > 0) {{
                    var track = L.polyline(coords, {{color: 'red'}}).addTo(map);
                    map.fitBounds(track.getBounds());
                    // Последняя точка
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

                // Маркеры остановок > 15 минут
                stops.forEach(function(s) {{
                    var circleStop = L.circleMarker([s.lat, s.lon], {{
                        radius: 6,
                        color: 'blue',
                        fillOpacity: 0.7
                    }}).addTo(map);
                    var popupStop = "<b>Остановка > 15 мин:</b><br>"
                                    + "Начало: " + s.start_local;
                    if (s.end_local) {{
                        popupStop += "<br>Конец: " + s.end_local
                                  + "<br>Длительность: " + s.duration;
                    }}
                    circleStop.bindPopup(popupStop);
                }});

                // Точки нулевой скорости (⛔)
                zeroPoints.forEach(function(z) {{
                    var zeroIcon = L.icon({{
                        iconUrl: 'https://cdn-icons-png.flaticon.com/512/1033/1033151.png',
                        iconSize: [20, 20],
                        iconAnchor: [10, 10]
                    }});
                    L.marker([z.lat, z.lon], {{icon: zeroIcon}}).addTo(map)
                     .bindPopup("⛔ Скорость = 0<br>Время (UTC): " + z.time);
                }});

                // Слои с границами регионов и городами
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

            if not not_departed_df.empty or not departed_df.empty:
                sheet_not = f"{day_str}_НеВыехал"
                sheet_dep = f"{day_str}_Выехал"
                not_departed_df.to_excel(writer, sheet_name=sheet_not, index=False)
                departed_df.to_excel(writer, sheet_name=sheet_dep, index=False)
            else:
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
