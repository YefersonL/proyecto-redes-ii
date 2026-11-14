#!/bin/bash
REPO_DIR="/opt/proyecto-redes-ii"
source "$REPO_DIR/.env"
FECHA=$(date +"%Y-%m-%d %H:%M:%S")

echo "[*] 1. Respaldando Proyecto.unl desde PNETLab ($PNET_IP)..."
scp -q "root@${PNET_IP}:${PNET_LAB_PATH}" "$REPO_DIR/1_topologia_pnetlab/Proyecto.unl"

echo "[*] 2. Extrayendo startup-configs de los routers desde Proyecto.unl..."
if [ -f "$REPO_DIR/1_topologia_pnetlab/Proyecto.unl" ]; then
    python3 -c '
import xml.etree.ElementTree as ET, base64
tree = ET.parse("/opt/proyecto-redes-ii/1_topologia_pnetlab/Proyecto.unl")
for node in tree.findall(".//node"):
    name = node.get("name", "node").strip()
    cfg = node.find("config")
    if cfg is not None and cfg.text:
        raw = base64.b64decode(cfg.text).decode("utf-8", errors="ignore")
        with open(f"/opt/proyecto-redes-ii/2_configs_red/{name}.cfg", "w") as f:
            f.write(raw)
' 2>/dev/null
fi

if [ -f /opt/network-backups/backup_routers.py ]; then
    python3 /opt/network-backups/backup_routers.py
    cp /opt/network-backups/configs/*.cfg "$REPO_DIR/2_configs_red/" 2>/dev/null
fi

echo "[*] 3. Respaldando Dashboards y BD de Grafana..."
# Exportar JSON de cada dashboard usando usuario:contraseña (o admin:admin de respaldo)
for uid in $(curl -s -u "$GRAFANA_AUTH" "$GRAFANA_URL/api/search?type=dash-db" | jq -r '.[].uid' 2>/dev/null); do
    if [ "$uid" != "null" ] && [ -n "$uid" ]; then
        curl -s -u "$GRAFANA_AUTH" "$GRAFANA_URL/api/dashboards/uid/$uid" | jq '.dashboard' > "$REPO_DIR/4_grafana/dashboard_${uid}.json"
    fi
done
# Copia de seguridad directa de la base de datos SQLite de Grafana
cp /var/lib/grafana/grafana.db "$REPO_DIR/4_grafana/grafana.db" 2>/dev/null

echo "[*] 4. Respaldando Hosts, Plantillas y BD de Zabbix..."
# Exportar grupos y configuraciones vía API de Zabbix 7.0 con tu token
curl -s -X POST -H "Content-Type: application/json-rpc" -H "Authorization: Bearer $ZABBIX_TOKEN" -d '{
    "jsonrpc": "2.0",
    "method": "configuration.export",
    "params": {
        "options": { "groups": ["2"] },
        "format": "json"
    },
    "id": 1
}' "$ZABBIX_URL" | jq -r '.result' > "$REPO_DIR/3_zabbix/zabbix_export.json" 2>/dev/null

# Respaldo de zabbix_server.conf (ocultando el password para GitHub público)
sed 's/^DBPassword=.*/DBPassword=PASSWORD_OCULTO/' /etc/zabbix/zabbix_server.conf > "$REPO_DIR/3_zabbix/zabbix_server.conf" 2>/dev/null

# Dump de la base de datos MySQL sin tablas pesadas de historial
mysqldump -u "$ZABBIX_DB_USER" -p"$ZABBIX_DB_PASS" --no-tablespaces \
  --ignore-table=zabbix.history --ignore-table=zabbix.history_uint \
  --ignore-table=zabbix.history_str --ignore-table=zabbix.history_text \
  --ignore-table=zabbix.history_log --ignore-table=zabbix.trends \
  --ignore-table=zabbix.trends_uint \
  zabbix > "$REPO_DIR/3_zabbix/zabbix_config_backup.sql" 2>/dev/null

echo "[*] 5. Exportando flujos de n8n (si está activo)..."
if docker ps 2>/dev/null | grep -q n8n; then
    docker exec -u node $(docker ps -qf "name=n8n") n8n export:workflow --all --output=/tmp/workflows.json
    docker cp $(docker ps -qf "name=n8n"):/tmp/workflows.json "$REPO_DIR/5_n8n/workflows_n8n.json"
    sed -i -E 's/[0-9]{8,10}:[a-zA-Z0-9_-]{35}/TELEGRAM_BOT_TOKEN_OCULTO/g' "$REPO_DIR/5_n8n/workflows_n8n.json"
fi

echo "[*] 6. Guardando cambios en Git..."
cd "$REPO_DIR"
git add .
if ! git diff --cached --quiet; then
    git commit -m "Backup automático del proyecto - $FECHA"
    if git remote | grep -q origin; then
        git push -u origin main
        echo "[✔] Respaldo subido exitosamente a GitHub."
    else
        echo "[✔] Commit guardado localmente (falta vincular git remote origin)."
    fi
else
    echo "[i] Sin cambios nuevos desde el último respaldo."
fi
