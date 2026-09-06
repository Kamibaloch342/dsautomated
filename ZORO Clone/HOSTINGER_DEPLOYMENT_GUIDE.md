# 🚀 Complete Hostinger Deployment Guide for EKUIPT

This guide provides step-by-step instructions to deploy the complete **EKUIPT** e-commerce platform on **Hostinger VPS** (recommended) or any Ubuntu/Debian Linux server.

---

## 🏗️ Architecture

```
Internet (HTTPS) 
   │
   ▼
[Nginx Reverse Proxy + Let's Encrypt SSL]  (Port 80/443)
   │
   ▼
[Gunicorn + Flask App]                      (Port 5001)
   ├── [React Vite SPA] (Static build served from frontend/dist)
   ├── [SQLite Operations DB] (zoro_clone.db: Users, Orders, Net30, Cases)
   └── [SQLite FTS5 Catalog Index] (catalog_enrichment.db & catalog_index.db)
```

---

## ⚡ Method 1: Hostinger VPS Deployment (Fastest & Recommended)

### Step 1: Connect to your Hostinger VPS via SSH
Open your terminal and run:
```bash
ssh root@YOUR_SERVER_IP
```

### Step 2: Upload or Clone the Project
You can clone your GitHub repository or upload the project folder:
```bash
git clone <your-repo-url> equipt
cd equipt
```

### Step 3: Configure Environment Variables
Copy the production environment template and set your secure keys:
```bash
cp .env.production.example .env
nano .env
```
Key variables to customize in `.env`:
- `JWT_SECRET`: A long random 32+ character string.
- `SITE_URL`: Your production domain (e.g., `https://yourdomain.com`).
- `RESEND_API_KEY`: API key from [resend.com](https://resend.com) for sending confirmation, tracking & password reset emails.
- `ADMIN_EMAIL` & `ADMIN_PASSWORD`: Your initial store administrator login.

### Step 4: Run the Automated Deployment Script
```bash
chmod +x deploy_hostinger.sh
sudo bash deploy_hostinger.sh
```
This script will automatically:
1. Install Docker, Docker Compose, Nginx, and Certbot.
2. Reassemble the compressed catalog database parts (`catalog_enrichment.db.gz.part_*`).
3. Build the search index (`catalog_index.db`).
4. Build the Vite React frontend.
5. Launch the Gunicorn application container on port `5001`.

---

## 🌐 Setting Up Your Custom Domain & Free SSL (Let's Encrypt)

### 1. Point Your Domain DNS to Hostinger
In your Domain DNS Manager (Hostinger DNS / Cloudflare / GoDaddy), add two **A Records**:
- Host `@` ➔ Points to `YOUR_SERVER_IP`
- Host `www` ➔ Points to `YOUR_SERVER_IP`

### 2. Configure Nginx
Create an Nginx configuration file:
```bash
sudo nano /etc/nginx/sites-available/equipt
```

Paste the following configuration (replace `yourdomain.com` with your actual domain):
```nginx
server {
    listen 80;
    server_name yourdomain.com www.yourdomain.com;

    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:5001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable the site and test Nginx:
```bash
sudo ln -s /etc/nginx/sites-available/equipt /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

### 3. Generate Free SSL Certificate with Certbot
```bash
sudo certbot --nginx -d yourdomain.com -d www.yourdomain.com
```
Follow the on-screen prompt to enter your email. Certbot will automatically configure HTTPS and auto-renewal.

---

## 🛡️ User Authentication & Admin Panel Verification

1. **User Sign Up:** Visit `https://yourdomain.com/register`. Users can verify instantly with their 6-digit email code or email confirmation link.
2. **Forgot Password:** Available at `https://yourdomain.com/forgot-password` with 6-digit recovery codes.
3. **Admin Dashboard:** Log in with `ADMIN_EMAIL` and `ADMIN_PASSWORD` at `https://yourdomain.com/login`, then navigate to `https://yourdomain.com/admin` to manage:
   - Order fulfillment & status transitions (with automated email dispatches)
   - Net 30 corporate applications & credit limits
   - Support cases & customer messaging
   - Product catalog overrides & pricing

---

## 🔄 Useful Management Commands

- **Check container status:** `docker compose ps`
- **View live server logs:** `docker compose logs -f equipt-app`
- **Restart application:** `docker compose restart`
- **Rebuild after updates:** `git pull && docker compose up -d --build`
