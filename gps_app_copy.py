import streamlit as st
import streamlit.components.v1 as components
import json

st.set_page_config(page_title="GPS карта Казахстана", layout="wide")
st.title("Интерактивная GPS карта Казахстана 🗺️")

# Загрузка данных
with open("geoBoundaries-KAZ-ADM2.geojson", encoding="utf-8") as f:
    polygons = json.load(f)

with open("hotosm_kaz_populated_places_points_geojson.geojson", encoding="utf-8") as f:
    points = json.load(f)

# Боковая панель управления
st.sidebar.header("⚙️ Панель управления")

random_walk = st.sidebar.checkbox("Случайное блуждание", False)
speed = st.sidebar.slider("🗆 Скорость перемещения (сек.)", 0.1, 2.0, 1.0, 0.1)
time_window = st.sidebar.slider("⏳ Интервал теплокарты (сек.)", 5, 60, 20, 5)

num_agents = st.sidebar.number_input("Количество агентов", 1, 10, 3)

agents_positions = [
    [51.1282, 71.4304],
    [47.0945, 51.9238],
    [42.3417, 69.5901],
    [44.8488, 65.4823],
    [43.2220, 76.8512],
    [50.2839, 57.1665],
    [52.9716, 63.1123],
    [48.0196, 66.9237],
    [46.8019, 61.6637],
    [53.2783, 69.3885]
]

initial_agents = agents_positions[:num_agents]

# HTML и JS
html = f'''
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script src="https://unpkg.com/leaflet.heat/dist/leaflet-heat.js"></script>
</head>
<body style="margin:0;padding:0;">
<div id="map" style="width:100%; height:650px;"></div>

<script>
    var map = L.map('map').setView([48.0, 68.0], 5);

    L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{ maxZoom: 18 }}).addTo(map);

    L.geoJson({json.dumps(polygons)}, {{ style: function() {{ return {{color: 'black', weight: 1, fillOpacity: 0}}; }} }}).addTo(map);

    var agents = {json.dumps(initial_agents)};
    var markers = [];
    var heatPoints = [];
    var timeWindow = {int(time_window * 1000)};  // in ms
    var agentTrails = Array.from({{length: agents.length}}, () => []);
    var stepIndex = 0;

    for (var i = 0; i < agents.length; i++) {{
        markers.push(L.marker(agents[i]).addTo(map).bindPopup(`Агент ${{i+1}}`));
        agentTrails[i].push([...agents[i], Date.now()]);
    }}

    var heatLayer = L.heatLayer([], {{radius: 25, blur: 15, maxZoom: 10}}).addTo(map);

    function stepAlongDefinedRoute(agentIndex, step) {{
        const base = {json.dumps(initial_agents)}[agentIndex];
        const angle = Math.PI / 6 + agentIndex * 0.2;
        const radius = 0.5 + agentIndex * 0.05;
        const lat = base[0] + Math.cos(step * 0.1 + angle) * radius;
        const lng = base[1] + Math.sin(step * 0.1 + angle) * radius;
        return [Math.min(Math.max(lat, 40), 55), Math.min(Math.max(lng, 50), 80)];
    }}

    function updateAgents() {{
        var now = Date.now();

        for (var i = 0; i < agents.length; i++) {{
            var pos = stepAlongDefinedRoute(i, stepIndex);
            agents[i] = pos;
            agentTrails[i].push([...pos, now]);

            markers[i].setLatLng(pos);
            markers[i].bindPopup(`Агент ${{i+1}}: траектория`);
        }}

        var filtered = [];
        for (var i = 0; i < agentTrails.length; i++) {{
            filtered.push(...agentTrails[i].filter(p => now - p[2] <= timeWindow).map(p => [p[0], p[1], 0.5]));
        }}
        heatLayer.setLatLngs(filtered);
        stepIndex++;
    }}

    var interval = null;
    if ({str(random_walk).lower()}) {{
        interval = setInterval(updateAgents, {int(speed * 1000)});
    }} else {{
        interval = setInterval(updateAgents, {int(speed * 1000)});
    }}
</script>
</body>
</html>
'''

components.html(html, height=670, width=1200)