Quick ways to host this FastAPI app for a small test:

1) Replit (easiest GUI)
- Create a new Replit, import from GitHub or upload files.
- Ensure `requirements.txt` is present.
- Create a `run` command or use the Shell to run: `uvicorn app:app --host 0.0.0.0 --port 3000`.
- Enable "Always on" for persistent hosting (paid).

2) Railway / Fly.io / Render (quick Git-based deploy)
- Connect your GitHub repo, set build command to `pip install -r requirements.txt`.
- Set start command to `uvicorn app:app --host 0.0.0.0 --port $PORT`.

3) Vercel (serverless via Python runtimes) or Docker deploy
- Build a small Dockerfile and deploy to any container host.

4) Local tunnel for quick sharing
- Use `ngrok http 8000` after starting locally to share a public URL.

See the project files for `requirements.txt` and a `Procfile` for Heroku-like hosts.
