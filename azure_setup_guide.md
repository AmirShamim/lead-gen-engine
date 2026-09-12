# Azure Production Deployment Guide (Option 1)

This guide walks you through deploying your **Lead Generation Portal & Database** to Microsoft Azure using a small compute instance powered by your **$5,000 Azure credits**.

---

## Architecture & Monthly Cost Overview

| Azure Resource | SKU | Specs | Approx. Cost | Credits Runway ($5k) |
|---|---|---|---|---|
| **Azure App Service** (Linux) | **B1** (Basic) | 1 vCPU, 1.75 GB RAM | **~$13 / month** | ~32 Years |
| **PostgreSQL Flexible Server** | **Standard_B1ms** | 1 vCPU, 2 GB RAM, 32 GB SSD | **~$15 / month** | ~27 Years |
| **Total Combined** | | | **~$28 / month** | **~14+ Years** |

---

## Step 1: Open Azure Cloud Shell

1. Open your browser and navigate to [portal.azure.com](https://portal.azure.com).
2. In the top navigation bar, click the **Cloud Shell** icon (`>_`).
3. Select **Bash**.

---

## Step 2: Create Azure Resources in 2 Commands

Paste these commands into the Azure Cloud Shell:

### 1. Create the Resource Group
```bash
az group create --name lead-gen-rg --location eastus
```

### 2. Create Azure Database for PostgreSQL (Flexible Server)
> Configured with database password: `dycuhxdt&*5&:46rfy`
```bash
az postgres flexible-server create \
  --resource-group lead-gen-rg \
  --name lead-gen-db-$(openssl rand -hex 3) \
  --location eastus \
  --admin-user leadadmin \
  --admin-password "dycuhxdt&*5&:46rfy" \
  --sku-name Standard_B1ms \
  --tier Burstable \
  --storage-size 32 \
  --public-access 0.0.0.0
```
*Note down the server name and host returned in the output.*

### 3. Create the App Service Plan & Web App
```bash
az appservice plan create \
  --resource-group lead-gen-rg \
  --name lead-gen-plan \
  --is-linux \
  --sku B1

az webapp create \
  --resource-group lead-gen-rg \
  --plan lead-gen-plan \
  --name lead-gen-portal-$(openssl rand -hex 3) \
  --runtime "PYTHON:3.11"
```

---

## Step 3: Configure Environment Variables in App Service

Set your database connection string and portal authentication credentials:

```bash
az webapp config appsettings set \
  --resource-group lead-gen-rg \
  --name <your-webapp-name> \
  --settings \
    DATABASE_URL="postgresql://leadadmin:dycuhxdt&*5&:46rfy@<your-postgres-host>:5432/postgres?sslmode=require" \
    PORTAL_AUTH_USERNAME="admin" \
    PORTAL_AUTH_PASSWORD="dycuhxdt&*5&:46rfy" \
    PORTAL_SECRET_KEY="$(openssl rand -hex 32)" \
    GOOGLE_PLACES_API_KEY="<YourKey>" \
    SERPER_API_KEY="<YourKey>" \
    AZURE_OPENAI_ENDPOINT="<YourEndpoint>" \
    AZURE_OPENAI_KEY="<YourKey>"
```
*(Note: If your environment requires standard URL encoding for special characters like `&` and `:`, the encoded password is `dycuhxdt%26%2A5%26%3A46rfy`).*

---

## Step 4: Migrate Existing Leads to Azure PostgreSQL

To copy all **2,397 existing leads** and **5,320 state transitions** from your local machine to Azure PostgreSQL, run this on your local machine:

```powershell
.venv\Scripts\python migrate_to_postgres.py --url "postgresql://leadadmin:dycuhxdt&*5&:46rfy@<your-postgres-host>:5432/postgres?sslmode=require"
```

All 2,397 records and transitions will transfer in under 10 seconds!

---

## Step 5: Continuous Deployment from GitHub

1. In the Azure Portal, navigate to your App Service (`<your-webapp-name>`).
2. In the left menu, click **Deployment Center**.
3. Under **Source**, select **GitHub**.
4. Authorize your GitHub account and select:
   - Organization: `AmirShamim`
   - Repository: `lead-gen-engine`
   - Branch: `main`
5. Click **Save**.

Azure will now automatically deploy your portal on every `git push`!

---

## Step 6: Access on Mobile Phone

Open your phone's browser (Safari or Chrome) and navigate to:
```
https://<your-webapp-name>.azurewebsites.net/queue
```
1. Log in with your credentials (`admin` / `dycuhxdt&*5&:46rfy`).
2. Tap **"Open LinkedIn Profile ↗"** -> opens native LinkedIn app.
3. Tap **"✓ Sent Blank (Rec.)"** -> marks lead as sent, zero bot pasting footprint.
4. Done for the day in 6 minutes!
