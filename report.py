import streamlit as st

html_template = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8" />
    <title>Wialon Playground - Execute report</title>
    <!-- Подключаем jQuery -->
    <script type="text/javascript" src="//code.jquery.com/jquery-latest.min.js"></script>
    <!-- Подключаем Wialon JS SDK -->
    <script type="text/javascript" src="//hst-api.wialon.host/wsdk/script/wialon.js"></script>
</head>
<body>
<style>
td, th { border: 1px solid #c6c6c6; }
.wrap { max-height:150px; overflow-y: auto; }
.odd, th { background:#EEE; border: 1px solid #c6c6c6; }
</style>

<table>
    <tr>
        <td>Select resource and template:</td>
        <td>
            <select id="res"></select>
            <select id="templ"></select>
        </td>
    </tr>
    <tr>
        <td>Select unit:</td>
        <td>
            <select id="units"></select>
        </td>
    </tr>
    <tr>
        <td>Select time interval:</td>
        <td>
            <select id="interval">
                <option value="86400" title="60 sec * 60 minutes * 24 hours = 86400 sec = 1 day">Last day</option> 
                <option value="604800" title="86400 sec * 7 days = 604800 sec = 1 week">Last week</option>
                <option value="2592000" title="86400 sec * 30 days = 2592000 sec = 1 month">Last month</option>
            </select>
        </td>
    </tr>
    <tr>
        <td colspan="2" style="text-align:center;">
            <input type="button" value="Execute report" id="exec_btn"/>
        </td>
    </tr>
</table>
<div id="log"></div>

<script type="text/javascript">
// Функция вывода сообщений в лог
function msg(text) { $("#log").prepend(text + "<br/>"); }

function init() {
    var res_flags = wialon.item.Item.dataFlag.base | wialon.item.Resource.dataFlag.reports;
    var unit_flags = wialon.item.Item.dataFlag.base;
    var sess = wialon.core.Session.getInstance();
    sess.loadLibrary("resourceReports");
    sess.updateDataFlags(
        [{type: "type", data: "avl_resource", flags: res_flags, mode: 0},
         {type: "type", data: "avl_unit", flags: unit_flags, mode: 0}],
        function (code) {
            if (code) { msg(wialon.core.Errors.getErrorText(code)); return; }
            var res = sess.getItems("avl_resource");
            if (!res || !res.length){ msg("Resources not found"); return; }
            for (var i = 0; i < res.length; i++)
                $("#res").append("<option value='" + res[i].getId() + "'>" + res[i].getName() + "</option>");
            getTemplates();
            $("#res").change(getTemplates);
            var units = sess.getItems("avl_unit");
            if (!units || !units.length){ msg("Units not found"); return; }
            for (var i = 0; i < units.length; i++)
                $("#units").append("<option value='"+ units[i].getId() +"'>"+ units[i].getName()+ "</option>");
        }
    );
}

function getTemplates(){
    $("#templ").html("<option></option>");
    var res = wialon.core.Session.getInstance().getItem($("#res").val());
    if (!wialon.util.Number.and(res.getUserAccess(), wialon.item.Item.accessFlag.execReports)){
        $("#exec_btn").prop("disabled", true);
        msg("Not enought rights for report execution"); 
        return;
    } else {
        $("#exec_btn").prop("disabled", false);
    }
    var templ = res.getReports();
    for(var i in templ){
        if (templ[i].ct != "avl_unit") continue;
        $("#templ").append("<option value='"+ templ[i].id +"'>"+ templ[i].n+ "</option>");
    }
}

function executeReport(){
    var id_res = $("#res").val(), id_templ = $("#templ").val(), id_unit = $("#units").val(), time = $("#interval").val();
    if(!id_res){ msg("Select resource"); return; }
    if(!id_templ){ msg("Select report template"); return; }
    if(!id_unit){ msg("Select unit"); return; }
    var sess = wialon.core.Session.getInstance();
    var res = sess.getItem(id_res);
    var to = sess.getServerTime();
    var from = to - parseInt($("#interval").val(), 10);
    var interval = { "from": from, "to": to, "flags": wialon.item.MReport.intervalFlag.absolute };
    var template = res.getReport(id_templ);
    $("#exec_btn").prop("disabled", true);
    res.execReport(template, id_unit, 0, interval,
        function(code, data) {
            $("#exec_btn").prop("disabled", false);
            if(code){ msg(wialon.core.Errors.getErrorText(code)); return; }
            if(!data.getTables().length){
                msg("<b>There is no data generated</b>");
                return;
            } else {
                showReportResult(data);
            }
        }
    );
}

function showReportResult(result){
    var tables = result.getTables();
    if (!tables) return;
    for(var i=0; i < tables.length; i++){
        var html = "<b>"+ tables[i].label +"</b><div class='wrap'><table style='width:100%'>";
        var headers = tables[i].header;
        html += "<tr>";
        for (var j=0; j<headers.length; j++)
            html += "<th>" + headers[j] + "</th>";
        html += "</tr>";
        result.getTableRows(i, 0, tables[i].rows,
            qx.lang.Function.bind(function(html, code, rows) {
                if (code) { msg(wialon.core.Errors.getErrorText(code)); return; }
                for(var j in rows) {
                    if (typeof rows[j].c == "undefined") continue;
                    html += "<tr"+(j%2==1?" class='odd' ":"")+">";
                    for (var k = 0; k < rows[j].c.length; k++)
                        html += "<td>" + getTableValue(rows[j].c[k]) + "</td>";
                    html += "</tr>";
                }
                html += "</table>";
                msg(html +"</div>");
            }, this, html)
        );
    }
}

function getTableValue(data) {
    if (typeof data == "object")
        if (typeof data.t == "string") return data.t;
        else return "";
    else return data;
}

$(document).ready(function () {
    $("#exec_btn").click(executeReport);
    wialon.core.Session.getInstance().initSession("https://hst-api.wialon.host");
    // Замените ниже "YOUR_WIALON_TOKEN_HERE" на ваш реальный токен
    wialon.core.Session.getInstance().loginToken("c611c2bab48335e36a4b59be460c57d2DC99601D0C49777B24DFE07B7614A2826A62C393", "", function (code) {
        if (code){
            msg(wialon.core.Errors.getErrorText(code));
            return;
        }
        msg("Logged successfully");
        init();
    });
});
</script>
</body>
</html>
"""

import streamlit as st
st.components.v1.html(html_template, height=800, width=1400)
