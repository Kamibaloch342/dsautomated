#!/usr/bin/env bash
# =============================================================================
# EKUIPT / EQUIPT - Production Deployment Script for Hostinger VPS (Ubuntu/Debian)
# =============================================================================
set -e

echo "======================================================================"
echo "🚀 EKUIPT Hostinger VPS Automated Setup & Deployment"
echo "======================================================================"

# 1. Check for root privileges
if [ "$EUID" -ne 0 ]; then
  echo "⚠️  Please run with sudo: sudo bash deploy_hostinger.sh"
  exit 1
fi

# 2. Update packages and install dependencies
echo "📦 1/5 Updating system packages..."
apt-get update -y
apt-get install -y curl git ufw gzip build-essential nginx certbot python3-certbot-nginx

# 3. Check / Install Docker & Docker Compose if missing
if ! command -v docker &> /dev/null; then
    echo "🐳 2/5 Installing Docker Engine..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
    rm get-docker.sh
    systemctl enable docker
    systemctl start docker
fi

# 4. Prepare Environment Configuration
echo "⚙️  3/5 Checking environment configuration..."
if [ ! -f .env ]; then
    if [ -f .env.production.example ]; then
        cp .env.production.example .env
        echo "📄 Created .env from .env.production.example. Please review and update values."
    fi
fi

# 5. Build and Start Container
echo "🏗️  4/5 Building and launching EKUIPT application via Docker Compose..."
if docker compose version &> /dev/null; then
    docker compose up -d --build
else
    docker-compose up -d --build
fi

echo "======================================================================"
echo "✅ Application is running on port 5001!"
echo "======================================================================"
echo ""
echo "To attach a domain and configure HTTPS/SSL with Nginx:"
echo "1. Point your domain (A record) to this server IP."
echo "2. Create /etc/nginx/sites-available/equipt:"
echo "   server {"
echo "       server_name yourdomain.com www.yourdomain.com;"
echo "       location / {"
echo "           proxy_pass http://127.0.0.1:5001;"
echo "           proxy_set_header Host \$host;"
echo "           proxy_set_header X-Real-IP \$remote_addr;"
echo "           proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;"
echo "           proxy_set_header X-Forwarded-Proto \$scheme;"
echo "       }"
echo "   }"
echo "3. Run: ln -s /etc/nginx/sites-available/equipt /etc/nginx/sites-enabled/"
echo "4. Run: systemctl restart nginx"
echo "5. Run: certbot --nginx -d yourdomain.com -d www.yourdomain.com"
echo "======================================================================"
