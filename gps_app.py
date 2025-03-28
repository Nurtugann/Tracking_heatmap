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

random_walk = st.sidebar.checkbox("Случайное блуждание", True)
speed = st.sidebar.slider("🛆 Скорость перемещения (сек.)", 0.1, 2.0, 1.0, 0.1)

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
</head>
<body style="margin:0;padding:0;">
<div id="map" style="width:100%; height:650px;"></div>

<script>
    var map = L.map('map').setView([48.0, 68.0], 5);

    L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{ maxZoom: 18 }}).addTo(map);

    L.geoJson({json.dumps(polygons)}, {{ style: function() {{ return {{color: 'black', weight: 1, fillOpacity: 0}}; }} }}).addTo(map);

    L.geoJson({json.dumps(points)}, {{
        pointToLayer: function(feature, latlng) {{
            return L.circleMarker(latlng, {{ radius: 0.5, fillColor: "red", color: "red", fillOpacity: 0.7 }}).bindPopup(feature.properties.name || "Без названия");
        }}
    }}).addTo(map);

    var agents = {json.dumps(initial_agents)};
    var markers = [];

    var gridSizeLat = 0.25;
    var gridSizeLng = 0.25;
    var heatmapCounts = {{}};

    function getCell(lat, lng) {{
        var latCell = Math.floor(lat / gridSizeLat);
        var lngCell = Math.floor(lng / gridSizeLng);
        return `${{latCell}},${{lngCell}}`;
    }}

    for (var i = 0; i < agents.length; i++) {{
        markers.push(L.marker(agents[i]).addTo(map).bindPopup(`Агент ${{i+1}}`));
    }}

    function updateHeatmap(cellKey) {{
        if (!(cellKey in heatmapCounts)) {{
            heatmapCounts[cellKey] = 0;
        }}
        heatmapCounts[cellKey]++;
    }}

    function drawHeatmap() {{
        for (var key in heatmapCounts) {{
            var parts = key.split(",");
            var lat = parseInt(parts[0]) * gridSizeLat;
            var lng = parseInt(parts[1]) * gridSizeLng;
            var intensity = Math.min(heatmapCounts[key] * 5, 255);
            var color = 'rgba(255,0,0,' + (intensity / 255) + ')';

            var rect = L.rectangle([
                [lat, lng],
                [lat + gridSizeLat, lng + gridSizeLng]
            ], {{color: color, weight: 0, fillOpacity: 0.3}});

            rect.addTo(map);
        }}
    }}

    function randomWalk() {{
        for (var i = 0; i < agents.length; i++) {{
            var active = Math.random() >= 0.5;

            if (active) {{
                var angle = Math.random() * 2 * Math.PI;
                var stepSize = 0.1;
                agents[i][0] += Math.cos(angle) * stepSize;
                agents[i][1] += Math.sin(angle) * stepSize;

                agents[i][0] = Math.min(Math.max(agents[i][0], 40), 55);
                agents[i][1] = Math.min(Math.max(agents[i][1], 50), 80);

                markers[i].setIcon(new L.Icon.Default({{iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon.png'}}));
            }} else {{
                markers[i].setIcon(new L.Icon.Default({{iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon-red.png'}}));
            }}

            var cell = getCell(agents[i][0], agents[i][1]);
            updateHeatmap(cell);

            markers[i].setLatLng(agents[i]);
            markers[i].bindPopup(`Агент ${{i+1}}: ${{active ? "активный" : "пассивный"}}<br>Ячейка: ${{cell}} (посещено: ${{heatmapCounts[cell]}} раз)`);
        }}

        drawHeatmap();
    }}

    var interval = null;
    if ({str(random_walk).lower()}) {{
        interval = setInterval(randomWalk, {int(speed*1000)});
    }}
</script>
</body>
</html>
'''

components.html(html, height=670, width=1200)