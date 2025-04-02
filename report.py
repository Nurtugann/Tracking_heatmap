import streamlit as st
import requests
import json
import pandas as pd
import datetime

st.set_page_config(layout="wide")
st.title("📊 Wialon: отчёт как таблица")

TOKEN = "c611c2bab48335e36a4b59be460c57d2DC99601D0C49777B24DFE07B7614A2826A62C393"
BASE_URL = "https://hst-api.wialon.host/wialon/ajax.html"

# Авторизация
@st.cache_data
def login(token):
    params = {"svc": "token/login", "params": json.dumps({"token": token})}
    return requests.get(BASE_URL, params=params).json().get("eid")

SID = login(TOKEN)

# Получение ресурсов и юнитов
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
    st.warning("Нет юнитов или ресурсов.")
    st.stop()

unit_dict = {u["nm"]: u["id"] for u in units}
res_dict = {r["nm"]: r for r in resources}

# UI
unit_name = st.selectbox("Выбери юнит", list(unit_dict.keys()))
res_name = st.selectbox("Выбери ресурс", list(res_dict.keys()))
tpl_dict = {tpl["n"]: tpl["id"] for tpl in res_dict[res_name]["rep"].values()}
tpl_name = st.selectbox("Выбери шаблон отчёта", list(tpl_dict.keys()))
interval_label = st.selectbox("Период", ["Последний день", "Последняя неделя", "Последний месяц"])
interval_seconds = {"Последний день": 86400, "Последняя неделя": 604800, "Последний месяц": 2592000}[interval_label]

# Выполнение отчёта
def execute_report(sid, res_id, tpl_id, unit_id, interval):
    to_time = int(datetime.datetime.now().timestamp())
    from_time = to_time - interval
    params = {
        "svc": "report/exec_report",
        "params": json.dumps({
            "reportResourceId": res_id,
            "reportTemplateId": tpl_id,
            "reportObjectId": unit_id,
            "reportObjectSecId": 0,
            "interval": {"from": from_time, "to": to_time, "flags": 0}
        }),
        "sid": sid
    }
    return requests.get(BASE_URL, params=params).json()

# Загрузка строк отчёта
def load_report_rows(sid, table_index, row_count):
    params = {
        "svc": "report/get_result_rows",
        "params": json.dumps({
            "tableIndex": table_index,
            "indexFrom": 0,
            "indexTo": row_count
        }),
        "sid": sid
    }
    return requests.get(BASE_URL, params=params).json()

# Кнопка
if st.button("📥 Получить отчёт"):
    report_data = execute_report(SID, res_dict[res_name]["id"], tpl_dict[tpl_name], unit_dict[unit_name], interval_seconds)

    if "reportResult" in report_data:
        for table_index, table in enumerate(report_data["reportResult"]["tables"]):
            st.subheader(table["label"])
            rows = load_report_rows(SID, table_index, table["rows"])

            if "rows" in rows:
                parsed_data = []
                for row in rows["rows"]:
                    parsed_row = []
                    for cell in row.get("c", []):
                        if isinstance(cell, dict):
                            parsed_row.append(cell.get("t", json.dumps(cell)))
                        else:
                            parsed_row.append(cell)
                    parsed_data.append(parsed_row)

                df = pd.DataFrame(parsed_data, columns=table["header"])
                st.dataframe(df, use_container_width=True)
            else:
                st.error("❌ Ошибка при получении строк отчёта")
                st.json(rows)
    else:
        st.warning("Отчёт пуст или не сработал.")
