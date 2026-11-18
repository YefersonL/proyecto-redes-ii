#!/usr/bin/env python3
import os, re, glob, json, urllib.request
import xml.etree.ElementTree as ET

ZBX_URL = "http://127.0.0.1/zabbix/api_jsonrpc.php"
ZBX_USER = "Admin"
ZBX_PASS = "zabbix"

GRAFANA_URL = "http://127.0.0.1:3000"
GRAFANA_AUTH = ("admin", "admin")

REPO_DIR = "/opt/proyecto-redes-ii"
UNL_FILE = os.path.join(REPO_DIR, "1_topologia/Proyecto.unl")
CFG_DIR = os.path.join(REPO_DIR, "2_configs_red/routers_switches")
VPC_DIR = os.path.join(REPO_DIR, "2_configs_red/servidores_y_vpcs")

def zbx_req(method, params, token=None):
    payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    headers = {"Content-Type": "application/json-rpc"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(ZBX_URL, data=json.dumps(payload).encode("utf-8"), headers=headers)
    with urllib.request.urlopen(req) as r:
        res = json.loads(r.read().decode("utf-8"))
    if "error" in res:
        raise Exception(f"Zabbix API Error ({method}): {res['error']}")
    return res["result"]

def graf_req(method, endpoint, data=None):
    import base64
    url = GRAFANA_URL + endpoint
    b64_auth = base64.b64encode(f"{GRAFANA_AUTH[0]}:{GRAFANA_AUTH[1]}".encode()).decode()
    headers = {"Content-Type": "application/json", "Authorization": f"Basic {b64_auth}"}
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            txt = r.read().decode("utf-8")
            return json.loads(txt) if txt else {}
    except urllib.error.HTTPError as e:
        return {"error": e.code, "message": e.read().decode("utf-8", errors="ignore")}

def extract_ip_from_cfg(cfg_path, raw_name=""):
    if "r2-pamplona" in raw_name.lower():
        return "192.168.1.250", True
    if not os.path.exists(cfg_path):
        return None, False
    with open(cfg_path, "r", errors="ignore") as f:
        txt = f.read()
    has_snmp = "snmp-server community" in txt
    ips = re.findall(r"ip address\s+(\d+\.\d+\.\d+\.\d+)\s+\d+\.\d+\.\d+\.\d+", txt)
    for ip in ips:
        if ip.startswith("192.168.1."):
            return ip, has_snmp
    for ip in ips:
        if ip.startswith("192.168."):
            return ip, has_snmp
    return (ips[0] if ips else None), has_snmp

def extract_ip_from_string_or_vpc(text, vpc_path=None):
    m = re.search(r"(\d+\.\d+\.\d+\.\d+)", text or "")
    if m and m.group(1) != "127.0.0.1":
        return m.group(1)
    if vpc_path and os.path.exists(vpc_path):
        with open(vpc_path, "r", errors="ignore") as f:
            vtxt = f.read()
        mv = re.search(r"ip\s+(\d+\.\d+\.\d+\.\d+)", vtxt)
        if mv and mv.group(1) != "0.0.0.0":
            return mv.group(1)
    return None

def get_or_create_zabbix_api_token(session_token):
    existing = zbx_req("token.get", {"filter": {"name": ["Token-Grafana-Redes2"]}}, session_token)
    if existing:
        zbx_req("token.delete", [existing[0]["tokenid"]], session_token)
    res_create = zbx_req("token.create", {"name": "Token-Grafana-Redes2", "userid": "1", "status": "0"}, session_token)
    tid = res_create["tokenids"][0]
    return zbx_req("token.generate", [tid], session_token)[0]["token"]

def main():
    print("[*] 1. Autenticando en Zabbix 7.0 API...")
    token = zbx_req("user.login", {"username": ZBX_USER, "password": ZBX_PASS})
    api_token = get_or_create_zabbix_api_token(token)

    grupos_nombres = ["Redes2 - Routers", "Redes2 - Switches", "Redes2 - Servidores", "Redes2 - PCs_VPCs"]
    grupos_id = {}
    for gname in grupos_nombres:
        res = zbx_req("hostgroup.get", {"filter": {"name": [gname]}}, token)
        grupos_id[gname] = res[0]["groupid"] if res else zbx_req("hostgroup.create", {"name": gname}, token)["groupids"][0]

    tpl_icmp = zbx_req("template.get", {"filter": {"host": ["ICMP Ping"]}}, token)
    tpl_snmp = zbx_req("template.get", {"filter": {"host": ["Cisco IOS by SNMP"]}}, token)
    id_tpl_icmp = tpl_icmp[0]["templateid"] if tpl_icmp else None
    id_tpl_snmp = tpl_snmp[0]["templateid"] if tpl_snmp else None

    tree = ET.parse(UNL_FILE)
    root = tree.getroot()

    print("[*] 2. Sincronizando hosts en Zabbix (sin conflicto de plantillas)...")
    for node in root.findall(".//node"):
        nid = node.get("id")
        raw_name = node.get("name", f"node_{nid}").strip()
        safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", raw_name)
        ntype = node.get("type", "").lower()
        nimage = node.get("image", "").lower()

        categoria, ip_addr, use_snmp = None, None, False
        if ntype == "iol":
            cfg_file = os.path.join(CFG_DIR, f"{safe_name}_id{nid}.cfg")
            ip_addr, use_snmp = extract_ip_from_cfg(cfg_file, raw_name)
            categoria = "Redes2 - Switches" if ("l2-" in nimage or raw_name.lower().startswith("s")) else "Redes2 - Routers"
        elif ntype == "docker":
            categoria = "Redes2 - Servidores"
            ip_addr = extract_ip_from_string_or_vpc(node.get("eth1_ip", "") or raw_name)
        elif ntype == "vpcs":
            categoria = "Redes2 - PCs_VPCs"
            vpc_file = os.path.join(VPC_DIR, f"{safe_name}_id{nid}.vpc")
            ip_addr = extract_ip_from_string_or_vpc(raw_name, vpc_file)

        if not categoria:
            continue

        final_ip = ip_addr if ip_addr else "127.0.0.1"
        host_tech_name = f"{safe_name}_id{nid}"
        visible_name = f"{raw_name} ({final_ip})" if ip_addr else f"{raw_name} [Sin IP]"
        gid = grupos_id[categoria]
        status_val = 0 if ip_addr else 1

        # Usar EXCLUSIVAMENTE SNMP o ICMP Ping (nunca ambos a la vez para evitar colisión de icmpping)
        if use_snmp and id_tpl_snmp:
            templates = [{"templateid": id_tpl_snmp}]
            iface_type = 2
        else:
            templates = [{"templateid": id_tpl_icmp}] if id_tpl_icmp else []
            iface_type = 1

        iface = {
            "type": iface_type, "main": 1, "useip": 1, "ip": final_ip, "dns": "",
            "port": "161" if iface_type == 2 else "10050"
        }
        if iface_type == 2:
            iface["details"] = {"version": 2, "bulk": 1, "community": "{$SNMP_COMMUNITY}"}

        existing = zbx_req("host.get", {"filter": {"host": [host_tech_name]}}, token)
        if existing:
            hid = existing[0]["hostid"]
            try:
                zbx_req("host.update", {
                    "hostid": hid,
                    "name": visible_name,
                    "groups": [{"groupid": gid}],
                    "status": status_val
                }, token)
                ifaces = zbx_req("hostinterface.get", {"hostids": hid}, token)
                if ifaces and ifaces[0]["ip"] != final_ip:
                    zbx_req("hostinterface.update", {"interfaceid": ifaces[0]["interfaceid"], "ip": final_ip}, token)
            except Exception:
                pass
        else:
            zbx_req("host.create", {
                "host": host_tech_name,
                "name": visible_name,
                "groups": [{"groupid": gid}],
                "templates": templates,
                "interfaces": [iface],
                "status": status_val,
                "macros": [{"macro": "{$SNMP_COMMUNITY}", "value": "public"}]
            }, token)

    # Mostrar en consola qué datos reales tiene Zabbix recolectados ahora mismo
    items_sample = zbx_req("item.get", {
        "groupids": list(grupos_id.values()),
        "search": {"key_": "icmpping"},
        "output": ["name", "key_", "lastvalue", "lastclock"],
        "selectHosts": ["name"],
        "limit": 10
    }, token)
    print("    [i] Muestra de datos actuales en Zabbix (icmpping):")
    for it in items_sample[:6]:
        hname = it["hosts"][0]["name"] if it.get("hosts") else "?"
        print(f"        - {hname} | {it['name']} ({it['key_']}) = {it['lastvalue']} (clock: {it['lastclock']})")

    print("[*] 3. Configurando Data Source en Grafana (alexanderzobnin-zabbix-datasource v6.10.1)...")
    graf_req("POST", "/api/plugins/alexanderzobnin-zabbix-app/settings", {"enabled": True, "pinned": True})

    ds_payload = {
        "name": "Zabbix-Redes2",
        "type": "alexanderzobnin-zabbix-datasource",
        "access": "proxy",
        "url": "http://127.0.0.1/zabbix/api_jsonrpc.php",
        "basicAuth": False,
        "isDefault": True,
        "jsonData": {
            "authType": "userLogin",
            "username": ZBX_USER,
            "trends": True,
            "timeout": 30
        },
        "secureJsonData": {
            "apiToken": api_token,
            "password": ZBX_PASS
        }
    }
    ds_list = graf_req("GET", "/api/datasources")
    ds_uid = None
    ds_id = None
    if isinstance(ds_list, list):
        for d in ds_list:
            if d.get("type") == "alexanderzobnin-zabbix-datasource":
                ds_uid = d.get("uid")
                ds_id = d.get("id")
                graf_req("PUT", f"/api/datasources/uid/{ds_uid}", ds_payload)
                break
    if not ds_uid:
        created_ds = graf_req("POST", "/api/datasources", ds_payload)
        ds_uid = created_ds.get("datasource", {}).get("uid") or created_ds.get("uid")
        ds_id = created_ds.get("datasource", {}).get("id") or created_ds.get("id")

    print(f"    [✔] Data Source ID={ds_id} | UID={ds_uid}")

    # Estructura exacta de consulta para alexanderzobnin-zabbix-app v6.10.1 + Zabbix 7.0 (sin 'application')
    def make_target(ref, grp, item_regex):
        return {
            "refId": ref,
            "datasource": {"type": "alexanderzobnin-zabbix-datasource", "uid": ds_uid},
            "queryType": "0",
            "schema": 12,
            "group": {"filter": grp},
            "host": {"filter": "/.*/"},
            "itemTag": {"filter": ""},
            "item": {"filter": item_regex},
            "functions": [],
            "options": {
                "showDisabledItems": False,
                "skipEmptyValues": False,
                "disableDataAlignment": False,
                "useZabbixValueMapping": False
            }
        }

    print("[*] 4. Actualizando los 4 Dashboards en Grafana...")
    dashboards_spec = [
        ("redes2-routers", "1. Redes II - Panel de Routers (WAN/LAN)", "Redes2 - Routers"),
        ("redes2-switches", "2. Redes II - Panel de Switches (Capa 2 / VLANs)", "Redes2 - Switches"),
        ("redes2-servidores", "3. Redes II - Panel de Servidores (DNS / WEB / DHCP)", "Redes2 - Servidores"),
        ("redes2-pcs", "4. Redes II - Panel de PCs y VPCs por VLAN", "Redes2 - PCs_VPCs")
    ]

    for uid, title, zbx_group in dashboards_spec:
        dash_json = {
            "dashboard": {
                "uid": uid,
                "title": title,
                "tags": ["Redes2", zbx_group],
                "timezone": "browser",
                "schemaVersion": 39,
                "refresh": "10s",
                "time": {"from": "now-1h", "to": "now"},
                "panels": [
                    {
                        "id": 1,
                        "title": f"Estado ICMP Ping (Up=1 / Down=0) - {zbx_group}",
                        "type": "stat",
                        "gridPos": {"h": 7, "w": 24, "x": 0, "y": 0},
                        "datasource": {"type": "alexanderzobnin-zabbix-datasource", "uid": ds_uid},
                        "targets": [make_target("A", zbx_group, "/ICMP ping/")],
                        "options": {
                            "colorMode": "background",
                            "graphMode": "none",
                            "textMode": "value_and_name",
                            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}
                        },
                        "fieldConfig": {
                            "defaults": {
                                "mappings": [{"type": "value", "options": {"0": {"text": "DOWN", "color": "red"}, "1": {"text": "UP", "color": "green"}}}],
                                "thresholds": {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "green", "value": 1}]}
                            }
                        }
                    },
                    {
                        "id": 2,
                        "title": f"Latencia ICMP Response Time - {zbx_group}",
                        "type": "timeseries",
                        "gridPos": {"h": 9, "w": 12, "x": 0, "y": 7},
                        "datasource": {"type": "alexanderzobnin-zabbix-datasource", "uid": ds_uid},
                        "targets": [make_target("A", zbx_group, "/ICMP response time/")],
                        "fieldConfig": {"defaults": {"unit": "s"}}
                    },
                    {
                        "id": 3,
                        "title": f"Pérdida de Paquetes ICMP Loss (%) - {zbx_group}",
                        "type": "timeseries",
                        "gridPos": {"h": 9, "w": 12, "x": 12, "y": 7},
                        "datasource": {"type": "alexanderzobnin-zabbix-datasource", "uid": ds_uid},
                        "targets": [make_target("A", zbx_group, "/ICMP loss/")],
                        "fieldConfig": {"defaults": {"unit": "percent", "min": 0, "max": 100}}
                    },
                    {
                        "id": 4,
                        "title": f"Tráfico Interfaces SNMP (Bits rx/tx) - {zbx_group}",
                        "type": "timeseries",
                        "gridPos": {"h": 9, "w": 24, "x": 0, "y": 16},
                        "datasource": {"type": "alexanderzobnin-zabbix-datasource", "uid": ds_uid},
                        "targets": [make_target("A", zbx_group, "/Bits (received|sent)/")],
                        "fieldConfig": {"defaults": {"unit": "bps"}}
                    }
                ]
            },
            "overwrite": True
        }
        graf_req("POST", "/api/dashboards/db", dash_json)

    # 5. Probar una consulta real al endpoint /api/ds/query de Grafana para diagnosticar respuesta
    print("[*] 5. Probando consulta real de datos en el motor de Grafana (/api/ds/query)...")
    test_query = {
        "queries": [make_target("A", "Redes2 - Servidores", "/ICMP ping/")],
        "from": "now-1h",
        "to": "now"
    }
    q_res = graf_req("POST", "/api/ds/query", test_query)
    print("    [i] Resultado test /api/ds/query:", str(q_res)[:350])
    print("\n[✔] ¡Proceso completado!")

if __name__ == "__main__":
    main()
