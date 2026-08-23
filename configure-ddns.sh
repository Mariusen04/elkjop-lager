#!/bin/sh
set -eu

APP_DIR="/home/orangepi/elkjop-lager"
ENV_FILE="$APP_DIR/.env.ddns"

printf "Domeneshop API-token: "
IFS= read -r api_token

printf "Domeneshop API-secret (skjult): "
stty -echo
trap 'stty echo 2>/dev/null || true' EXIT HUP INT TERM
IFS= read -r api_secret
stty echo
trap - EXIT HUP INT TERM
printf "\n"

if [ -z "$api_token" ] || [ -z "$api_secret" ]; then
	printf "Token og secret kan ikke være tomme.\n" >&2
	exit 1
fi

umask 077
{
	printf "DDNS_HOSTNAME=%s\n" "ankervold.no,www.ankervold.no,lager.ankervold.no"
	printf "DDNS_INTERVAL_SECONDS=%s\n" "300"
	printf "DOMENESHOP_API_TOKEN=%s\n" "$api_token"
	printf "DOMENESHOP_API_SECRET=%s\n" "$api_secret"
} > "$ENV_FILE"

unset api_token api_secret

docker rm -f domeneshop-ddns >/dev/null 2>&1 || true

docker run -d \
	--name domeneshop-ddns \
	--restart unless-stopped \
	--env-file "$ENV_FILE" \
	-v "$APP_DIR/domeneshop_ddns.py:/app/domeneshop_ddns.py:ro" \
	--entrypoint python \
	elkjop-lager:latest \
	/app/domeneshop_ddns.py >/dev/null

printf "DDNS-containeren er startet. Status:\n"
docker ps --filter name=domeneshop-ddns --format "{{.Names}} | {{.Status}}"
printf "Vent noen sekunder og kontroller med:\n"
printf "docker logs --tail 20 domeneshop-ddns\n"
