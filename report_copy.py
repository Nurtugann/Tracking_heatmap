import requests
import json
import time

# URL базового запроса к Wialon API
BASE_URL = "https://hst-api.wialon.host/wialon/ajax.html"

# Токен для входа в систему (из вашего примера)
TOKEN = "c611c2bab48335e36a4b59be460c57d2DC99601D0C49777B24DFE07B7614A2826A62C393"

# Используем requests.Session для сохранения куки и параметров между запросами
session = requests.Session()

def login():
    """
    Выполняет вход по токену.
    """
    params = {
        "svc": "core/login",
        "params": json.dumps({"token": TOKEN})
    }
    resp = session.get(BASE_URL, params=params)
    data = resp.json()
    if "error" in data:
        print("Ошибка входа:", data["error"])
        return None
    print("Вход выполнен успешно.")
    return data.get("eid")  # Идентификатор сессии

def search_items(item_type, flags):
    """
    Выполняет поиск элементов указанного типа.
    Используется метод 'core/search_items', который возвращает список ресурсов или единиц.
    
    :param item_type: Тип элемента, например, "avl_resource" или "avl_unit"
    :param flags: Флаги, определяющие набор данных (например, базовая информация и дополнительные поля)
    :return: Список найденных элементов
    """
    search_spec = {
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
    }
    params = {
        "svc": "core/search_items",
        "params": json.dumps(search_spec)
    }
    resp = session.get(BASE_URL, params=params)
    data = resp.json()
    if "error" in data:
        print(f"Ошибка поиска {item_type}:", data["error"])
        return []
    return data.get("items", [])

def get_report_templates(resource_id):
    """
    Получает список шаблонов отчётов для указанного ресурса.
    Используется метод 'resource/get_reports'.
    
    :param resource_id: Идентификатор ресурса
    :return: Список шаблонов отчётов (фильтруются по типу 'avl_unit')
    """
    params = {
        "svc": "resource/get_reports",
        "params": json.dumps({"id": resource_id})
    }
    resp = session.get(BASE_URL, params=params)
    data = resp.json()
    if "error" in data:
        print("Ошибка получения шаблонов отчётов:", data["error"])
        return []
    # Фильтруем шаблоны по типу, оставляем только те, у которых ct == "avl_unit"
    templates = [tpl for tpl in data.get("reports", []) if tpl.get("ct") == "avl_unit"]
    return templates

def exec_report(resource_id, template_id, unit_id, interval_from, interval_to):
    """
    Выполняет отчёт с указанными параметрами.
    Используется метод 'resource/exec_report'. Параметры передаются аналогично вызову res.execReport(...) в JS.
    
    :param resource_id: Идентификатор ресурса
    :param template_id: Идентификатор шаблона отчёта
    :param unit_id: Идентификатор единицы (устройства)
    :param interval_from: Начало интервала в UNIX-времени (секунды)
    :param interval_to: Конец интервала в UNIX-времени (секунды)
    :return: Результат выполнения отчёта (словарь)
    """
    exec_params = {
        "itemId": resource_id,
        "reportTemplateId": template_id,
        "unitId": unit_id,
        "flags": 0,  # Дополнительные флаги (по необходимости)
        "interval": {
            "from": interval_from,
            "to": interval_to,
            "flags": 0  # Флаг абсолютного интервала
        }
    }
    params = {
        "svc": "resource/exec_report",
        "params": json.dumps(exec_params)
    }
    resp = session.get(BASE_URL, params=params)
    data = resp.json()
    if "error" in data:
        print("Ошибка выполнения отчёта:", data["error"])
        return None
    return data

