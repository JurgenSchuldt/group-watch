"""Descarga películas populares de TMDB con sinopsis en español.

Uso:
    python3 data/fetch_tmdb.py [--count 50] [--out data/tmdb-movies.json]

La API key se lee de AWS Secrets Manager (hackathon/tmdb-api-key, us-east-1)
o de la variable de entorno TMDB_API_KEY. Admite la key v3 o el token v4.
"""
import argparse
import json
import os
import urllib.parse
import urllib.request

import boto3

BASE = "https://api.themoviedb.org/3"
SECRET_ID = "hackathon/tmdb-api-key"


def get_key() -> str:
    key = os.environ.get("TMDB_API_KEY")
    if key:
        return key.strip()
    sm = boto3.client("secretsmanager", region_name="us-east-1")
    return sm.get_secret_value(SecretId=SECRET_ID)["SecretString"].strip()


def call(path: str, key: str, **params) -> dict:
    headers = {"Accept": "application/json"}
    # Los tokens v4 son JWT (empiezan por "eyJ"); las keys v3 son hexadecimales.
    if key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {key}"
    else:
        params["api_key"] = key
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=50)
    ap.add_argument("--out", default="data/tmdb-movies.json")
    args = ap.parse_args()

    key = get_key()
    genres = {
        g["id"]: g["name"]
        for g in call("/genre/movie/list", key, language="es-ES")["genres"]
    }

    movies, page = [], 1
    while len(movies) < args.count:
        data = call("/movie/popular", key, language="es-ES", page=page)
        for m in data["results"]:
            if not m.get("overview"):  # solo películas con sinopsis en español
                continue
            movies.append({
                "id": m["id"],
                "titulo": m["title"],
                "titulo_original": m["original_title"],
                "fecha_estreno": m.get("release_date"),
                "generos": [genres.get(g, str(g)) for g in m["genre_ids"]],
                "valoracion": m["vote_average"],
                "sinopsis": m["overview"],
            })
        if page >= data["total_pages"]:
            break
        page += 1

    movies = movies[: args.count]
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(movies, f, ensure_ascii=False, indent=2)
    print(f"{len(movies)} películas guardadas en {args.out}")


if __name__ == "__main__":
    main()
