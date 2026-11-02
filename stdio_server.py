"""Uso LOCAL (opcional): servidor MCP por stdio para Claude Desktop / Claude Code en tu computador.

Si defines SUPABASE_URL y SUPABASE_SERVICE_KEY en .env, usa los mismos tokens que la nube.
La versión recomendada es la remota (app.py en Vercel).
"""
from ml_tools import mcp

if __name__ == "__main__":
    mcp.run()