def print_report_result(result):
    """
    Обрабатывает и выводит результаты отчёта в консоль.
    Предполагается, что результат содержит список таблиц с ключами:
    - label: название таблицы
    - header: список заголовков
    - rows: список строк, где каждая строка – словарь с ключом "c" (ячейки)
    """
    tables = result.get("tables", [])
    if not tables:
        print("Отчёт не вернул данных.")
        return

    for table in tables:
        print("\n=== Таблица:", table.get("label", "") + " ===")
        headers = table.get("header", [])
        print("\t" + "\t".join(headers))
        rows = table.get("rows", [])
        for row in rows:
            # Если данные строки отсутствуют, пропускаем
            if "c" not in row:
                continue
            cells = []
            for cell in row["c"]:
                # Если ячейка – это словарь с ключом 't'
                if isinstance(cell, dict) and "t" in cell:
                    cells.append(str(cell["t"]))
                else:
                    cells.append(str(cell))
            print("\t" + "\t".join(cells))

def main():
    # Выполняем вход
    eid = login()
    if not eid:
        return

    # Задаём флаги для запроса базовой информации.
    # Обычно для "base" используется значение 1.
    # Для получения информации об отчётах (для ресурсов) может потребоваться дополнительный флаг.
    base_flag = 1
    report_flag = 1 << 12  # Обычно используется для получения шаблонов отчётов (примерное значение)
    res_flags = base_flag | report_flag
    unit_flags = base_flag

    # Получаем список ресурсов
    print("Ищем ресурсы...")
    resources = search_items("avl_resource", res_flags)
    if not resources:
        print("Ресурсы не найдены.")
        return

    print("Найдены следующие ресурсы:")
    for res in resources:
        # Обычно имя хранится в ключе "nm" (системное имя)
        print(f"ID = {res['id']}, Name = {res.get('nm', '')}")

    resource_id_input = input("Введите ID ресурса: ").strip()
    try:
        resource_id = int(resource_id_input)
    except ValueError:
        print("Некорректный ID ресурса")
        return

    # Получаем список единиц (устройств)
    print("\nИщем единицы (устройства)...")
    units = search_items("avl_unit", unit_flags)
    if not units:
        print("Единицы не найдены.")
        return

    print("Найдены следующие единицы:")
    for unit in units:
        print(f"ID = {unit['id']}, Name = {unit.get('nm', '')}")

    unit_id_input = input("Введите ID единицы: ").strip()
    try:
        unit_id = int(unit_id_input)
    except ValueError:
        print("Некорректный ID единицы")
        return

    # Получаем шаблоны отчётов для выбранного ресурса
    print("\nПолучаем шаблоны отчётов для ресурса...")
    templates = get_report_templates(resource_id)
    if not templates:
        print("Шаблоны отчётов не найдены или недостаточно прав для их выполнения.")
        return

    print("Доступные шаблоны отчётов (отфильтрованные по типу 'avl_unit'):")
    for tpl in templates:
        print(f"ID = {tpl['id']}, Name = {tpl.get('n', '')}")

    template_id_input = input("Введите ID шаблона отчёта: ").strip()
    try:
        template_id = int(template_id_input)
    except ValueError:
        print("Некорректный ID шаблона")
        return

    # Выбор временного интервала
    print("\nВыберите временной интервал:")
    print("1. За последний день (86400 секунд)")
    print("2. За последнюю неделю (604800 секунд)")
    print("3. За последний месяц (2592000 секунд)")
    choice = input("Ваш выбор (1-3): ").strip()
    if choice == "1":
        interval_seconds = 86400
    elif choice == "2":
        interval_seconds = 604800
    elif choice == "3":
        interval_seconds = 2592000
    else:
        print("Неверный выбор.")
        return

    # Определяем интервал отчёта по текущему времени
    to_time = int(time.time())
    from_time = to_time - interval_seconds

    # Выполняем отчёт
    print("\nВыполняется отчёт...")
    result = exec_report(resource_id, template_id, unit_id, from_time, to_time)
    if result is None:
        return

    # Вывод результатов отчёта в консоль
    print_report_result(result)

if __name__ == '__main__':
    main()
