import streamlit as st
import requests
import json
import datetime
import pandas as pd

BASE_URL = "https://hst-api.wialon.host/wialon/ajax.html"
TOKEN = "c611c2bab48335e36a4b59be460c57d2DC99601D0C49777B24DFE07B7614A2826A62C393"

@st.cache_data
def login(token):
    """Логинимся в Wialon и получаем SID."""
    r = requests.get(BASE_URL, params={"svc": "token/login", "params": json.dumps({"token": token})})
    return r.json().get("eid")

@st.cache_data
def get_units(sid):
    """Получаем список юнитов (avl_unit). Возвращаем словарь {имя: id}."""
    r = requests.get(BASE_URL, params={
        "svc": "core/search_items",
        "params": json.dumps({
            "spec": {
                "itemsType": "avl_unit",
                "propName": "sys_name",
                "propValueMask": "*",
                "sortType": "sys_name"
            },
            "force": 1,
            "flags": 1,
            "from": 0,
            "to": 0
        }),
        "sid": sid
    })
    data = r.json()
    items = data.get("items", [])
    return {item["nm"]: item["id"] for item in items}

def exec_report_dynamic(sid, template, obj_id, from_ts, to_ts):
    """Выполняем динамический отчёт (не указываем reportResourceId / reportTemplateId)."""
    r = requests.get(BASE_URL, params={
        "svc": "report/exec_report",
        "params": json.dumps({
            "reportResourceId": 0,      # обязательно 0
            "reportTemplateId": 0,      # обязательно 0
            "reportTemplate": template, # JSON объекта шаблона
            "reportObjectId": obj_id,
            "reportObjectSecId": 0,
            "interval": {
                "from": from_ts,
                "to": to_ts,
                "flags": 0
            }
        }),
        "sid": sid
    })
    return r.json()

def get_table_rows(sid, table_index, rows_count):
    """Загружаем строки таблицы (report/get_result_rows)."""
    r = requests.get(BASE_URL, params={
        "svc": "report/get_result_rows",
        "params": json.dumps({
            "tableIndex": table_index,
            "indexFrom": 0,
            "indexTo": rows_count
        }),
        "sid": sid
    })
    return r.json()

############### Streamlit-приложение ###############
st.set_page_config(layout="wide")
st.title("Пример с выбором ЮНИТов и столбцов (динамический отчёт)")

SID = login(TOKEN)
if not SID:
    st.error("Не удалось авторизоваться в Wialon. Проверьте токен.")
    st.stop()

# Получаем список юнитов
unit_dict = get_units(SID)
if not unit_dict:
    st.warning("Нет ни одного юнита в доступе.")
    st.stop()

# 1. Выбор ЮНИТов (можно несколько):
selected_units = st.multiselect("Выберите юниты", list(unit_dict.keys()))

# 2. Выбор интервала дат
today = datetime.date.today()
date_start = st.date_input("Дата начала", today)
date_end = st.date_input("Дата конца", today)

ts_from = int(datetime.datetime.combine(date_start, datetime.time.min).timestamp())
ts_to   = int(datetime.datetime.combine(date_end,   datetime.time.max).timestamp())

# 3. Выбор столбцов
possible_cols = [
    {"n": "time_begin",            "l": "Beginning"},
    {"n": "location_begin",        "l": "Initial location"},
    {"n": "coord_begin",           "l": "Initial coordinates"},
    {"n": "time_end",              "l": "End"},
    {"n": "location_end",          "l": "Final location"},
    {"n": "coord_end",             "l": "Final coordinates"},
    {"n": "duration_ival",         "l": "Total time"},
    {"n": "duration_next",         "l": "Following off-time"},
    {"n": "mileage",               "l": "Mileage"},
    {"n": "absolute_mileage_end",  "l": "Final mileage"}
]

all_col_names = [col["n"] for col in possible_cols]  # для множественного выбора
default_sel = ["time_begin", "location_begin", "time_end", "location_end"]
selected_cols = st.multiselect(
    "Выберите столбцы для отчёта 'unit_trips':",
    all_col_names,
    default=default_sel
)

# Кнопка "Сформировать отчёт"
if st.button("Сформировать отчёт для выбранных ЮНИТов"):
    # Если пользователь не выбрал юниты или колонки
    if not selected_units:
        st.warning("Выберите хотя бы один ЮНИТ")
        st.stop()
    if not selected_cols:
        st.warning("Выберите хотя бы одну колонку")
        st.stop()

    # Формируем строковые списки колоночных имён
    c_list = []
    cl_list = []
    for col in possible_cols:
        if col["n"] in selected_cols:
            c_list.append(col["n"])
            cl_list.append(col["l"])

    c_str = ",".join(c_list)    # Wialon ждёт строки вида "time_begin,time_end,..."
    cl_str = ",".join(cl_list)  # "Beginning,End,..."

    # Создаём JSON шаблон. Используем "unit_trips"
    # (при необходимости можно "unit_stays" или иной)
    dynamic_template = {
        "id": 0,
        "n": "unit_trips",
        "ct": "avl_unit",
        "p": "",
        "tbl": [{
            "n": "unit_trips",
            "l": "Trips (Dyn)",
            "c": c_str,         # строка из ID колонок
            "cl": cl_str,       # строка из "человеческих" меток
            "s": "",
            "sl": "",
            "p": "",
            "sch": {"f1":0, "f2":0, "t1":0, "t2":0, "m":0, "y":0, "w":0},
            "f": 0
        }]
    }

    # Последовательно обрабатываем каждый выбранный юнит
    for unit_name in selected_units:
        unit_id = unit_dict[unit_name]
        st.markdown(f"## Отчёт по ЮНИТу: **{unit_name}** (ID: {unit_id})")

        # 1) Запускаем отчёт (exec_report_dynamic)
        result_json = exec_report_dynamic(SID, dynamic_template, unit_id, ts_from, ts_to)
        if "reportResult" not in result_json:
            st.error(f"Не удалось построить отчёт для {unit_name}. Ответ: {result_json}")
            continue

        # 2) Достаём таблицы
        tables = result_json["reportResult"].get("tables", [])
        if not tables:
            st.warning(f"Нет таблиц в отчёте для {unit_name}")
            continue

        # Возьмём первую таблицу (или найти по названию "unit_trips")
        table_info = tables[0]
        row_count = table_info["rows"]
        headers = table_info["header"]
        table_idx = 0  # индекс таблицы

        if row_count == 0:
            st.info("Данных нет (0 строк).")
            continue

        # 3) Грузим строки из get_result_rows
        rows_data = get_table_rows(SID, table_idx, row_count)
        if "rows" not in rows_data:
            st.warning(f"Нет строк: {rows_data}")
            continue

        # Превращаем в DataFrame
        all_rows = rows_data["rows"]
        parsed = []
        for row in all_rows:
            cells = row["c"]
            row_list = []
            for cell in cells:
                val = cell["t"] if isinstance(cell, dict) and "t" in cell else cell
                row_list.append(val)
            parsed.append(row_list)

        df = pd.DataFrame(parsed, columns=headers)
        st.dataframe(df, use_container_width=True)
