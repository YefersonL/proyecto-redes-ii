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

# ---------------- FUNCIONES API ZABBIX ----------------
def zbx_req(method, params, auth=None):
    payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    if auth:
        payload["auth"] = auth
    req = urllib.request.Request(
        ZBX_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json-rpc"}
    )
    with urllib.request.urlopen(req) as r:
        res = json.loads(r.read().decode("utf-8"))
    if "error" in res:
        raise Exception(f"Zabbix API Error ({method}): {res['error']}")
    return res["result"]

# ---------------- FUNCIONES API GRAFANA ----------------
def graf_req(method, endpoint, data=None):
    import base64
    url = GRAFANA_URL + endpoint
    b64_auth = base64.b64encode(f"{GRAFANA_AUTH[0]}:{GRAFANA_AUTH[1]}".encode()).decode()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Basic {b64_auth}"
    }
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            txt = r.read().decode("utf-8")
            return json.loads(txt) if txt else {}
    except urllib.error.HTTPError as e:
        err_txt = e.read().decode("utf-8", errors="ignore")
        return {"error": e.code, "message": err_txt}

def extract_ip_from_cfg(cfg_path):
    if not os.path.exists(cfg_path):
        return None, False
    with open(cfg_path, "r", errors="ignore") as f:
        txt = f.read()
    has_snmp = "snmp-server community" in txt
    # Priorizar IP en red 192.168.1.X (Management), luego cualquier otra IP configurada
    ips = re.findall(r"ip address\s+(\d+\.\d+\.\d+\.\d+)\s+\d+\.\d+\.\d+\.\d+", txt)
    for ip in ips:
        if ip.startswith("192.168.1."):
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
        if mv:
            return mv.group(1)
    return None

