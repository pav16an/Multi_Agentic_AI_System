# Deployment Guide

## Best Free Platform For This Project

For this exact project structure, **Render** is the best free option.

Why:

- Your repo already has separate Dockerfiles for the API and Streamlit app.
- Render supports **free Docker web services**.
- You can deploy both services from the same GitHub repo.
- You do **not** need to rewrite the project into a single-file Streamlit app.

Important free-tier note:

- Render free services can **sleep after 15 minutes of inactivity**.
- The first request after sleep can take around a minute to wake up.
- This is fine for demos, student projects, and portfolio deployments.

## Files Added For You

- `render.yaml`: lets Render create both services from one repo.
- This guide: explains the exact steps.

## Before You Deploy

1. Push this project to a GitHub repository.
2. Keep the repo root as `Multi_Agentic_AI_System`.
3. Make sure these files stay in the root:
   - `render.yaml`
   - `Dockerfile.api`
   - `Dockerfile.app`
   - `requirements.txt`

## Recommended Deployment Flow

### Option A: Easiest Way Using `render.yaml`

1. Create an account at Render.
2. Click **New +**.
3. Choose **Blueprint**.
4. Connect your GitHub account.
5. Select this repository.
6. Render will detect `render.yaml`.
7. Review the two services it creates:
   - `multi-agentic-ai-api`
   - `multi-agentic-ai-app`
8. Click **Apply** or **Create Blueprint**.

Render will build both Docker services automatically.

## What Each Service Does

### `multi-agentic-ai-api`

- Runs FastAPI from `Dockerfile.api`
- Public URL example:
  - `https://multi-agentic-ai-api.onrender.com`
- Health check:
  - `/health`

### `multi-agentic-ai-app`

- Runs Streamlit from `Dockerfile.app`
- Public URL example:
  - `https://multi-agentic-ai-app.onrender.com`
- This is the URL you will share with users.

## Environment Variables

The included `render.yaml` already sets the essentials:

- `BACKEND_URL` is wired automatically from the app to the API using Render internal networking.
- `REDIS_URL` is left empty so you do not need a third paid/free service.
- `ENABLE_CLEANUP_ENDPOINT=false` keeps cleanup disabled in production.

Optional manual environment variables:

- `APP_AUTH_TOKEN`
  - Add the same value to both services only if you want to protect API access.
- `CORS_ORIGINS`
  - Usually not required for the current Streamlit-to-FastAPI setup because Streamlit talks to the API server-side.

## Step-By-Step After Deployment

1. Open the API service logs first.
2. Confirm `/health` returns healthy.
3. Open the Streamlit app URL.
4. Test:
   - document upload
   - provider selection
   - model selection
   - text-to-SQL flow

## If Build Fails

Check these common causes:

### 1. Large AI dependencies

This project uses:

- `sentence-transformers`
- `faiss-cpu`
- `langchain`

Free builds can take longer than small apps. Wait for the first build to finish.

### 2. Missing provider API key

This app expects the user to enter provider keys at runtime:

- OpenAI
- Groq
- Anthropic

So deployment can succeed even without storing those keys in Render.

### 3. Cold starts

If the app seems slow on first open, it is probably waking from sleep.

## If You Want A More Beginner-Friendly But Less Suitable Option

You could use **Streamlit Community Cloud**, but it is a worse fit here because:

- it is ideal for a single Streamlit app
- your project has a separate FastAPI backend
- you would likely need to refactor the architecture

So for this repo, Render is the cleaner choice.

## Deployment Checklist

1. Push repo to GitHub.
2. Sign in to Render.
3. Create a new Blueprint deployment.
4. Select this repo.
5. Let Render create both services from `render.yaml`.
6. Wait for both builds to finish.
7. Open the Streamlit service URL.
8. Test one document flow end to end.

## Nice Next Improvements

After the first successful deploy, consider these:

1. Add a custom domain.
2. Add the same `APP_AUTH_TOKEN` to both services for extra protection.
3. Add a small sample CSV and demo document for showcase testing.
4. Add screenshots to the README for your portfolio.
