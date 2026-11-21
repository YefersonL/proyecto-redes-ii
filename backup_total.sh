#!/bin/bash
REPO_DIR="/opt/proyecto-redes-ii"
source "$REPO_DIR/.env"
FECHA=$(date +"%Y-%m-%d %H:%M:%S")
ip route replace 192.168.10.0/24 via 192.168.1.253 2>/dev/null
ip route replace 192.168.20.0/24 via 192.168.1.253 2>/dev/null
ip route replace 192.168.30.0/24 via 192.168.1.253 2>/dev/null
ip route replace 192.168.40.0/24 via 192.168.1.253 2>/dev/null

mkdir -p "$REPO_DIR/2_configs_red/routers_switches"
mkdir -p "$REPO_DIR/2_configs_red/servidores_y_vpcs"

echo "[*] 1. Extrayendo NVRAM, startup-configs, VLANs y VPCs desde PNETLab ($PNET_IP)..."
ssh "root@${PNET_IP}" "/usr/local/bin/extraer_lab.py"
scp -q "root@${PNET_IP}:${PNET_LAB_PATH}" "$REPO_DIR/1_topologia_pnetlab/Proyecto.unl"
scp -q -r "root@${PNET_IP}:/tmp/export_lab/routers_switches/*" "$REPO_DIR/2_configs_red/routers_switches/" 2>/dev/null
scp -q -r "root@${PNET_IP}:/tmp/export_lab/servidores_y_vpcs/*" "$REPO_DIR/2_configs_red/servidores_y_vpcs/" 2>/dev/null

# También extraer cualquier bloque <config> que ya estuviera dentro de Proyecto.unl

echo "[*] 2. Respaldando Dashboards y BD de Grafana..."
for uid in $(curl -s -u "$GRAFANA_AUTH" "$GRAFANA_URL/api/search?type=dash-db" | jq -r '.[].uid' 2>/dev/null); do
    if [ "$uid" != "null" ] && [ -n "$uid" ]; then
        curl -s -u "$GRAFANA_AUTH" "$GRAFANA_URL/api/dashboards/uid/$uid" | jq '.dashboard' > "$REPO_DIR/4_grafana/dashboard_${uid}.json"
    fi
done
cp /var/lib/grafana/grafana.db "$REPO_DIR/4_grafana/grafana.db" 2>/dev/null

echo "[*] 3. Respaldando Hosts, Plantillas y BD de Zabbix..."
curl -s -X POST -H "Content-Type: application/json-rpc" -H "Authorization: Bearer $ZABBIX_TOKEN" -d '{
    "jsonrpc": "2.0",
    "method": "configuration.export",
    "params": {
        "options": { "groups": ["2"] },
        "format": "json"
    },
    "id": 1
}' "$ZABBIX_URL" | jq -r '.result' > "$REPO_DIR/3_zabbix/zabbix_export.json" 2>/dev/null

sed 's/^DBPassword=.*/DBPassword=PASSWORD_OCULTO/' /etc/zabbix/zabbix_server.conf > "$REPO_DIR/3_zabbix/zabbix_server.conf" 2>/dev/null

mysqldump -u "$ZABBIX_DB_USER" -p"$ZABBIX_DB_PASS" --no-tablespaces \
  --ignore-table=zabbix.history --ignore-table=zabbix.history_uint \
  --ignore-table=zabbix.history_str --ignore-table=zabbix.history_text \
  --ignore-table=zabbix.history_log --ignore-table=zabbix.trends \
  --ignore-table=zabbix.trends_uint \
  zabbix > "$REPO_DIR/3_zabbix/zabbix_config_backup.sql" 2>/dev/null

echo "[*] 4. Exportando flujos de n8n (si está activo)..."
if docker ps 2>/dev/null | grep -q n8n; then
    docker exec -u node $(docker ps -qf "name=n8n") n8n export:workflow --all --output=/tmp/workflows.json
    docker cp $(docker ps -qf "name=n8n"):/tmp/workflows.json "$REPO_DIR/5_n8n/workflows_n8n.json"
    sed -i -E 's/[0-9]{8,10}:[a-zA-Z0-9_-]{35}/TELEGRAM_BOT_TOKEN_OCULTO/g' "$REPO_DIR/5_n8n/workflows_n8n.json"
fi

echo "[*] 5. Subiendo cambios a GitHub..."
cd "$REPO_DIR"
git add .
if ! git diff --cached --quiet; then
    git commit -m "Backup automático del proyecto - $FECHA"
    git push -u origin main
    echo "[✔] Respaldo subido exitosamente a GitHub."
else
    echo "[i] Sin cambios nuevos desde el último respaldo."
fi