def main():
    print("[*] 1. Autenticando en la API de Zabbix...")
    token = zbx_req("user.login", {"username": ZBX_USER, "password": ZBX_PASS})

    # Crear/obtener los 4 grupos en Zabbix
    grupos_nombres = [
        "Redes2 - Routers",
        "Redes2 - Switches",
        "Redes2 - Servidores",
        "Redes2 - PCs_VPCs"
    ]
    grupos_id = {}
    for gname in grupos_nombres:
        res = zbx_req("hostgroup.get", {"filter": {"name": [gname]}}, token)
        if res:
            grupos_id[gname] = res[0]["groupid"]
        else:
            c = zbx_req("hostgroup.create", {"name": gname}, token)
            grupos_id[gname] = c["groupids"][0]
    print(f"    [✔] Grupos en Zabbix listos: {grupos_id}")

    # Buscar plantillas ICMP Ping y Cisco IOS by SNMP
    tpl_icmp = zbx_req("template.get", {"filter": {"host": ["ICMP Ping"]}}, token)
    tpl_snmp = zbx_req("template.get", {"filter": {"host": ["Cisco IOS by SNMP"]}}, token)
    id_tpl_icmp = tpl_icmp[0]["templateid"] if tpl_icmp else None
    id_tpl_snmp = tpl_snmp[0]["templateid"] if tpl_snmp else None
    print(f"    [✔] Plantillas detectadas -> ICMP Ping: {id_tpl_icmp} | Cisco SNMP: {id_tpl_snmp}")

    # Si no existe aún copia local de Proyecto.unl, traerla de PNETLab
    if not os.path.exists(UNL_FILE):
        os.makedirs(os.path.dirname(UNL_FILE), exist_ok=True)
        os.system(f"scp -q root@192.168.1.50:/opt/unetlab/labs/Redes2/Proyecto/Proyecto.unl {UNL_FILE}")

    tree = ET.parse(UNL_FILE)
    root = tree.getroot()

    print("[*] 2. Clasificando nodos de Proyecto.unl y aprovisionando en Zabbix...")
    resumen = {"Redes2 - Routers": [], "Redes2 - Switches": [], "Redes2 - Servidores": [], "Redes2 - PCs_VPCs": []}

    for node in root.findall(".//node"):
        nid = node.get("id")
        raw_name = node.get("name", f"node_{nid}").strip()
        safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", raw_name)
        ntype = node.get("type", "").lower()
        nimage = node.get("image", "").lower()

        categoria = None
        ip_addr = None
        use_snmp = False

        if ntype == "iol":
            cfg_file = os.path.join(CFG_DIR, f"{safe_name}_id{nid}.cfg")
            ip_addr, has_snmp = extract_ip_from_cfg(cfg_file)
            if "l2-" in nimage or raw_name.lower().startswith("s"):
                categoria = "Redes2 - Switches"
            else:
                categoria = "Redes2 - Routers"
            use_snmp = has_snmp

        elif ntype == "docker":
            categoria = "Redes2 - Servidores"
            ip_addr = extract_ip_from_string_or_vpc(node.get("eth1_ip", "") or raw_name)

        elif ntype == "vpcs":
            categoria = "Redes2 - PCs_VPCs"
            vpc_file = os.path.join(VPC_DIR, f"{safe_name}_id{nid}.vpc")
            ip_addr = extract_ip_from_string_or_vpc(raw_name, vpc_file)

        if not categoria:
            continue

        # Si un router/switch o VPC aún no tiene IP asignada, le dejamos 127.0.0.1 temporal o su IP real
        final_ip = ip_addr if ip_addr else "127.0.0.1"
        host_tech_name = f"{safe_name}_id{nid}"
        visible_name = f"{raw_name} ({final_ip})" if ip_addr else f"{raw_name} [Sin IP]"

        gid = grupos_id[categoria]
        templates = []
        if use_snmp and id_tpl_snmp:
            templates.append({"templateid": id_tpl_snmp})
        elif id_tpl_icmp:
            templates.append({"templateid": id_tpl_icmp})

        iface_type = 2 if (use_snmp and id_tpl_snmp) else 1  # 2 = SNMP, 1 = Agent/ICMP
        iface_port = "161" if iface_type == 2 else "10050"
        iface = {
            "type": iface_type,
            "main": 1,
            "useip": 1,
            "ip": final_ip,
            "dns": "",
            "port": iface_port
        }
        if iface_type == 2:
            iface["details"] = {"version": 2, "bulk": 1, "community": "{$SNMP_COMMUNITY}"}

        existing = zbx_req("host.get", {"filter": {"host": [host_tech_name]}}, token)
        if existing:
            hid = existing[0]["hostid"]
            zbx_req("host.update", {
                "hostid": hid,
                "name": visible_name,
                "groups": [{"groupid": gid}],
                "status": 0 if ip_addr else 1
            }, token)
            # Actualizar IP de la interfaz si cambió
            ifaces = zbx_req("hostinterface.get", {"hostids": hid}, token)
            if ifaces and ifaces[0]["ip"] != final_ip:
                try:
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
                "status": 0 if ip_addr else 1,
                "macros": [{"macro": "{$SNMP_COMMUNITY}", "value": "public"}]
            }, token)

        resumen[categoria].append(f"{visible_name}")

    for cat, items in resumen.items():
        print(f"    -> {cat}: {len(items)} equipos registrados")

    # ---------------- CONFIGURAR GRAFANA ----------------
    print("[*] 3. Habilitando plugin Zabbix y configurando Data Source en Grafana...")
    graf_req("POST", "/api/plugins/alexanderzobnin-zabbix-app/settings", {"enabled": True, "pinned": True})

    ds_payload = {
        "name": "Zabbix-Redes2",
        "type": "alexanderzobnin-zabbix-datasource",
        "access": "proxy",
        "url": "http://127.0.0.1/zabbix/api_jsonrpc.php",
        "isDefault": True,
        "jsonData": {
            "authType": "userLogin",
            "username": ZBX_USER,
            "trends": True
        },
        "secureJsonData": {
            "password": ZBX_PASS
        }
    }
    ds_list = graf_req("GET", "/api/datasources")
    ds_uid = None
    if isinstance(ds_list, list):
        for d in ds_list:
            if d.get("name") == "Zabbix-Redes2" or d.get("type") == "alexanderzobnin-zabbix-datasource":
                ds_uid = d.get("uid")
                graf_req("PUT", f"/api/datasources/{d['id']}", ds_payload)
                break
    if not ds_uid:
        created_ds = graf_req("POST", "/api/datasources", ds_payload)
        ds_uid = created_ds.get("datasource", {}).get("uid") or created_ds.get("uid")

    print(f"    [✔] Data Source Zabbix en Grafana listo (UID: {ds_uid})")

    print("[*] 4. Generando los 4 Dashboards separados en Grafana...")
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
                "schemaVersion": 38,
                "refresh": "30s",
                "panels": [
                    {
                        "id": 1,
                        "title": f"Estado ICMP Ping (Up=1 / Down=0) - {zbx_group}",
                        "type": "stat",
                        "gridPos": {"h": 7, "w": 24, "x": 0, "y": 0},
                        "datasource": {"type": "alexanderzobnin-zabbix-datasource", "uid": ds_uid},
                        "targets": [{
                            "refId": "A",
                            "queryType": "0",
                            "group": {"filter": zbx_group},
                            "host": {"filter": "/.*/"},
                            "item": {"filter": "/ICMP ping/"}
                        }],
                        "options": {
                            "colorMode": "background",
                            "graphMode": "none",
                            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}
                        },
                        "fieldConfig": {
                            "defaults": {
                                "mappings": [
                                    {"type": "value", "options": {"0": {"text": "DOWN", "color": "red"}, "1": {"text": "UP", "color": "green"}}}
                                ],
                                "thresholds": {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "green", "value": 1}]}
                            }
                        }
                    },
                    {
                        "id": 2,
                        "title": f"Tiempo de Respuesta Ping (Latencia) - {zbx_group}",
                        "type": "timeseries",
                        "gridPos": {"h": 9, "w": 12, "x": 0, "y": 7},
                        "datasource": {"type": "alexanderzobnin-zabbix-datasource", "uid": ds_uid},
                        "targets": [{
                            "refId": "A",
                            "queryType": "0",
                            "group": {"filter": zbx_group},
                            "host": {"filter": "/.*/"},
                            "item": {"filter": "/ICMP response time/"}
                        }],
                        "fieldConfig": {"defaults": {"unit": "s"}}
                    },
                    {
                        "id": 3,
                        "title": f"Pérdida de Paquetes (%) - {zbx_group}",
                        "type": "timeseries",
                        "gridPos": {"h": 9, "w": 12, "x": 12, "y": 7},
                        "datasource": {"type": "alexanderzobnin-zabbix-datasource", "uid": ds_uid},
                        "targets": [{
                            "refId": "A",
                            "queryType": "0",
                            "group": {"filter": zbx_group},
                            "host": {"filter": "/.*/"},
                            "item": {"filter": "/ICMP loss/"}
                        }],
                        "fieldConfig": {"defaults": {"unit": "percent", "min": 0, "max": 100}}
                    },
                    {
                        "id": 4,
                        "title": f"Tráfico de Red / Interfaces SNMP (Bits recibidos/enviados) - {zbx_group}",
                        "type": "timeseries",
                        "gridPos": {"h": 9, "w": 24, "x": 0, "y": 16},
                        "datasource": {"type": "alexanderzobnin-zabbix-datasource", "uid": ds_uid},
                        "targets": [{
                            "refId": "A",
                            "queryType": "0",
                            "group": {"filter": zbx_group},
                            "host": {"filter": "/.*/"},
                            "item": {"filter": "/Bits (received|sent)/"}
                        }],
                        "fieldConfig": {"defaults": {"unit": "bps"}}
                    }
                ]
            },
            "overwrite": True
        }
        r = graf_req("POST", "/api/dashboards/db", dash_json)
        print(f"    [✔] Dashboard creado: {title} -> {r.get('url', 'OK')}")

    print("\n[✔] ¡Agente de Monitoreo finalizado con éxito!")

if __name__ == "__main__":
    main()
