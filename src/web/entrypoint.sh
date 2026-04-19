#!/bin/sh
# Generates config.js from environment variables at container startup,
# then hands off to nginx.

API_URL="${MARKET_INTELLIGENCE_API_URL:-http://localhost:8000/api}"

cat > /usr/share/nginx/html/config.js <<EOF
window.MARKET_INTELLIGENCE_CONFIG = {
  apiBase: "${API_URL}"
};
EOF

echo "Dashboard config: apiBase=${API_URL}"
exec nginx -g "daemon off;"
